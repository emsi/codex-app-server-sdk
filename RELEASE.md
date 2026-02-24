# PyPI release checklist

This project uses `uv` and `hatchling`.

## 1. Bump version

Edit `pyproject.toml`:

- update `[project].version`

## 2. Build artifacts

From `codex-app-server-sdk/

```bash
uv sync --group dev
uv run python -m build
```

Artifacts are created in `dist/`:

- `*.tar.gz` (sdist)
- `*.whl` (wheel)

## 3. Validate package metadata and README rendering

```bash
uv run twine check dist/*
```

## 4. Optional: publish to TestPyPI first

```bash
uv run twine upload --repository testpypi dist/*
```

Install test build:

```bash
pip install -i https://test.pypi.org/simple/ codex-app-server-sdk
```

## 5. Publish to PyPI

```bash
uv run twine upload dist/*
```

## 6. Tag release in git

Example:

```bash
git tag v0.1.0
git push origin v0.1.0
```
