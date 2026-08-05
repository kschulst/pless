"""pless — self-hosted Paperless-ngx on hardware you own."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("pless")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0+unknown"
