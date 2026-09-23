"""Validate release metadata, tag identity, notes, and built distributions."""

from __future__ import annotations

import re
import hashlib
import json
import subprocess
import tarfile
import tomllib
from email.parser import BytesParser
from pathlib import Path
from typing import Annotated
from urllib.error import HTTPError
from urllib.request import urlopen
from zipfile import ZipFile

import typer
from packaging.utils import (
    canonicalize_name,
    parse_sdist_filename,
    parse_wheel_filename,
)
from packaging.version import InvalidVersion, Version

ROOT = Path(__file__).resolve().parent.parent
PACKAGE_NAME = "codex-app-server-sdk"
TYPING_MARKER = "codex_app_server_sdk/py.typed"
PYPI_TIMEOUT_SECONDS = 30
HTTP_NOT_FOUND = 404


def read_release(root: Path) -> tuple[Version, str]:
    """Validate project and lock versions and extract the matching changelog entry.

    :param root: Repository containing pyproject.toml, uv.lock, and CHANGELOG.md.
    :return: Canonical package version and nonempty release notes.
    """
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    version = Version(project["version"])
    if project["name"] != PACKAGE_NAME:
        raise ValueError(f"Expected project name {PACKAGE_NAME}")
    if (
        str(version) != project["version"]
        or len(version.release) != 3
        or version.epoch
        or version.local
        or version.is_devrelease
    ):
        raise ValueError("Use a canonical X.Y.Z version, optionally with a/b/rc/post")
    packages = tomllib.loads((root / "uv.lock").read_text())["package"]
    locked = [p["version"] for p in packages if p["name"] == PACKAGE_NAME]
    if locked != [str(version)]:
        raise ValueError("Package version in uv.lock does not match pyproject.toml")
    changelog = (root / "CHANGELOG.md").read_text()
    entries = re.findall(
        rf"^## {re.escape(str(version))}\n(.*?)(?=^## |\Z)",
        changelog,
        flags=re.MULTILINE | re.DOTALL,
    )
    if len(entries) != 1 or not entries[0].strip():
        raise ValueError(f"CHANGELOG.md needs one nonempty '## {version}' section")
    return version, entries[0].strip() + "\n"


def git_output(root: Path, *args: str) -> str:
    """Read a Git result without invoking a shell.

    :param root: Git working tree.
    :param args: Git command arguments.
    :return: Stripped standard output, raising on failure.
    """
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def validate_tag(root: Path, version: Version, tag: str, require_tag: bool) -> None:
    """Require an exact version tag and optionally verify its checked-out commit.

    :param root: Git working tree.
    :param version: Expected package version.
    :param tag: Proposed or existing tag, including the v prefix.
    :param require_tag: Verify the existing tag resolves to HEAD.
    :return: None.
    """
    if tag != f"v{version}":
        raise ValueError(f"Tag {tag!r} must be v{version}")
    if require_tag and git_output(
        root, "rev-parse", f"refs/tags/{tag}^{{commit}}"
    ) != git_output(root, "rev-parse", "HEAD"):
        raise ValueError("Release tag does not point to the checked-out commit")


def previous_release_ref(root: Path, version: Version) -> str:
    """Choose the highest earlier version tag reachable from the release commit.

    :param root: Git working tree with complete tag history.
    :param version: Version being checked.
    :return: Previous release tag, or an empty tree for the first release.
    """
    candidates: dict[Version, str] = {}
    for tag in git_output(root, "tag", "--merged", "HEAD", "--list", "v*").splitlines():
        try:
            parsed = Version(tag.removeprefix("v"))
        except InvalidVersion:
            continue
        if tag == f"v{parsed}" and parsed < version:
            candidates[parsed] = tag
    if candidates:
        return candidates[max(candidates)]
    return subprocess.check_output(
        ["git", "hash-object", "-t", "tree", "--stdin"],
        cwd=root,
        input="",
        text=True,
    ).strip()


def artifact_metadata(path: Path) -> bytes:
    """Read top-level package metadata without extracting an archive.

    :param path: Wheel or compressed source distribution.
    :return: Raw metadata bytes.
    """
    if path.suffix == ".whl":
        with ZipFile(path) as wheel:
            metadata = [
                name
                for name in wheel.namelist()
                if name.endswith(".dist-info/METADATA")
            ]
            if len(metadata) != 1 or TYPING_MARKER not in wheel.namelist():
                raise ValueError(f"{path.name} lacks unique metadata or py.typed")
            return wheel.read(metadata[0])
    with tarfile.open(path) as archive:
        members = [
            member
            for member in archive.getmembers()
            if member.name.count("/") == 1 and member.name.endswith("/PKG-INFO")
        ]
        if len(members) != 1:
            raise ValueError(f"{path.name} lacks unique top-level PKG-INFO")
        stream = archive.extractfile(members[0])
        if stream is None:
            raise ValueError(f"{path.name} has unreadable PKG-INFO")
        with stream:
            return stream.read()


