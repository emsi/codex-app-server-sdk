"""Run independent quality checks in parallel on changed Python files."""

from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Annotated

import typer

ROOT = Path(__file__).resolve().parent.parent
CHECKS = (
    ("ruff", "check"),
    ("black", "--check"),
    ("mypy",),
    ("basedpyright", "--level", "error"),
)


def changed_python_files(root: Path, base: str) -> list[str]:
    """Find existing changed Python files, including staged and untracked work.

    :param root: Git working tree.
    :param base: Commit, tag, or tree used as the comparison baseline.
    :return: Sorted repository-relative filenames.
    """
    if not base or not base.strip("0"):
        raise ValueError("A valid comparison base is required")
    tree = subprocess.check_output(
        ["git", "rev-parse", "--verify", "--end-of-options", f"{base}^{{tree}}"],
        cwd=root,
        text=True,
    ).strip()
    changed = subprocess.check_output(
        ["git", "diff", "--name-only", "-z", "--diff-filter=ACMRT", tree, "--", "*.py"],
        cwd=root,
    )
    untracked = subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard", "-z", "--", "*.py"],
        cwd=root,
    )
    paths = set((changed + untracked).decode().split("\0"))
    return sorted(path for path in paths if path and (root / path).is_file())


def run_check(command: tuple[str, ...], *, root: Path, files: list[str]) -> int:
    """Run one check against explicit paths without modifying them.

    :param command: Tool and check arguments.
    :param root: Working tree with a synced development environment.
    :param files: Python paths selected for checking.
    :return: Tool exit status.
    """
    return subprocess.run(
        ["uv", "run", "--no-sync", *command, *files], cwd=root, check=False
    ).returncode


def main(
    base: Annotated[str, typer.Option(help="Compare against this commit or tag.")],
    root: Annotated[Path, typer.Option(help="Repository root.")] = ROOT,
) -> None:
    """Run Ruff, Black, mypy, and basedpyright on changed files in parallel.

    :param base: Required comparison baseline; releases use the previous tag.
    :param root: Repository with its development dependencies installed.
    :return: None; any failed check produces an unsuccessful exit status.
    """
    try:
        files = changed_python_files(root, base)
    except (ValueError, subprocess.CalledProcessError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not files:
        typer.echo("No changed Python files to check")
        return
    typer.echo("Checking: " + ", ".join(files))
    with ThreadPoolExecutor(max_workers=len(CHECKS)) as pool:
        statuses = list(pool.map(partial(run_check, root=root, files=files), CHECKS))
    if any(statuses):
        raise typer.Exit(1)


if __name__ == "__main__":
    typer.run(main)
