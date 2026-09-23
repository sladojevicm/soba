"""Upload hardening on top of ``api.jobs.archive.validate_and_extract``.

Everything here is pure stdlib + numpy-free byte inspection so it runs on a
box without OpenCV / imageio / Pillow (the depth check parses the PNG IHDR
chunk by hand instead of decoding pixels).

Limits (``UploadLimits.from_env``; MiB values, 0 = library default):

  SOBA_MAX_EXTRACTED_MB        total uncompressed bytes            (8192)
  SOBA_MAX_MEMBER_MB           any single archive member           (512)
  SOBA_MAX_MEMBERS             archive member count                (200000)
  SOBA_MAX_DECOMPRESSION_RATIO uncompressed / compressed, total and
                               per member above 1 MiB              (200)
  SOBA_MAX_FRAMES              frame dirs under frames/, the manifest's
                               frame_count, or TUM rgb.txt/depth.txt
                               lines                               (5000)
  SOBA_MAX_FRAME_PIXELS        depth width * height                (4096*4096)
  SOBA_DEPTH_SAMPLE            depth frames inspected per upload   (8)

What is checked, in order, on the archive listing BEFORE any byte is written:
member path (absolute / ``..`` / drive / backslash / NUL), zip symlink
entries, nested archives by name, per-member size, running total, running
compression ratio; then, at write time, that the resolved target is still
inside the extraction dir (a second, filesystem-level check on top of the
path parse). After extraction, for a PerceptionBundle: ``manifest.json`` is a
JSON object accepted by ``perception.bundle.Manifest.from_dict``, the frame
count is capped, and a sample of ``frames/NNNNN/depth.png`` files must be
16-bit greyscale PNGs of bounded resolution.
"""

from __future__ import annotations

import json
import os
import re
import stat
import struct
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from perception.bundle import Manifest

from ..jobs.upload_errors import (
    BadDepthDtype,
    BadManifest,
    DecompressionRatio,
    FrameTooLarge,
    InvalidUpload,
    MemberTooLarge,
    NestedArchive,
    TooManyFrames,
    UploadTooLarge,
)

_MIB = 1024 ** 2
_RATIO_MIN_MEMBER = 1 * _MIB          # per-member ratio only judged above this
_NESTED_SUFFIXES = (".zip", ".tar", ".tgz", ".tar.gz", ".gz", ".7z", ".rar", ".bz2", ".xz")
_FRAME_DIR_RE = re.compile(r"^[0-9]{5}$")
_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        v = int(float(raw))
    except ValueError:
        return default
    return v if v > 0 else default


@dataclass(frozen=True)
class UploadLimits:
    max_extracted_bytes: int = 8 * 1024 ** 3
    max_member_bytes: int = 512 * _MIB
    max_members: int = 200_000
    max_ratio: float = 200.0
    max_frames: int = 5000
    max_frame_pixels: int = 4096 * 4096
    depth_sample: int = 8

    @classmethod
    def from_env(cls) -> UploadLimits:
        d = cls()
        return cls(
            max_extracted_bytes=_env_int("SOBA_MAX_EXTRACTED_MB", 0) * _MIB or d.max_extracted_bytes,
            max_member_bytes=_env_int("SOBA_MAX_MEMBER_MB", 0) * _MIB or d.max_member_bytes,
            max_members=_env_int("SOBA_MAX_MEMBERS", d.max_members),
            max_ratio=float(_env_int("SOBA_MAX_DECOMPRESSION_RATIO", int(d.max_ratio))),
            max_frames=_env_int("SOBA_MAX_FRAMES", d.max_frames),
            max_frame_pixels=_env_int("SOBA_MAX_FRAME_PIXELS", d.max_frame_pixels),
            depth_sample=_env_int("SOBA_DEPTH_SAMPLE", d.depth_sample),
        )

    def replace(self, **changes) -> UploadLimits:
        return replace(self, **changes)