def validate_artifacts(directory: Path, version: Version) -> None:
    """Require one matching wheel and sdist, checking filenames and metadata.

    :param directory: Build output directory without older distributions.
    :param version: Expected project version.
    :return: None.
    """
    wheels = list(directory.glob("*.whl"))
    sdists = list(directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ValueError("Build directory must contain exactly one wheel and one sdist")
    for artifact in [*wheels, *sdists]:
        if artifact.suffix == ".whl":
            name, artifact_version, _, _ = parse_wheel_filename(artifact.name)
        else:
            name, artifact_version = parse_sdist_filename(artifact.name)
        metadata = BytesParser().parsebytes(artifact_metadata(artifact))
        if (
            canonicalize_name(name) != PACKAGE_NAME
            or artifact_version != version
            or metadata["Name"] != PACKAGE_NAME
            or metadata["Version"] != str(version)
        ):
            raise ValueError(
                f"{artifact.name} filename/metadata does not match {PACKAGE_NAME} {version}"
            )


def verify_pypi_artifacts(directory: Path, version: Version) -> None:
    """Allow retries only when already-published filenames have identical bytes.

    :param directory: Previously validated wheel and sdist directory.
    :param version: Version whose public PyPI metadata should be checked.
    :return: None; conflicting hashes or network failures abort publishing.
    """
    url = f"https://pypi.org/pypi/{PACKAGE_NAME}/{version}/json"
    try:
        with urlopen(url, timeout=PYPI_TIMEOUT_SECONDS) as response:
            published = json.load(response)
    except HTTPError as exc:
        if exc.code == HTTP_NOT_FOUND:
            return
        raise
    hashes = {
        entry["filename"]: entry["digests"]["sha256"] for entry in published["urls"]
    }
    for path in [*directory.glob("*.whl"), *directory.glob("*.tar.gz")]:
        if path.name not in hashes:
            continue
        with path.open("rb") as source:
            checksum = hashlib.file_digest(source, "sha256").hexdigest()
        if checksum != hashes[path.name]:
            raise ValueError(
                f"PyPI already has different bytes for {path.name}; reuse the original artifacts or choose a new version"
            )


def main(
    root: Annotated[Path, typer.Option(help="Repository root.")] = ROOT,
    tag: Annotated[
        str | None, typer.Option(help="Expected v-prefixed release tag.")
    ] = None,
    require_tag: Annotated[
        bool, typer.Option(help="Require this tag to point to HEAD.")
    ] = False,
    dist_dir: Annotated[
        Path | None, typer.Option(help="Validate wheel and sdist here.")
    ] = None,
    notes_file: Annotated[
        Path | None, typer.Option(help="Write this version's changelog entry.")
    ] = None,
    github_output: Annotated[
        Path | None, typer.Option(help="Append GitHub Actions outputs.")
    ] = None,
    check_pypi: Annotated[
        bool, typer.Option(help="Reject conflicting files already on PyPI.")
    ] = False,
) -> None:
    """Check a release locally or before publishing from a tagged GitHub run.

    :param root: Repository containing release inputs.
    :param tag: Optional proposed tag; required when require_tag is set.
    :param require_tag: Reject missing tags or tags pointing elsewhere.
    :param dist_dir: Optional directory of built distributions.
    :param notes_file: Optional destination for extracted changelog notes.
    :param github_output: Optional Actions output file for version, base, prerelease.
    :param check_pypi: Compare existing PyPI hashes before allowing a retry.
    :return: None; invalid releases exit unsuccessfully.
    """
    try:
        version, notes = read_release(root)
        if require_tag and tag is None:
            raise ValueError("--require-tag requires --tag")
        if tag is not None:
            validate_tag(root, version, tag, require_tag)
        if dist_dir is not None:
            validate_artifacts(dist_dir, version)
        if check_pypi:
            if dist_dir is None:
                raise ValueError("--check-pypi requires --dist-dir")
            verify_pypi_artifacts(dist_dir, version)
        if notes_file is not None:
            notes_file.write_text(notes)
        if github_output is not None:
            base = previous_release_ref(root, version)
            with github_output.open("a") as output:
                output.write(
                    f"version={version}\nbase_ref={base}\nprerelease={str(version.is_prerelease).lower()}\n"
                )
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"Release metadata valid: {PACKAGE_NAME} {version}")


if __name__ == "__main__":
    typer.run(main)
