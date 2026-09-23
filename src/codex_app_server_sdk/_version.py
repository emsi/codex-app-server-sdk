"""Read the SDK version from installed package metadata."""

from importlib.metadata import version
from typing import Final

DISTRIBUTION_NAME: Final = "codex-app-server-sdk"
__version__: str = version(DISTRIBUTION_NAME)
