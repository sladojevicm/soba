"""Read selected files out of a large REMOTE zip via HTTP range requests.

A zip's central directory lives at the END of the file, so with a seekable
range-backed file object we can list members and extract individual entries
WITHOUT downloading the whole archive. Used to pull just the strided frames of
each Replica room out of the 44.79 GB vmap.zip on HuggingFace.
"""

from __future__ import annotations

import io
import time

import requests

DEFAULT_BLOCK = 4 * 1024 * 1024  # 4 MiB
TIMEOUT = (15, 90)  # (connect, read) seconds
RETRIES = 5


class HTTPRangeFile(io.RawIOBase):
    """A seekable, read-only file-like backed by HTTP Range requests.

    Caches fixed-size blocks so zipfile's many small reads (central-directory
    scan, per-member headers) don't each become a separate request.
    """

    def __init__(self, url: str, block: int = DEFAULT_BLOCK, session=None):
        self.url = url
        self.block = block
        self._pos = 0
        self._cache: dict[int, bytes] = {}
        self._session = session or requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=24)
        self._session.mount("https://", adapter)
        self._eff_url = url  # signed CDN url after following the HF 302 (resolved lazily)
        # resolve the redirect ONCE and read size from the same ranged GET, so we
        # don't follow a 302 on every subsequent block request.
        r = self._ranged_get(0, 0, resolve=True)
        if r.status_code != 206:
            raise RuntimeError(f"server does not support range requests ({r.status_code})")
        # Content-Range: bytes 0-0/<total>
        self.size = int(r.headers["content-range"].split("/")[-1])

    def _ranged_get(self, start: int, end: int, resolve: bool = False):
        """Range GET [start,end] inclusive. Follows the HF 302 once (resolve=True)
        and caches the signed CDN url; later calls hit it directly. Retries
        transient timeouts/connection errors and re-resolves an expired url."""
        headers = {"Range": f"bytes={start}-{end}"}
        last_exc = None
        for attempt in range(RETRIES):
            try:
                url = self.url if resolve else self._eff_url
                r = self._session.get(
                    url, headers=headers, allow_redirects=resolve, timeout=TIMEOUT
                )
                if r.status_code in (401, 403) and not resolve:
                    resolve = True  # signed url expired -> re-resolve next loop
                    continue
                if resolve and r.history:
                    self._eff_url = r.url  # remember the signed CDN url
                    resolve = False
                if r.status_code in (200, 206):
                    return r
                if r.status_code in (429, 500, 502, 503, 504):
                    raise requests.exceptions.RequestException(f"status {r.status_code}")
                r.raise_for_status()
                return r
            except (requests.exceptions.RequestException, OSError) as exc:
                last_exc = exc
                resolve = True  # safest: re-resolve and retry
                time.sleep(min(2 ** attempt, 20))
        raise RuntimeError(f"range GET {start}-{end} failed after {RETRIES} tries: {last_exc}")

    # -- io plumbing ------------------------------------------------------
    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            self._pos = offset
        elif whence == io.SEEK_CUR:
            self._pos += offset
        elif whence == io.SEEK_END:
            self._pos = self.size + offset
        return self._pos

    def tell(self) -> int:
        return self._pos

    def _fetch_block(self, idx: int) -> bytes:
        if idx in self._cache:
            return self._cache[idx]
        start = idx * self.block
        end = min(start + self.block, self.size) - 1
        data = self._ranged_get(start, end).content
        self._cache[idx] = data
        return data

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            n = self.size - self._pos
        n = min(n, self.size - self._pos)
        if n <= 0:
            return b""
        out = bytearray()
        pos = self._pos
        while len(out) < n:
            idx = pos // self.block
            off = pos % self.block
            blk = self._fetch_block(idx)
            take = min(len(blk) - off, n - len(out))
            out += blk[off : off + take]
            pos += take
        self._pos = pos
        return bytes(out)

    def prefetch_blocks(self, block_indices, workers: int = 12) -> None:
        """Fetch many blocks concurrently into the cache (CDN range requests are
        independent). Subsequent read()s for these blocks hit the cache."""
        from concurrent.futures import ThreadPoolExecutor

        todo = sorted({i for i in block_indices if i not in self._cache})
        if not todo:
            return
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(self._fetch_block, todo))

    def blocks_for_span(self, start: int, length: int) -> range:
        """Block indices covering [start, start+length)."""
        first = start // self.block
        last = (start + max(length, 1) - 1) // self.block
        return range(first, last + 1)

    def clear_cache(self) -> None:
        self._cache.clear()


if __name__ == "__main__":
    import sys
    import zipfile
    from collections import Counter

    url = sys.argv[1]
    rf = HTTPRangeFile(url)
    print(f"remote size: {rf.size / 1e9:.2f} GB")
    zf = zipfile.ZipFile(rf)
    names = zf.namelist()
    print(f"members: {len(names)}")
    tops = Counter(n.split("/")[0] for n in names)
    print("top-level entries:")
    for k, v in sorted(tops.items()):
        print(f"  {v:>7}  {k}")
    # show a couple of example deep paths
    print("examples:")
    for n in names[:4] + [x for x in names if "imap/00/depth" in x][:3]:
        print("  ", n)
