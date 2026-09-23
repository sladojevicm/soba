"""Upload rejection hierarchy: one class per error code the job API can return.

Kept in its own module so ``api.jobs.archive`` (extraction) and
``api.security.upload_validation`` (hardening) can both import it without a
cycle. Every class carries ``.status`` and ``.code``; ``routes/jobs.py`` maps
an instance straight onto the JSON error envelope.

    400 bad_upload            base class (never raised as such today)
    413 upload_too_large      body / total extracted size / member count cap
    413 member_too_large      one archive member over the per-member cap
    415 unsupported_archive   not zip / tar.gz / tar (MP4 lands here)
    422 invalid_upload        zip-slip, links, corrupt archive, unknown layout
    422 decompression_ratio   archive inflates more than the allowed ratio
    422 nested_archive        an archive inside the archive
    422 bad_manifest          manifest.json rejected by Manifest.from_dict
    422 too_many_frames       frame_count / frame dirs over the cap
    422 bad_depth_dtype       depth.png is not a 16-bit greyscale PNG
    422 frame_too_large       depth.png resolution over the pixel cap
"""

from __future__ import annotations


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


class MemberTooLarge(UploadTooLarge):
    code = "member_too_large"


class InvalidUpload(UploadError):
    status = 422
    code = "invalid_upload"


class DecompressionRatio(InvalidUpload):
    code = "decompression_ratio"


class NestedArchive(InvalidUpload):
    code = "nested_archive"


class BadManifest(InvalidUpload):
    code = "bad_manifest"


class TooManyFrames(InvalidUpload):
    code = "too_many_frames"


class BadDepthDtype(InvalidUpload):
    code = "bad_depth_dtype"


class FrameTooLarge(InvalidUpload):
    code = "frame_too_large"


__all__ = [
    "BadDepthDtype",
    "BadManifest",
    "DecompressionRatio",
    "FrameTooLarge",
    "InvalidUpload",
    "MemberTooLarge",
    "NestedArchive",
    "TooManyFrames",
    "UnsupportedArchive",
    "UploadError",
    "UploadTooLarge",
]
