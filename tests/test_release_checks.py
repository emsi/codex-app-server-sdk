"""Exercise publishing guards with real archives and isolated Git histories."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tarfile
from email.message import Message
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from zipfile import ZipFile

import pytest
import typer
from packaging.version import Version
from typer.testing import CliRunner

from scripts import check_release
from scripts.check_quality import changed_python_files

VERSION = Version("1.2.3")
PACKAGE = "codex-app-server-sdk"
WHEEL_NAME = "codex_app_server_sdk-1.2.3-py3-none-any.whl"
SDIST_NAME = "codex_app_server_sdk-1.2.3.tar.gz"


@pytest.fixture
def release_root(tmp_path: Path) -> Path:
    """Create minimal release inputs without touching the project itself.

    :param tmp_path: Pytest temporary directory.
    :return: Root containing matching project, lockfile, and changelog versions.
    """
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "{PACKAGE}"\nversion = "{VERSION}"\n'
    )
    (tmp_path / "uv.lock").write_text(
        f'[[package]]\nname = "{PACKAGE}"\nversion = "{VERSION}"\n'
    )
    (tmp_path / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## {VERSION}\n\nRelease notes.\n\n## 1.2.2\n\nOld notes.\n"
    )
    return tmp_path


def write_artifacts(root: Path, *, metadata_version: str = str(VERSION)) -> None:
    """Create tiny distribution archives with independently configurable metadata.

    :param root: Output directory.
    :param metadata_version: Version recorded inside the archives.
    :return: None.
    """
    metadata = f"Metadata-Version: 2.4\nName: {PACKAGE}\nVersion: {metadata_version}\n".encode()
    with ZipFile(root / WHEEL_NAME, "w") as wheel:
        wheel.writestr("codex_app_server_sdk-1.2.3.dist-info/METADATA", metadata)
        wheel.writestr(check_release.TYPING_MARKER, b"")
    with tarfile.open(root / SDIST_NAME, "w:gz") as archive:
        member = tarfile.TarInfo("codex_app_server_sdk-1.2.3/PKG-INFO")
        member.size = len(metadata)
        archive.addfile(member, BytesIO(metadata))


def git(root: Path, *args: str) -> str:
    """Run Git only inside a temporary test repository.

    :param root: Temporary working tree.
    :param args: Git arguments.
    :return: Stripped command output.
    """
    return subprocess.check_output(
        ["git", *args], cwd=root, text=True, stderr=subprocess.DEVNULL
    ).strip()


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """Create a temporary Git history for tag and changed-file checks.

    :param tmp_path: Pytest temporary directory.
    :return: Initialized repository with one commit and an earlier version tag.
    """
    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "Release test")
    git(tmp_path, "config", "user.email", "release-test@example.invalid")
    (tmp_path / "kept.py").write_text("pass\n")
    (tmp_path / "deleted.py").write_text("pass\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "baseline")
    git(tmp_path, "tag", "v1.2.2")
    return tmp_path


def test_notes_are_limited_to_current_version(release_root: Path) -> None:
    """Extract only the intended release section.

    :param release_root: Valid release inputs.
    :return: None.
    """
    assert check_release.read_release(release_root) == (VERSION, "Release notes.\n")


@pytest.mark.parametrize(
    "broken", ["lock", "missing-notes", "duplicate-notes", "empty-notes"]
)
def test_inconsistent_release_inputs_fail(release_root: Path, broken: str) -> None:
    """Reject independent metadata drift and malformed release notes.

    :param release_root: Valid release inputs to corrupt.
    :param broken: Fault being introduced.
    :return: None.
    """
    if broken == "lock":
        path = release_root / "uv.lock"
        path.write_text(path.read_text().replace(str(VERSION), "1.2.2"))
    else:
        path = release_root / "CHANGELOG.md"
        entries = {
            "missing-notes": "## 1.2.2\nOld notes\n",
            "duplicate-notes": f"## {VERSION}\nOne\n## {VERSION}\nTwo\n",
            "empty-notes": f"## {VERSION}\n\n## 1.2.2\nOld notes\n",
        }
        path.write_text(entries[broken])
    with pytest.raises(ValueError):
        check_release.read_release(release_root)


@pytest.mark.parametrize("tag", ["master", "v1.2.2", "1.2.3"])
def test_cli_rejects_wrong_tag(release_root: Path, tag: str) -> None:
    """Fail before publishing for branches, stale versions, or missing prefixes.

    :param release_root: Valid release inputs.
    :param tag: Invalid release ref.
    :return: None.
    """
    app = typer.Typer()
    app.command()(check_release.main)
    result = CliRunner().invoke(app, ["--root", str(release_root), "--tag", tag])
    assert result.exit_code != 0
    assert "must be v1.2.3" in result.output


def test_existing_tag_must_point_to_head(repository: Path) -> None:
    """Reject a correctly named tag targeting a different commit.

    :param repository: Temporary Git repository.
    :return: None.
    """
    git(repository, "tag", f"v{VERSION}")
    check_release.validate_tag(repository, VERSION, f"v{VERSION}", True)
    git(repository, "commit", "--allow-empty", "-m", "later")
    with pytest.raises(ValueError, match="checked-out commit"):
        check_release.validate_tag(repository, VERSION, f"v{VERSION}", True)
    assert check_release.previous_release_ref(repository, VERSION) == "v1.2.2"


@pytest.mark.parametrize("broken", [None, "metadata", "extra-wheel", "missing-sdist"])
def test_built_artifact_identity(tmp_path: Path, broken: str | None) -> None:
    """Validate real archives and reject stale, missing, or mislabeled packages.

    :param tmp_path: Distribution directory.
    :param broken: Optional artifact fault.
    :return: None.
    """
    write_artifacts(
        tmp_path, metadata_version="1.2.2" if broken == "metadata" else str(VERSION)
    )
    if broken == "extra-wheel":
        (tmp_path / "stale.whl").touch()
    elif broken == "missing-sdist":
        (tmp_path / SDIST_NAME).unlink()
    if broken is None:
        check_release.validate_artifacts(tmp_path, VERSION)
    else:
        with pytest.raises(ValueError):
            check_release.validate_artifacts(tmp_path, VERSION)


@pytest.mark.parametrize("matching", [True, False])
def test_pypi_retry_checks_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, matching: bool
) -> None:
    """Only identical already-published files may be skipped during retries.

    :param tmp_path: Distribution directory.
    :param monkeypatch: Fixture for replacing network access.
    :param matching: Whether the remote checksum matches the local file.
    :return: None.
    """
    write_artifacts(tmp_path)
    digest = hashlib.sha256((tmp_path / WHEEL_NAME).read_bytes()).hexdigest()
    payload = {
        "urls": [
            {
                "filename": WHEEL_NAME,
                "digests": {"sha256": digest if matching else "different"},
            }
        ]
    }
    monkeypatch.setattr(
        check_release,
        "urlopen",
        lambda *args, **kwargs: BytesIO(json.dumps(payload).encode()),
    )
    if matching:
        check_release.verify_pypi_artifacts(tmp_path, VERSION)
    else:
        with pytest.raises(ValueError, match="different bytes"):
            check_release.verify_pypi_artifacts(tmp_path, VERSION)


@pytest.mark.parametrize("status", [404, 503])
def test_pypi_absence_and_network_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    """Treat only HTTP 404 as a version that has not been published.

    :param tmp_path: Distribution directory.
    :param monkeypatch: Fixture for replacing network access.
    :param status: Simulated PyPI HTTP response code.
    :return: None.
    """

    def unavailable(*args: object, **kwargs: object) -> BytesIO:
        """Raise the configured HTTP error instead of accessing PyPI."""
        raise HTTPError("https://pypi.org", status, "test", Message(), None)

    monkeypatch.setattr(check_release, "urlopen", unavailable)
    if status == 404:
        check_release.verify_pypi_artifacts(tmp_path, VERSION)
    else:
        with pytest.raises(HTTPError):
            check_release.verify_pypi_artifacts(tmp_path, VERSION)


def test_quality_checks_select_changed_python_only(repository: Path) -> None:
    """Include staged and untracked Python changes while excluding deleted files.

    :param repository: Temporary Git history.
    :return: None.
    """
    (repository / "kept.py").write_text("print('changed')\n")
    git(repository, "add", "kept.py")
    (repository / "new file.py").write_text("pass\n")
    (repository / "notes.md").write_text("Changed documentation\n")
    (repository / "deleted.py").unlink()
    assert changed_python_files(repository, "v1.2.2") == ["kept.py", "new file.py"]


@pytest.mark.parametrize("prerelease", [False, True])
def test_cli_writes_release_outputs(
    release_root: Path, repository: Path, prerelease: bool
) -> None:
    """Exercise tag verification and the output contract consumed by Actions.

    :param release_root: Prepared metadata in the temporary repository.
    :param repository: Git history sharing release_root's temporary directory.
    :param prerelease: Whether to prepare a release candidate.
    :return: None.
    """
    version = f"{VERSION}rc1" if prerelease else str(VERSION)
    if prerelease:
        for name in ("pyproject.toml", "uv.lock", "CHANGELOG.md"):
            path = release_root / name
            path.write_text(path.read_text().replace(str(VERSION), version))
        git(repository, "add", ".")
        git(repository, "commit", "-m", "release candidate")
    git(repository, "tag", f"v{version}")
    notes = release_root / "notes.md"
    output = release_root / "output.txt"
    app = typer.Typer()
    app.command()(check_release.main)
    result = CliRunner().invoke(
        app,
        [
            "--root",
            str(release_root),
            "--tag",
            f"v{version}",
            "--require-tag",
            "--notes-file",
            str(notes),
            "--github-output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    assert notes.read_text() == "Release notes.\n"
    assert output.read_text() == (
        f"version={version}\nbase_ref=v1.2.2\nprerelease={str(prerelease).lower()}\n"
    )
