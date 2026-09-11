"""Upload archive validation + extraction.

``validate_and_extract`` is the ONE entry point every upload goes through;
security-agent (phase B) extends it (MIME/magic policy, manifest field
checks, frame-count and depth-dtype checks). What it guarantees today:

* format sniffed from magic bytes, not the file name: zip, gzip'd tar, plain
  tar; anything else -> ``UnsupportedArchive`` (415);
* no member may escape ``dest``: absolute paths, ``..`` segments, drive
  letters and backslashes are rejected; tar symlinks / hardlinks / devices /
  fifos are rejected (zip-slip guard) -> ``InvalidUpload`` (422);
* caps on uncompressed size and member count -> ``UploadTooLarge`` (413);
* layout detection after extraction: a PerceptionBundle (``manifest.json`` +
  ``intrinsics.json``, at the archive root or inside a single top-level dir)
  is opened with ``PerceptionBundle.open`` and must hold >= 1 frame; a TUM
  sequence (``rgb.txt`` + ``depth.txt``) is accepted only when an imaging
  backend (OpenCV / imageio) is importable, because the worker converts it
  with ``TUMReader.to_bundle`` which decodes PNGs. Otherwise 422 with a
  message that says so. MP4 is never accepted (maintainer decision 1).
"""

from __future__ import annotations

import json
import os
import shutil
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from perception.bundle import PerceptionBundle

DEFAULT_MAX_EXTRACTED_BYTES = 8 * 1024 ** 3  # 8 GiB
DEFAULT_MAX_MEMBERS = 200_000
_IGNORED_TOP_LEVEL = {"__MACOSX"}


class UploadError(Exception):
    status = 400
    code = "bad_upload"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class UnsupportedArchive(UploadError):
    status = 415
    code = "unsupported_archive"


class UploadTooLarge(UploadError):
    status = 413
    code = "upload_too_large"


class InvalidUpload(UploadError):
    status = 422
    code = "invalid_upload"


@dataclass(frozen=True)
class ExtractResult:
    root: Path             # the bundle / TUM sequence directory
    format: str            # "bundle" | "tum"
    archive_format: str    # "zip" | "tar.gz" | "tar"


# --- format sniffing --------------------------------------------------------
def sniff_format(path: Path) -> str:
    with Path(path).open("rb") as fh:
        head = fh.read(4)
    if head[:4] == b"PK\x03\x04":
        return "zip"
    if head[:2] == b"\x1f\x8b":
        return "tar.gz"
    if tarfile.is_tarfile(path):
        return "tar"
    raise UnsupportedArchive(
        "upload must be a zip, tar.gz or tar archive of a PerceptionBundle "
        "or a TUM RGB-D sequence (MP4/video is not accepted)")


def ext_for(archive_format: str) -> str:
    return {"zip": "zip", "tar.gz": "tar.gz", "tar": "tar"}[archive_format]


# --- member safety ----------------------------------------------------------
def _safe_relpath(name: str) -> PurePosixPath | None:
    """Normalised member path, or None for a directory entry / empty name.

    Raises InvalidUpload for anything that could land outside ``dest``.
    """
    if "\\" in name or "\x00" in name:
        raise InvalidUpload(f"illegal character in archive member {name!r}")
    p = PurePosixPath(name)
    if p.is_absolute() or (p.parts and ":" in p.parts[0]):
        raise InvalidUpload(f"absolute path in archive: {name!r}")
    if any(part in ("..", "") for part in p.parts):
        raise InvalidUpload(f"path traversal in archive: {name!r}")
    return p if p.parts else None


def _check_budget(total: int, count: int, max_bytes: int, max_members: int) -> None:
    if total > max_bytes:
        raise UploadTooLarge(
            f"archive expands to more than {max_bytes // 1024 ** 2} MiB")
    if count > max_members:
        raise UploadTooLarge(f"archive has more than {max_members} members")


def _extract_zip(path: Path, dest: Path, max_bytes: int, max_members: int) -> None:
    try:
        with zipfile.ZipFile(path) as zf:
            total = count = 0
            plan: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
            for info in zf.infolist():
                rel = _safe_relpath(info.filename)
                if rel is None or info.is_dir():
                    continue
                total += info.file_size
                count += 1
                _check_budget(total, count, max_bytes, max_members)
                plan.append((info, rel))
            for info, rel in plan:
                target = dest / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
    except zipfile.BadZipFile as exc:
        raise InvalidUpload(f"corrupt zip: {exc}") from exc


