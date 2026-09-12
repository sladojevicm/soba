"""Security phase B upload hardening: bombs, caps, manifest/depth checks,
symlink and absolute members, the streaming body cap, and each new 422/413
code as seen through POST /api/jobs. CPU-only, no image decoding."""

from __future__ import annotations

import io
import json
import tarfile
import zipfile

import pytest
from _fixtures import (
    DEPTH_PNG,
    make_bundle,
    make_tum_sequence,
    png_header,
    post_archive,
    tar_dir,
    zip_dir,
)
from starlette.testclient import TestClient

from api.app import create_app
from api.jobs.archive import (
    BadDepthDtype,
    BadManifest,
    DecompressionRatio,
    FrameTooLarge,
    InvalidUpload,
    MemberTooLarge,
    NestedArchive,
    TooManyFrames,
    UploadTooLarge,
    validate_and_extract,
)
from api.security.upload_validation import (
    UploadLimits,
    capped_receive,
    check_tum_contents,
    png_ihdr,
)

MIB = 1024 ** 2


def _write(tmp_path, data: bytes, name: str = "u.bin"):
    p = tmp_path / name
    p.write_bytes(data)
    return p


def _zip_with(tmp_path, extra: dict[str, bytes], frames: int = 2, **overrides) -> bytes:
    """A valid bundle zip plus/overriding the given members."""
    src = make_bundle(tmp_path / "src", frames=frames)
    for rel, data in overrides.items():
        (src / rel).write_bytes(data)
    buf = io.BytesIO(zip_dir(src))
    if extra:
        with zipfile.ZipFile(buf, "a", zipfile.ZIP_DEFLATED) as zf:
            for name, data in extra.items():
                zf.writestr(name, data)
    return buf.getvalue()


# --- limits from env -------------------------------------------------------------
def test_limits_from_env_and_defaults(monkeypatch):
    d = UploadLimits()
    assert UploadLimits.from_env() == d
    monkeypatch.setenv("SOBA_MAX_EXTRACTED_MB", "16")
    monkeypatch.setenv("SOBA_MAX_MEMBER_MB", "2")
    monkeypatch.setenv("SOBA_MAX_MEMBERS", "50")
    monkeypatch.setenv("SOBA_MAX_DECOMPRESSION_RATIO", "10")
    monkeypatch.setenv("SOBA_MAX_FRAMES", "7")
    monkeypatch.setenv("SOBA_MAX_FRAME_PIXELS", "1000")
    monkeypatch.setenv("SOBA_DEPTH_SAMPLE", "3")
    lim = UploadLimits.from_env()
    assert lim.max_extracted_bytes == 16 * MIB and lim.max_member_bytes == 2 * MIB
    assert (lim.max_members, lim.max_ratio, lim.max_frames) == (50, 10.0, 7)
    assert (lim.max_frame_pixels, lim.depth_sample) == (1000, 3)
    monkeypatch.setenv("SOBA_MAX_FRAMES", "garbage")
    monkeypatch.setenv("SOBA_MAX_MEMBERS", "-4")
    assert UploadLimits.from_env().max_frames == d.max_frames
    assert UploadLimits.from_env().max_members == d.max_members


# --- decompression bombs ---------------------------------------------------------
def test_zip_member_inflating_past_the_ratio_is_rejected(tmp_path):
    data = _zip_with(tmp_path, {"frames/00000/conf.png": b"\0" * (4 * MIB)})
    assert len(data) < 64 * 1024  # it really is a bomb
    with pytest.raises(DecompressionRatio) as ei:
        validate_and_extract(_write(tmp_path, data), tmp_path / "d")
    assert ei.value.status == 422 and ei.value.code == "decompression_ratio"
    assert not (tmp_path / "d" / "frames").exists()  # rejected at listing time


def test_total_ratio_applies_to_tar_gz(tmp_path):
    src = make_bundle(tmp_path / "src")
    (src / "frames" / "00000" / "conf.png").write_bytes(b"\0" * (2 * MIB))
    data = tar_dir(src)
    with pytest.raises(DecompressionRatio, match="inflates more than"):
        validate_and_extract(_write(tmp_path, data), tmp_path / "d",
                             limits=UploadLimits(max_ratio=5))


