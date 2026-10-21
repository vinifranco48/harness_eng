"""Configured MySQL/MariaDB connections, without global shared sockets."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pymysql
from pymysql.connections import Connection
from pymysql.cursors import DictCursor


class MySQLError(RuntimeError):
    """Base exception for controlled MySQL adapter failures."""


class MySQLConfigurationError(MySQLError, ValueError):
    """Missing or invalid application database configuration."""


class MySQLConnectionError(MySQLError):
    """The application database could not be reached or authenticated."""


_ENV_FIELDS = {
    "MYSQL_HOST": "host",
    "MYSQL_PORT": "port",
    "MYSQL_DATABASE": "database",
    "MYSQL_USER": "user",
    "MYSQL_PASSWORD": "password",
    "MYSQL_CONNECT_TIMEOUT": "connect_timeout",
    "MYSQL_READ_TIMEOUT": "read_timeout",
    "MYSQL_WRITE_TIMEOUT": "write_timeout",
}


def _config_path(config_file: Path | None) -> Path:
    return Path(config_file) if config_file is not None else Path.cwd() / ".harness-eng" / "mysql.json"


def _integer(value: Any, name: str, maximum: int) -> int:
    if type(value) is int:
        number = value
    elif isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        number = int(value)
    else:
        raise MySQLConfigurationError(f"{name} deve ser um número inteiro.")
    if not 1 <= number <= maximum:
        raise MySQLConfigurationError(f"{name} deve estar entre 1 e {maximum}.")
    return number


@dataclass(frozen=True, repr=False)
class MySQLConfig:
    host: str
    database: str
    user: str
    password: str = field(repr=False)
    port: int = 3306
    connect_timeout: int = 10
    read_timeout: int = 30
    write_timeout: int = 30

    def __post_init__(self) -> None:
        for name in ("host", "database", "user"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
                raise MySQLConfigurationError(f"Configure um valor válido para {name}.")
            object.__setattr__(self, name, value.strip())
        if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", self.database):
            raise MySQLConfigurationError("O nome do banco deve conter até 64 letras, números ou underscores.")
        if not isinstance(self.password, str) or not self.password or "\x00" in self.password:
            raise MySQLConfigurationError("Configure MYSQL_PASSWORD ou password no arquivo de configuração.")
        for name, maximum in (("port", 65535), ("connect_timeout", 120), ("read_timeout", 3600), ("write_timeout", 3600)):
            object.__setattr__(self, name, _integer(getattr(self, name), name, maximum))

    @classmethod
    def is_configured(
        cls, config_file: Path | None = None, environ: Mapping[str, str] | None = None,
    ) -> bool:
        settings = os.environ if environ is None else environ
        return _config_path(config_file).exists() or any(key in settings for key in _ENV_FIELDS)

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None, *, config_file: Path | None = None,
    ) -> MySQLConfig:
        """Read private JSON, then apply explicit environment overrides.

        File values remain local to this config; nothing is copied into
        os.environ or into the environment inherited by provider processes.
        """
        path = _config_path(config_file)
        values: dict[str, Any] = {}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                raise MySQLConfigurationError("Não foi possível ler a configuração JSON do MySQL.") from None
            if not isinstance(loaded, dict) or set(loaded) - set(_ENV_FIELDS.values()):
                raise MySQLConfigurationError("A configuração MySQL contém campos inválidos.")
            values.update(loaded)
        settings = os.environ if environ is None else environ
        for variable, name in _ENV_FIELDS.items():
            if variable in settings:
                values[name] = settings[variable]
        missing = [name for name in ("host", "database", "user", "password") if not values.get(name)]
        if missing:
            raise MySQLConfigurationError("Configure os campos MySQL: " + ", ".join(missing) + ".")
        return cls(**values)

    def __repr__(self) -> str:
        return (
            f"MySQLConfig(host={self.host!r}, port={self.port}, "
            f"database={self.database!r}, user={self.user!r}, password=<redacted>)"
        )


class MySQLClient:
    """A connection factory safe to share across application workers.

    Each operation owns a separate socket. Successful transactions commit;
    failed operations roll back; sockets always close. Database creation is
    performed by the provisioning script, never by the web application.
    """

    def __init__(self, config: MySQLConfig) -> None:
        self.config = config

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None, *, config_file: Path | None = None,
    ) -> MySQLClient:
        return cls(MySQLConfig.from_env(environ, config_file=config_file))

    @contextmanager
    def connection(self) -> Iterator[Connection]:
        try:
            connection = pymysql.connect(
                host=self.config.host, port=self.config.port,
                user=self.config.user, password=self.config.password,
                database=self.config.database, charset="utf8mb4",
                cursorclass=DictCursor, autocommit=False, local_infile=False,
                connect_timeout=self.config.connect_timeout,
                read_timeout=self.config.read_timeout,
                write_timeout=self.config.write_timeout,
                init_command="SET time_zone = '+00:00'",
            )
        except (pymysql.MySQLError, OSError):
            raise MySQLConnectionError(
                "Não foi possível conectar ao MySQL. Confira endereço, credenciais e permissões de acesso."
            ) from None
        try:
            yield connection
            connection.commit()
        except BaseException:
            try:
                connection.rollback()
            except (pymysql.MySQLError, OSError):
                pass
            raise
        finally:
            if connection.open:
                connection.close()

    def check_connection(self) -> dict[str, Any]:
        """Authenticate and confirm the selected schema using a real query."""
        try:
            with self.connection() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 AS reachable, DATABASE() AS database_name, VERSION() AS server_version, "
                    "CURRENT_USER() AS account, @@character_set_connection AS charset"
                )
                row = cursor.fetchone()
        except (pymysql.MySQLError, OSError):
            raise MySQLConnectionError("O MySQL não concluiu a verificação da conexão.") from None
        if not row or row["reachable"] != 1 or row["database_name"] != self.config.database:
            raise MySQLConnectionError("O MySQL não confirmou o banco de dados configurado.")
        return {
            "connected": True,
            "host": self.config.host,
            "port": self.config.port,
            "database": row["database_name"],
            "user": row["account"],
            "server_version": row["server_version"],
            "charset": row["charset"],
        }
