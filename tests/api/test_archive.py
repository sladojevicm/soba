"""validate_and_extract: format sniffing, zip-slip guards, caps, layout detection."""

from __future__ import annotations

import io
import tarfile
import zipfile

import pytest
from _fixtures import make_bundle, make_tum_sequence, tar_dir, zip_dir

from api.jobs.archive import (
    InvalidUpload,
    UnsupportedArchive,
    UploadTooLarge,
    imaging_available,
    sniff_format,
    validate_and_extract,
)


def _write(tmp_path, data: bytes, name: str = "u.bin"):
    p = tmp_path / name
    p.write_bytes(data)
    return p


@pytest.mark.parametrize("kind", ["zip", "tar.gz", "tar"])
def test_bundle_archives_are_sniffed_and_extracted(tmp_path, kind):
    src = make_bundle(tmp_path / "src")
    data = {"zip": zip_dir(src), "tar.gz": tar_dir(src), "tar": tar_dir(src, gz=False)}[kind]
    res = validate_and_extract(_write(tmp_path, data), tmp_path / "dest")
    assert res.archive_format == kind
    assert res.format == "bundle"
    assert res.root == tmp_path / "dest"
    assert (res.root / "frames" / "00000" / "depth.png").is_file()


def test_bundle_inside_single_top_level_dir(tmp_path):
    src = make_bundle(tmp_path / "src")
    res = validate_and_extract(_write(tmp_path, zip_dir(src, top="office_3")), tmp_path / "d")
    assert res.root == tmp_path / "d" / "office_3"


def test_macosx_junk_dir_is_ignored_for_layout_detection(tmp_path):
    src = make_bundle(tmp_path / "src")
    buf = io.BytesIO(zip_dir(src, top="b"))
    with zipfile.ZipFile(buf, "a") as zf:
        zf.writestr("__MACOSX/b/._manifest.json", b"junk")
    res = validate_and_extract(_write(tmp_path, buf.getvalue()), tmp_path / "d")
    assert res.root == tmp_path / "d" / "b"


@pytest.mark.parametrize("data", [b"not an archive at all", bytes(range(256)) * 4,
                                  b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64])
def test_non_archives_are_unsupported(tmp_path, data):
    with pytest.raises(UnsupportedArchive):
        sniff_format(_write(tmp_path, data))
    with pytest.raises(UnsupportedArchive) as ei:
        validate_and_extract(_write(tmp_path, data), tmp_path / "d")
    assert ei.value.status == 415


def test_corrupt_zip_is_invalid(tmp_path):
    with pytest.raises(InvalidUpload) as ei:
        validate_and_extract(_write(tmp_path, b"PK\x03\x04" + b"garbage" * 10), tmp_path / "d")
    assert ei.value.status == 422


@pytest.mark.parametrize("member", ["../evil.txt", "a/../../evil.txt", "/etc/passwd",
                                    "C:\\evil.txt", "a\\b.txt"])
def test_zip_slip_members_are_rejected(tmp_path, member):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", "{}")
        zf.writestr(member, "x")
    with pytest.raises(InvalidUpload):
        validate_and_extract(_write(tmp_path, buf.getvalue()), tmp_path / "d")
    assert not (tmp_path / "evil.txt").exists()


@pytest.mark.parametrize("linktype", [tarfile.SYMTYPE, tarfile.LNKTYPE])
def test_tar_links_are_rejected(tmp_path, linktype):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        info = tarfile.TarInfo("manifest.json")
        info.size = 2
        tf.addfile(info, io.BytesIO(b"{}"))
        link = tarfile.TarInfo("link")
        link.type = linktype
        link.linkname = "/etc/passwd"
        tf.addfile(link)
    with pytest.raises(InvalidUpload, match="not a regular file"):
        validate_and_extract(_write(tmp_path, buf.getvalue()), tmp_path / "d")


def test_extracted_size_cap(tmp_path):
    src = make_bundle(tmp_path / "src")
    with pytest.raises(UploadTooLarge) as ei:
        validate_and_extract(_write(tmp_path, zip_dir(src)), tmp_path / "d",
                             max_extracted_bytes=10)
    assert ei.value.status == 413


def test_member_count_cap(tmp_path):
    src = make_bundle(tmp_path / "src", frames=3)
    with pytest.raises(UploadTooLarge, match="members"):
        validate_and_extract(_write(tmp_path, tar_dir(src)), tmp_path / "d", max_members=3)


def test_unknown_layout_is_invalid(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "hello")
        zf.writestr("video.mp4", b"\x00" * 16)
    with pytest.raises(InvalidUpload, match="neither a PerceptionBundle"):
        validate_and_extract(_write(tmp_path, buf.getvalue()), tmp_path / "d")


def test_bundle_without_frames_is_invalid(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", '{"session_id":"s","fps":30,"frame_count":0}')
        zf.writestr("intrinsics.json", '{"fx":1,"fy":1,"cx":0,"cy":0}')
    with pytest.raises(InvalidUpload, match="frames"):
        validate_and_extract(_write(tmp_path, buf.getvalue()), tmp_path / "d")


def test_bundle_with_bad_manifest_is_invalid(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", '{"fps": "thirty"}')  # missing session_id
        zf.writestr("intrinsics.json", '{"fx":1,"fy":1,"cx":0,"cy":0}')
        zf.writestr("frames/00000/rgb.jpg", b"x")
    with pytest.raises(InvalidUpload, match="manifest"):
        validate_and_extract(_write(tmp_path, buf.getvalue()), tmp_path / "d")


def test_tum_layout_needs_an_imaging_backend(tmp_path):
    src = make_tum_sequence(tmp_path / "seq")
    archive = _write(tmp_path, zip_dir(src, top="rgbd_dataset_freiburg1_xyz"))
    if imaging_available():
        res = validate_and_extract(archive, tmp_path / "d")
        assert res.format == "tum"
        assert res.root.name == "rgbd_dataset_freiburg1_xyz"
    else:
        with pytest.raises(InvalidUpload, match="cannot convert") as ei:
            validate_and_extract(archive, tmp_path / "d")
        assert ei.value.status == 422