def test_small_members_are_not_judged_on_ratio(tmp_path):
    # objects.json / masks compress extremely well; the per-member ratio only
    # counts above 1 MiB, and the total ratio is what bounds the small ones.
    import random

    rnd = random.Random(1).randbytes(1 * MIB)  # incompressible, dominates the total
    data = _zip_with(tmp_path, {"frames/00000/objects.json": b"[" + b"0," * 250_000 + b"0]"},
                     **{"frames/00000/rgb.jpg": rnd})
    res = validate_and_extract(_write(tmp_path, data), tmp_path / "d",
                               limits=UploadLimits(max_ratio=3))
    assert res.format == "bundle"


# --- size caps -------------------------------------------------------------------
def test_member_over_per_member_cap_is_413_member_too_large(tmp_path):
    data = _zip_with(tmp_path, {}, **{"frames/00000/rgb.jpg": bytes(range(256)) * 64})
    with pytest.raises(MemberTooLarge) as ei:
        validate_and_extract(_write(tmp_path, data), tmp_path / "d",
                             limits=UploadLimits(max_member_bytes=1024))
    assert ei.value.status == 413 and ei.value.code == "member_too_large"
    assert isinstance(ei.value, UploadTooLarge)


def test_legacy_keyword_caps_still_override(tmp_path):
    src = make_bundle(tmp_path / "src")
    with pytest.raises(UploadTooLarge, match="members"):
        validate_and_extract(_write(tmp_path, zip_dir(src)), tmp_path / "d", max_members=2)


def test_zip_member_lying_about_its_size_is_rejected(tmp_path):
    src = make_bundle(tmp_path / "src")
    (src / "frames" / "00000" / "conf.png").write_bytes(b"x" * 5000)
    data = bytearray(zip_dir(src))
    # patch the central-directory file_size of conf.png down to 10 bytes
    with zipfile.ZipFile(io.BytesIO(bytes(data))) as zf:
        info = zf.getinfo("frames/00000/conf.png")
    cd = data.rfind(b"PK\x01\x02")
    while cd != -1:
        name_len = int.from_bytes(data[cd + 28:cd + 30], "little")
        if data[cd + 46:cd + 46 + name_len] == b"frames/00000/conf.png":
            data[cd + 24:cd + 28] = (10).to_bytes(4, "little")
            break
        cd = data.rfind(b"PK\x01\x02", 0, cd)
    assert info.file_size == 5000
    with pytest.raises(InvalidUpload):
        validate_and_extract(_write(tmp_path, bytes(data)), tmp_path / "d")


# --- member names ------------------------------------------------------------------
@pytest.mark.parametrize("name", ["frames/x.zip", "b/inner.tar.gz", "weights.7z", "a.tgz"])
def test_nested_archives_are_rejected(tmp_path, name):
    data = _zip_with(tmp_path, {name: b"PK\x03\x04junk"})
    with pytest.raises(NestedArchive) as ei:
        validate_and_extract(_write(tmp_path, data), tmp_path / "d")
    assert ei.value.code == "nested_archive" and ei.value.status == 422


