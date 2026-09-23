# Releasing codex-app-server-sdk

The maintained procedure is in [the release guide](docs/releasing.md), also
published on the [documentation site](https://emsi.github.io/codex-app-server-sdk/releasing/).

Prepare the version and changelog locally, then push the reviewed version tag.
GitHub Actions validates, builds, publishes to PyPI, and creates a GitHub Release
with the same distributions. Do not manually upload packages before pushing the
tag: the tag already triggers publication.
