"""MySQL/MariaDB access for the application's persistent data."""

from .client import (
    MySQLClient,
    MySQLConfig,
    MySQLConfigurationError,
    MySQLConnectionError,
    MySQLError,
)

__all__ = [
    "MySQLClient", "MySQLConfig", "MySQLConfigurationError",
    "MySQLConnectionError", "MySQLError",
]