def test_zip_symlink_entry_is_rejected(tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("nope")
    buf = io.BytesIO(zip_dir(make_bundle(tmp_path / "src")))
    with zipfile.ZipFile(buf, "a") as zf:
        info = zipfile.ZipInfo("frames/00000/conf.png")
        info.external_attr = (0o120777 << 16)  # S_IFLNK | 0777
        zf.writestr(info, str(outside))
    with pytest.raises(InvalidUpload, match="symlink"):
        validate_and_extract(_write(tmp_path, buf.getvalue()), tmp_path / "d")
    assert not (tmp_path / "d" / "frames").exists()


@pytest.mark.parametrize("name", ["/abs/manifest.json", "../up.txt", "frames/../../x"])
def test_tar_absolute_and_escaping_members_are_rejected(tmp_path, name):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        info = tarfile.TarInfo(name)
        info.size = 1
        tf.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(InvalidUpload):
        validate_and_extract(_write(tmp_path, buf.getvalue()), tmp_path / "d")
    assert not (tmp_path / "up.txt").exists() and not (tmp_path / "x").exists()


def test_member_written_through_a_pre_existing_symlink_dir_is_rejected(tmp_path):
    """Filesystem-level check: the parse passes, resolve() catches it."""
    outside = tmp_path / "outside"
    outside.mkdir()
    dest = tmp_path / "d"
    dest.mkdir()
    (dest / "frames").symlink_to(outside, target_is_directory=True)
    data = zip_dir(make_bundle(tmp_path / "src"))
    with pytest.raises(InvalidUpload, match="outside|link"):
        validate_and_extract(_write(tmp_path, data), dest)
    assert not any(outside.iterdir())


# --- bundle contents ---------------------------------------------------------------
@pytest.mark.parametrize("manifest", [
    b"[]", b"not json", b'{"fps": 30, "frame_count": 2}',
    b'{"session_id": "s", "fps": "thirty", "frame_count": 2}',
    b'{"session_id": "s", "fps": 0, "frame_count": 2}',
])
def test_bad_manifest_has_its_own_code(tmp_path, manifest):
    data = _zip_with(tmp_path, {}, **{"manifest.json": manifest})
    with pytest.raises(BadManifest) as ei:
        validate_and_extract(_write(tmp_path, data), tmp_path / "d")
    assert ei.value.code == "bad_manifest" and ei.value.status == 422
    assert isinstance(ei.value, InvalidUpload)


def test_frame_count_caps_apply_to_manifest_and_frame_dirs(tmp_path):
    lim = UploadLimits(max_frames=2)
    data = _zip_with(tmp_path, {}, frames=3)
    with pytest.raises(TooManyFrames) as ei:
        validate_and_extract(_write(tmp_path, data), tmp_path / "d1", limits=lim)
    assert ei.value.code == "too_many_frames"
    # manifest claims 2 but there are 3 dirs -> the dir count trips
    manifest = json.dumps({"session_id": "s", "fps": 30.0, "frame_count": 2}).encode()
    data = _zip_with(tmp_path / "b", {}, frames=3, **{"manifest.json": manifest})
    with pytest.raises(TooManyFrames, match="frame dirs"):
        validate_and_extract(_write(tmp_path, data), tmp_path / "d2", limits=lim)


@pytest.mark.parametrize("depth", [
    png_header(bit_depth=8),                 # 8-bit greyscale
    png_header(bit_depth=16, colour_type=2),  # 16-bit RGB
    b"\xff\xd8\xff\xd9",                     # a JPEG
    b"\x89PNG\r\n\x1a\n",                    # signature only, no IHDR
    b"",
])
def test_depth_png_must_be_16_bit_greyscale(tmp_path, depth):
    data = _zip_with(tmp_path, {}, **{"frames/00001/depth.png": depth})
    with pytest.raises(BadDepthDtype) as ei:
        validate_and_extract(_write(tmp_path, data), tmp_path / "d")
    assert ei.value.code == "bad_depth_dtype" and ei.value.status == 422


def test_missing_depth_png_is_bad_depth_dtype(tmp_path):
    src = make_bundle(tmp_path / "src")
    (src / "frames" / "00001" / "depth.png").unlink()
    with pytest.raises(BadDepthDtype, match="missing"):
        validate_and_extract(_write(tmp_path, zip_dir(src)), tmp_path / "d")


def test_depth_resolution_cap(tmp_path):
    data = _zip_with(tmp_path, {}, **{"frames/00000/depth.png": png_header(5000, 5000)})
    with pytest.raises(FrameTooLarge) as ei:
        validate_and_extract(_write(tmp_path, data), tmp_path / "d")
    assert ei.value.code == "frame_too_large"


def test_depth_sample_covers_first_and_last_frames(tmp_path):
    src = make_bundle(tmp_path / "src", frames=6)
    (src / "frames" / "00005" / "depth.png").write_bytes(png_header(bit_depth=8))
    with pytest.raises(BadDepthDtype, match="00005"):
        validate_and_extract(_write(tmp_path, zip_dir(src)), tmp_path / "d",
                             limits=UploadLimits(depth_sample=2))


def test_png_ihdr_parses_the_fixture_header():
    assert png_ihdr(DEPTH_PNG) == (4, 3, 16, 0)
    with pytest.raises(ValueError):
        png_ihdr(b"GIF89a" + b"\0" * 32)


def test_tum_frame_list_cap(tmp_path):
    seq = make_tum_sequence(tmp_path / "seq")
    (seq / "rgb.txt").write_text("# c\n" + "".join(f"{i}.0 rgb/{i}.png\n" for i in range(5)))
    check_tum_contents(seq, UploadLimits(max_frames=5))
    with pytest.raises(TooManyFrames, match="rgb.txt lists 5"):
        check_tum_contents(seq, UploadLimits(max_frames=4))


# --- streaming body cap ----------------------------------------------------------------
def test_capped_receive_aborts_mid_stream():
    import asyncio

    chunks = [b"a" * 10, b"b" * 10, b"c" * 10]

    async def receive():
        return {"type": "http.request", "body": chunks.pop(0), "more_body": bool(chunks)}

    recv = capped_receive(receive, 25)

    async def run():
        await recv()
        await recv()
        with pytest.raises(UploadTooLarge):
            await recv()

    asyncio.run(run())


def test_chunked_upload_without_content_length_is_cut_at_the_cap(
        root_scene, frontend_dir, jobs_dir, fixture_scene, bundle_zip):
    app = create_app(root_scene, frontend_dir, jobs_dir=jobs_dir, worker_mode="mock",
                     fixture_scene=fixture_scene, start_worker=False, max_upload_bytes=64)
    body = (b"--b\r\nContent-Disposition: form-data; name=\"archive\"; filename=\"x.zip\"\r\n"
            b"Content-Type: application/zip\r\n\r\n" + bundle_zip + b"\r\n--b--\r\n")
    # the multipart slack is 64 KiB; make the body clearly larger than that
    body += b"\r\n" + b"\0" * (80 * 1024)

    def gen():
        for i in range(0, len(body), 4096):
            yield body[i:i + 4096]

    r = TestClient(app).post("/api/jobs", content=gen(),
                             headers={"content-type": "multipart/form-data; boundary=b"})
    assert r.status_code == 413, r.text
    assert r.json()["error"]["code"] == "upload_too_large"
    assert not jobs_dir.exists() or not any(p.is_dir() for p in jobs_dir.iterdir())


# --- through the API -------------------------------------------------------------------
def test_each_rejection_code_reaches_the_client_and_leaves_no_job(
        client, jobs_dir, tmp_path, monkeypatch):
    cases = [
        ("bomb", _zip_with(tmp_path / "a", {"frames/00000/conf.png": b"\0" * (4 * MIB)}),
         422, "decompression_ratio"),
        ("nested", _zip_with(tmp_path / "b", {"frames/x.zip": b"PK"}), 422, "nested_archive"),
        ("manifest", _zip_with(tmp_path / "c", {}, **{"manifest.json": b"[]"}), 422, "bad_manifest"),
        ("depth", _zip_with(tmp_path / "d", {}, **{"frames/00000/depth.png": png_header(bit_depth=8)}),
         422, "bad_depth_dtype"),
        ("pixels", _zip_with(tmp_path / "e", {}, **{"frames/00000/depth.png": png_header(9000, 9000)}),
         422, "frame_too_large"),
    ]
    for label, data, status, code in cases:
        r = post_archive(client, data)
        assert r.status_code == status, (label, r.text)
        assert r.json()["error"]["code"] == code, label
    monkeypatch.setenv("SOBA_MAX_FRAMES", "1")
    r = post_archive(client, _zip_with(tmp_path / "f", {}, frames=2))
    assert (r.status_code, r.json()["error"]["code"]) == (422, "too_many_frames")
    monkeypatch.setenv("SOBA_MAX_MEMBER_MB", "1")
    r = post_archive(client, _zip_with(tmp_path / "g", {}, **{"frames/00000/rgb.jpg": bytes(range(256)) * 4200}))
    assert (r.status_code, r.json()["error"]["code"]) == (413, "member_too_large")
    assert client.get("/api/jobs").json()["jobs"] == []
    assert not jobs_dir.exists() or not any(p.is_dir() for p in jobs_dir.iterdir())