# --- listing-time checks ------------------------------------------------------
class Budget:
    """Running totals over an archive listing; raises on the first breach."""

    def __init__(self, limits: UploadLimits, archive_bytes: int) -> None:
        self.limits = limits
        self.archive_bytes = max(int(archive_bytes), 1)
        self.total = 0
        self.count = 0

    def add(self, name: str, size: int, compressed: int | None = None) -> None:
        lim = self.limits
        if size > lim.max_member_bytes:
            raise MemberTooLarge(
                f"archive member {name!r} is {size // _MIB} MiB; the cap is "
                f"{lim.max_member_bytes // _MIB} MiB")
        self.total += size
        self.count += 1
        if self.total > lim.max_extracted_bytes:
            raise UploadTooLarge(
                f"archive expands to more than {lim.max_extracted_bytes // _MIB} MiB")
        if self.count > lim.max_members:
            raise UploadTooLarge(f"archive has more than {lim.max_members} members")
        if compressed is not None and size > _RATIO_MIN_MEMBER \
                and size / max(compressed, 1) > lim.max_ratio:
            raise DecompressionRatio(
                f"archive member {name!r} inflates {size / max(compressed, 1):.0f}x; "
                f"the cap is {lim.max_ratio:.0f}x")
        if self.total / self.archive_bytes > lim.max_ratio:
            raise DecompressionRatio(
                f"archive inflates more than {lim.max_ratio:.0f}x its size")


def check_member_name(name: str) -> None:
    """Reject nested archives by name (the layout never needs one)."""
    low = name.lower()
    if low.endswith(_NESTED_SUFFIXES):
        raise NestedArchive(f"nested archive {name!r} is not allowed")


def check_zip_member(info: zipfile.ZipInfo) -> None:
    """Zip entries that unix tools would restore as symlinks are rejected."""
    mode = info.external_attr >> 16
    fmt = stat.S_IFMT(mode)
    if fmt == stat.S_IFLNK:
        raise InvalidUpload(
            f"archive member {info.filename!r} is a symlink (links are rejected)")
    # zipfile.writestr and Windows zips leave the type bits at 0: that is a
    # plain file. Only an explicit non-file, non-dir type is rejected.
    if fmt and fmt not in (stat.S_IFREG, stat.S_IFDIR):
        raise InvalidUpload(
            f"archive member {info.filename!r} is not a regular file")


def check_target_inside(dest: Path, rel: PurePosixPath) -> Path:
    """The path we are about to write, verified against the real filesystem.

    ``_safe_relpath`` already parsed the name; this resolves the target (so a
    directory that somehow became a symlink cannot redirect the write) and
    requires it to stay under ``dest``.
    """
    base = dest.resolve()
    target = (dest / rel)
    resolved = target.resolve()
    if not resolved.is_relative_to(base) or resolved == base:
        raise InvalidUpload(f"archive member {str(rel)!r} resolves outside the extraction dir")
    parent = target.parent
    if parent.exists() and (parent.is_symlink() or not parent.resolve().is_relative_to(base)):
        raise InvalidUpload(f"archive member {str(rel)!r} would be written through a link")
    return target


# --- bundle content checks -----------------------------------------------------
def png_ihdr(data: bytes) -> tuple[int, int, int, int]:
    """(width, height, bit_depth, colour_type) from a PNG's IHDR, or ValueError."""
    if len(data) < 8 + 8 + 13 or data[:8] != _PNG_SIG:
        raise ValueError("not a PNG")
    length, ctype = struct.unpack(">I4s", data[8:16])
    if ctype != b"IHDR" or length != 13:
        raise ValueError("PNG without a leading IHDR chunk")
    w, h, bit_depth, colour_type = struct.unpack(">IIBB", data[16:26])
    return w, h, bit_depth, colour_type


def check_depth_png(path: Path, limits: UploadLimits) -> None:
    try:
        with path.open("rb") as fh:
            head = fh.read(64)
    except OSError as exc:
        raise InvalidUpload(f"cannot read {path.name}: {exc}") from exc
    try:
        w, h, bit_depth, colour_type = png_ihdr(head)
    except ValueError as exc:
        raise BadDepthDtype(
            f"{path.parent.name}/depth.png: {exc}; depth must be a 16-bit "
            "greyscale PNG (uint16 millimetres)") from exc
    if bit_depth != 16 or colour_type != 0:
        raise BadDepthDtype(
            f"{path.parent.name}/depth.png is {bit_depth}-bit colour type "
            f"{colour_type}; depth must be a 16-bit greyscale PNG (uint16 millimetres)")
    if w * h > limits.max_frame_pixels or w == 0 or h == 0:
        raise FrameTooLarge(
            f"{path.parent.name}/depth.png is {w}x{h}; the cap is "
            f"{limits.max_frame_pixels} pixels")