def _extract_tar(path: Path, dest: Path, max_bytes: int, max_members: int) -> None:
    try:
        with tarfile.open(path, "r:*") as tf:
            total = count = 0
            plan: list[tuple[tarfile.TarInfo, PurePosixPath]] = []
            for m in tf:
                rel = _safe_relpath(m.name)
                if rel is None or m.isdir():
                    continue
                if not m.isreg():
                    raise InvalidUpload(
                        f"archive member {m.name!r} is not a regular file "
                        "(links and special files are rejected)")
                total += m.size
                count += 1
                _check_budget(total, count, max_bytes, max_members)
                plan.append((m, rel))
            for m, rel in plan:
                target = dest / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                src = tf.extractfile(m)
                if src is None:
                    continue
                with src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
    except tarfile.TarError as exc:
        raise InvalidUpload(f"corrupt tar: {exc}") from exc


# --- layout detection -------------------------------------------------------
def _candidates(dest: Path) -> list[Path]:
    entries = [p for p in dest.iterdir()
               if p.name not in _IGNORED_TOP_LEVEL and not p.name.startswith(".")]
    out = [dest]
    if len(entries) == 1 and entries[0].is_dir():
        out.append(entries[0])
    return out


def _is_bundle(d: Path) -> bool:
    return (d / "manifest.json").is_file() and (d / "intrinsics.json").is_file()


def _is_tum(d: Path) -> bool:
    return (d / "rgb.txt").is_file() and (d / "depth.txt").is_file()


def imaging_available() -> bool:
    """True when PerceptionBundle pixel I/O can run here (OpenCV or imageio)."""
    from perception.bundle import _imaging_backend
    try:
        _imaging_backend()
        return True
    except RuntimeError:
        return False


def _validate_bundle(root: Path) -> None:
    try:
        b = PerceptionBundle.open(root)
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
        raise InvalidUpload(f"bundle manifest/intrinsics invalid: {exc}") from exc
    if not (root / "frames").is_dir():
        raise InvalidUpload("bundle has no frames/ directory")
    if next(b.iter_frame_ids(), None) is None:
        raise InvalidUpload("bundle has no frames")


def detect_layout(dest: Path) -> tuple[Path, str]:
    for cand in _candidates(dest):
        if _is_bundle(cand):
            _validate_bundle(cand)
            return cand, "bundle"
        if _is_tum(cand):
            if not imaging_available():
                raise InvalidUpload(
                    "TUM sequence recognised, but this server cannot convert it: "
                    "TUM -> PerceptionBundle conversion decodes PNGs and needs the "
                    "'recon' extra (opencv-python or imageio) installed. Upload a "
                    "PerceptionBundle archive instead.")
            return cand, "tum"
    raise InvalidUpload(
        "archive is neither a PerceptionBundle (manifest.json + intrinsics.json + "
        "frames/) nor a TUM sequence (rgb.txt + depth.txt), at the root or in a "
        "single top-level directory")


# --- the entry point --------------------------------------------------------
def validate_and_extract(
    archive_path: Path | str,
    dest: Path | str,
    *,
    max_extracted_bytes: int = DEFAULT_MAX_EXTRACTED_BYTES,
    max_members: int = DEFAULT_MAX_MEMBERS,
) -> ExtractResult:
    """Sniff, safely extract into ``dest`` and identify the layout.

    Raises an ``UploadError`` subclass (carrying ``.status`` and ``.code``)
    on any rejection; ``dest`` may then contain a partial extraction and the
    caller removes the job dir.
    """
    archive_path, dest = Path(archive_path), Path(dest)
    fmt = sniff_format(archive_path)
    dest.mkdir(parents=True, exist_ok=True)
    if fmt == "zip":
        _extract_zip(archive_path, dest, max_extracted_bytes, max_members)
    else:
        _extract_tar(archive_path, dest, max_extracted_bytes, max_members)
    root, layout = detect_layout(dest)
    # belt and braces: the resolved root must still be inside dest
    if os.path.commonpath([root.resolve(), dest.resolve()]) != str(dest.resolve()):
        raise InvalidUpload("archive root resolved outside the extraction dir")
    return ExtractResult(root=root, format=layout, archive_format=fmt)
