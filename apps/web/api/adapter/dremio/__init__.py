"""Dremio REST API adapter."""

from .client import (
    DremioAPIError,
    DremioClient,
    DremioConfig,
    DremioConfigurationError,
    DremioError,
    DremioQueryError,
)

__all__ = [
    "DremioAPIError",
    "DremioClient",
    "DremioConfig",
    "DremioConfigurationError",
    "DremioError",
    "DremioQueryError",
]
