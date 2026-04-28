"""schools_sunbeds — analysis package for the NE schools × sunbeds study."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("schools-sunbeds")
except PackageNotFoundError:  # editable install before metadata is built
    __version__ = "0.0.0+local"

__all__ = ["__version__"]
