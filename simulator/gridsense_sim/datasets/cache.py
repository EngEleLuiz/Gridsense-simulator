"""Local cache for raw dataset files.

* Root: ``$GRIDSENSE_DATA_DIR`` or ``data/raw`` (git-ignored).
* Every cached file gets a sidecar ``<file>.source.json`` with the URL it
  came from, retrieval time, size and SHA-256. The hash is re-checked on
  every read, so a silently modified raw file is detected
  (:class:`ChecksumMismatchError`).
* Downloads go to ``<file>.part`` and are renamed only when complete, so
  an interrupted download never leaves a truncated file in the cache.
* Several mirror URLs can be given; they are tried in order, each with
  retries and exponential back-off.
* ``GRIDSENSE_OFFLINE=1`` (or ``offline=True``) forbids network access:
  a missing file raises :class:`DataNotAvailableError` whose message says
  exactly where to put a manually downloaded copy.

Only the standard library is used for HTTP (no ``requests`` dependency).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .base import DataNotAvailableError, DatasetError, SourceFile, sha256_file

logger = logging.getLogger(__name__)

USER_AGENT = "gridsense-simulator (+https://github.com/EngEleLuiz/Gridsense-simulator)"
DEFAULT_TIMEOUT_S = 120


class ChecksumMismatchError(DatasetError):
    """A cached file no longer matches the hash recorded when it was fetched."""


def default_root() -> Path:
    return Path(os.environ.get("GRIDSENSE_DATA_DIR", "data/raw"))


def offline_by_env() -> bool:
    return os.environ.get("GRIDSENSE_OFFLINE", "").strip().lower() in {"1", "true", "yes"}


@dataclass
class DataCache:
    root: Path | None = None
    offline: bool | None = None
    retries: int = 3
    backoff_s: float = 2.0
    timeout_s: float = DEFAULT_TIMEOUT_S

    def __post_init__(self) -> None:
        self.root = Path(self.root) if self.root is not None else default_root()
        if self.offline is None:
            self.offline = offline_by_env()

    # ------------------------------------------------------------------ paths
    def path_for(self, dataset: str, filename: str) -> Path:
        safe = Path(filename).name
        if safe != filename or safe in {"", ".", ".."}:
            raise ValueError(f"Cache file names must be plain names, got {filename!r}.")
        return self.root / dataset / safe

    @staticmethod
    def _sidecar(path: Path) -> Path:
        return path.with_name(path.name + ".source.json")

    # ------------------------------------------------------------------- read
    def cached(self, dataset: str, filename: str) -> SourceFile | None:
        path = self.path_for(dataset, filename)
        if not path.exists():
            return None
        return self._verify(path)

    def _verify(self, path: Path) -> SourceFile:
        sidecar = self._sidecar(path)
        actual = sha256_file(path)
        url = None
        if sidecar.exists():
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
            url = meta.get("url")
            if meta.get("sha256") and meta["sha256"] != actual:
                raise ChecksumMismatchError(
                    f"{path} changed since it was cached (sha256 {actual[:12]} != "
                    f"{meta['sha256'][:12]}). Delete it and its .source.json to re-fetch."
                )
        else:  # manually placed file: record it now
            self._write_sidecar(path, url=None, sha=actual, note="placed manually")
        return SourceFile(str(path), actual, path.stat().st_size, url)

    # ------------------------------------------------------------------ fetch
    def fetch(
        self,
        dataset: str,
        filename: str,
        urls: list[str] | tuple[str, ...],
        expected_sha256: str | None = None,
    ) -> SourceFile:
        """Return the cached file, downloading it from the first working mirror."""
        path = self.path_for(dataset, filename)
        hit = self.cached(dataset, filename)
        if hit is not None:
            if expected_sha256 and hit.sha256 != expected_sha256:
                raise ChecksumMismatchError(
                    f"{path}: sha256 {hit.sha256[:12]} != expected {expected_sha256[:12]}."
                )
            return hit
        if self.offline:
            raise DataNotAvailableError(self._manual_hint(path, urls, "offline mode"))
        errors: list[str] = []
        for url in urls:
            try:
                self._download(url, path)
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                errors.append(f"{url}: {exc}")
                continue
            actual = sha256_file(path)
            if expected_sha256 and actual != expected_sha256:
                path.unlink(missing_ok=True)
                errors.append(f"{url}: sha256 {actual[:12]} != expected {expected_sha256[:12]}")
                continue
            self._write_sidecar(path, url=url, sha=actual)
            return SourceFile(str(path), actual, path.stat().st_size, url)
        raise DataNotAvailableError(self._manual_hint(path, urls, "; ".join(errors) or "no URL"))

    def _download(self, url: str, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        last: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp, open(part, "wb") as out:
                    shutil.copyfileobj(resp, out, length=1 << 20)
                if part.stat().st_size == 0:
                    raise OSError(f"empty response from {url}")
                part.replace(path)
                logger.info("Downloaded %s -> %s (%d bytes)", url, path, path.stat().st_size)
                return
            except urllib.error.HTTPError as exc:
                last = exc
                part.unlink(missing_ok=True)
                if 400 <= exc.code < 500 and exc.code != 429:
                    break  # client error: retrying will not help
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                last = exc
                part.unlink(missing_ok=True)
            if attempt < self.retries:
                time.sleep(self.backoff_s * 2 ** (attempt - 1))
        assert last is not None
        raise last

    def _write_sidecar(self, path: Path, url: str | None, sha: str, note: str | None = None) -> None:
        meta = {
            "url": url,
            "sha256": sha,
            "size_bytes": path.stat().st_size,
            "retrieved_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        if note:
            meta["note"] = note
        self._sidecar(path).write_text(json.dumps(meta, indent=2), encoding="utf-8")

    @staticmethod
    def _manual_hint(path: Path, urls, reason: str) -> str:
        url_list = "\n  ".join(urls) if urls else "(no public URL; see docs/DATASETS.md)"
        return (
            f"Raw file not available ({reason}).\n"
            f"Download it manually from:\n  {url_list}\n"
            f"and save it as:\n  {path}\n"
            f"(or set GRIDSENSE_DATA_DIR to the folder that already holds it)."
        )


def build_url(base: str, params: dict) -> str:
    """URL with sorted, encoded query parameters (stable cache keys)."""
    clean = {k: v for k, v in params.items() if v is not None}
    return f"{base}?{urllib.parse.urlencode(sorted(clean.items()))}"


__all__ = ["ChecksumMismatchError", "DataCache", "build_url", "default_root", "offline_by_env"]
