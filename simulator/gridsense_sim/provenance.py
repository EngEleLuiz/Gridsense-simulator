"""Run provenance for reproducible studies (review finding N4).

Every study result must answer "which code, which libraries, which
seed produced this number?" -- otherwise it cannot be cited or
re-checked by the examining board. :func:`collect_provenance` is
cheap and never raises: missing git simply yields ``None`` fields.
"""

from __future__ import annotations

import platform
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

TRACKED_PACKAGES: tuple[str, ...] = ("pandapower", "numpy", "scipy", "pandas", "numba")


@dataclass(frozen=True)
class Provenance:
    """Code and environment fingerprint of a run."""

    git_commit: str | None
    git_dirty: bool | None
    python: str
    platform: str
    packages: dict[str, str | None]
    collected_at: str

    def to_dict(self) -> dict:
        return asdict(self)


def _git(args: list[str], cwd: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=5, check=True
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip()


def _version(pkg: str) -> str | None:
    try:
        return metadata.version(pkg)
    except metadata.PackageNotFoundError:
        return None


def collect_provenance(repo_dir: str | Path | None = None) -> Provenance:
    """Fingerprint the current checkout and environment."""
    cwd = Path(repo_dir) if repo_dir else Path(__file__).resolve().parent
    commit = _git(["rev-parse", "HEAD"], cwd)
    status = _git(["status", "--porcelain"], cwd) if commit else None
    return Provenance(
        git_commit=commit,
        git_dirty=(bool(status) if status is not None else None),
        python=sys.version.split()[0],
        platform=platform.platform(),
        packages={p: _version(p) for p in TRACKED_PACKAGES},
        collected_at=datetime.now(timezone.utc).isoformat(),
    )