def _sample(items: list, n: int) -> list:
    """``n`` items spread evenly, always including the first and the last."""
    if n <= 0 or len(items) <= n:
        return list(items)
    if n == 1:
        return [items[0]]
    return [items[round(i * (len(items) - 1) / (n - 1))] for i in range(n)]


def validate_manifest(root: Path, limits: UploadLimits) -> Manifest:
    """manifest.json through ``Manifest.from_dict`` plus the frame-count cap.

    Runs BEFORE ``PerceptionBundle.open`` so a broken manifest is reported as
    ``bad_manifest`` rather than the generic ``invalid_upload``.
    """
    mpath = root / "manifest.json"
    try:
        raw = json.loads(mpath.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BadManifest(f"manifest.json is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise BadManifest("manifest.json must be a JSON object")
    try:
        manifest = Manifest.from_dict(raw)
    except (KeyError, TypeError, ValueError) as exc:
        raise BadManifest(f"manifest.json rejected by Manifest.from_dict: {exc!r}") from exc
    if manifest.frame_count < 0 or manifest.frame_count > limits.max_frames:
        raise TooManyFrames(
            f"manifest frame_count {manifest.frame_count} exceeds the cap of "
            f"{limits.max_frames} frames")
    if not (manifest.fps > 0):
        raise BadManifest(f"manifest fps must be > 0, got {manifest.fps}")
    return manifest


def validate_frames(root: Path, limits: UploadLimits) -> int:
    """Frame-dir count cap, no symlinked frame dirs, depth sample check."""
    frames_dir = root / "frames"
    frame_dirs = sorted(p for p in frames_dir.iterdir()
                        if p.is_dir() and _FRAME_DIR_RE.match(p.name))
    if len(frame_dirs) > limits.max_frames:
        raise TooManyFrames(
            f"bundle has {len(frame_dirs)} frame dirs; the cap is {limits.max_frames}")
    for d in frame_dirs:
        if d.is_symlink():
            raise InvalidUpload(f"frames/{d.name} is a symlink")
    for d in _sample(frame_dirs, limits.depth_sample):
        depth = d / "depth.png"
        if depth.is_symlink() or not depth.is_file():
            raise BadDepthDtype(f"frames/{d.name}/depth.png is missing")
        check_depth_png(depth, limits)
    return len(frame_dirs)


def validate_bundle_contents(root: Path, limits: UploadLimits) -> Manifest:
    """Everything above, for a bundle ``PerceptionBundle.open`` already accepted."""
    manifest = validate_manifest(root, limits)
    validate_frames(root, limits)
    return manifest


def check_tum_contents(root: Path, limits: UploadLimits) -> None:
    """A TUM sequence is converted frame by frame; cap what the worker will decode."""
    for name in ("rgb.txt", "depth.txt"):
        try:
            with (root / name).open("r", errors="replace") as fh:
                n = sum(1 for line in fh if line.strip() and not line.lstrip().startswith("#"))
        except OSError as exc:
            raise InvalidUpload(f"cannot read {name}: {exc}") from exc
        if n > limits.max_frames:
            raise TooManyFrames(
                f"{name} lists {n} frames; the cap is {limits.max_frames} frames")


# --- streaming body cap --------------------------------------------------------
Receive = Callable[[], Awaitable[dict]]


def capped_receive(receive: Receive, max_bytes: int) -> Receive:
    """Wrap an ASGI ``receive`` so the request body cannot exceed ``max_bytes``.

    The multipart parser pulls chunks through this as it goes, so an
    oversized (or chunked, Content-Length-less) upload is aborted the moment
    the running count crosses the cap, not after the whole body was spooled.
    """
    seen = 0

    async def _recv() -> dict:
        nonlocal seen
        msg = await receive()
        if msg.get("type") == "http.request":
            seen += len(msg.get("body") or b"")
            if seen > max_bytes:
                raise UploadTooLarge(f"upload exceeds {max_bytes // _MIB} MiB")
        return msg

    return _recv
