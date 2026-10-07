"""provenance.py: dirty flag counts tracked changes only (review finding R17)."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from gridsense_sim.provenance import collect_provenance

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _git(repo, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "ci@example.com")
    _git(tmp_path, "config", "user.name", "ci")
    (tmp_path / "model.py").write_text("x = 1\n")
    _git(tmp_path, "add", "model.py")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def test_clean_checkout_is_not_dirty(repo) -> None:
    p = collect_provenance(repo)
    assert p.git_commit and p.git_dirty is False and p.git_untracked_files == 0


def test_untracked_file_is_counted_but_not_dirty(repo) -> None:
    """A stray .coverage used to mark every run as dirty."""
    (repo / ".coverage").write_text("noise")
    p = collect_provenance(repo)
    assert p.git_dirty is False and p.git_untracked_files == 1


def test_modified_tracked_file_is_dirty(repo) -> None:
    (repo / "model.py").write_text("x = 2\n")
    assert collect_provenance(repo).git_dirty is True


def test_outside_a_repository_yields_none(tmp_path) -> None:
    p = collect_provenance(tmp_path)
    assert p.git_commit is None and p.git_dirty is None and p.git_untracked_files is None
