"""Apply reviewed MySQL migrations through an administrative SSH session.

The web application's account remains limited to DML. One mysql batch owns
the advisory lock, DDL, schema inspection and migration record. A failed batch
closes that connection and releases its lock. DDL itself is not transactional;
an interrupted run can resume only when the existing table matches its schema.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from apps.web.api.adapter.mysql import MySQLConfig, MySQLError


class MigrationError(RuntimeError):
    """A safe, public migration failure without remote output or credentials."""


@dataclass(frozen=True)
class Migration:
    version: str
    checksum: str
    sql: str


_VERSION = "001_create_analyses"
_MARKER = "__HARNESS_MIGRATION__"
_FAILURES = {
    "lock": "Outra migração está em execução ou o lock administrativo não está disponível.",
    "database": "O servidor não confirmou o banco configurado.",
    "history_schema": "A tabela schema_migrations existente diverge do formato esperado.",
    "checksum": "O checksum da migração aplicada diverge do arquivo local; nenhuma alteração foi autorizada.",
    "analyses_missing": "A migração está registrada, mas a tabela analyses está ausente.",
    "analyses_schema": "A tabela analyses diverge do schema esperado; a migração não foi registrada como concluída.",
}


def load_migration(directory: Path | None = None) -> Migration:
    root = directory or Path(__file__).resolve().parent.parent / "migrations" / "mysql"
    try:
        # Canonical newlines avoid false drift between Windows and Linux checkouts.
        sql = (root / f"{_VERSION}.sql").read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    except (OSError, UnicodeError):
        raise MigrationError("Não foi possível ler o arquivo da migração MySQL.") from None
    if not sql.strip():
        raise MigrationError("O arquivo da migração MySQL está vazio.")
    return Migration(_VERSION, hashlib.sha256(sql.encode("utf-8")).hexdigest(), sql)


def _guard(condition: str, reason: str) -> str:
    """Abort mysql batch on a failed assertion, without persistent procedures.

    The connection-local guard already contains id=1. Inserting that primary
    key again reliably fails on both MySQL and MariaDB. Batch mode has no
    --force, so no following DDL or success record executes after a failure.
    """
    return (
        f"SELECT '{_MARKER}|FAIL|{reason}' FROM DUAL WHERE NOT COALESCE(({condition}), 0);\n"
        "INSERT INTO `_harness_migration_guard` (`id`) "
        f"SELECT 1 FROM DUAL WHERE NOT COALESCE(({condition}), 0);\n"
    )


def _schema_condition(table: str, columns: tuple[tuple[str, str, str | None], ...], indexes: str) -> str:
    scope = f"TABLE_SCHEMA = DATABASE() AND TABLE_NAME = '{table}'"
    conditions = [
        f"(SELECT COUNT(*) FROM information_schema.TABLES WHERE {scope} "
        "AND TABLE_TYPE = 'BASE TABLE' AND ENGINE = 'InnoDB' "
        "AND TABLE_COLLATION = 'utf8mb4_unicode_ci') = 1",
        f"(SELECT COUNT(*) FROM information_schema.COLUMNS WHERE {scope}) = {len(columns)}",
    ]
    for position, (name, column_type, collation) in enumerate(columns, 1):
        collation_match = "COLLATION_NAME IS NULL" if collation is None else f"COLLATION_NAME = '{collation}'"
        conditions.append(
            f"(SELECT COUNT(*) FROM information_schema.COLUMNS WHERE {scope} "
            f"AND COLUMN_NAME = '{name}' AND ORDINAL_POSITION = {position} "
            f"AND COLUMN_TYPE = '{column_type}' AND IS_NULLABLE = 'NO' "
            f"AND COLUMN_DEFAULT IS NULL AND EXTRA = '' AND {collation_match}) = 1"
        )
    conditions.append(indexes)
    return "\nAND ".join(conditions)


def build_script(config: MySQLConfig, migration: Migration) -> str:
    """Build one administrative batch. Application credentials never enter it."""
    if migration.version != _VERSION or not re.fullmatch(r"[0-9a-f]{64}", migration.checksum):
        raise MigrationError("A identificação da migração MySQL é inválida.")
    lock_name = "harness_migrations:" + hashlib.sha256(config.database.encode()).hexdigest()[:32]
    history_scope = "TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_migrations'"
    analyses_scope = "TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'analyses'"
    history_schema = _schema_condition(
        "schema_migrations",
        (("version", "varchar(128)", "ascii_bin"), ("checksum", "char(64)", "ascii_bin"),
         ("applied_at", "datetime(6)", None)),
        f"(SELECT COUNT(*) FROM information_schema.STATISTICS WHERE {history_scope}) = 1 "
        f"AND (SELECT COUNT(*) FROM information_schema.STATISTICS WHERE {history_scope} "
        "AND INDEX_NAME = 'PRIMARY' AND NON_UNIQUE = 0 AND SEQ_IN_INDEX = 1 "
        "AND COLUMN_NAME = 'version' AND SUB_PART IS NULL AND INDEX_TYPE = 'BTREE') = 1",
    )
    analyses_schema = _schema_condition(
        "analyses",
        (("id", "char(36)", "ascii_bin"), ("title", "varchar(120)", "utf8mb4_unicode_ci"),
         ("created_at", "datetime(6)", None), ("updated_at", "datetime(6)", None)),
        f"(SELECT COUNT(*) FROM information_schema.STATISTICS WHERE {analyses_scope}) = 3 "
        f"AND (SELECT COUNT(*) FROM information_schema.STATISTICS WHERE {analyses_scope} "
        "AND INDEX_NAME = 'PRIMARY' AND NON_UNIQUE = 0 AND SEQ_IN_INDEX = 1 "
        "AND COLUMN_NAME = 'id' AND SUB_PART IS NULL AND INDEX_TYPE = 'BTREE') = 1 "
        f"AND (SELECT COUNT(*) FROM information_schema.STATISTICS WHERE {analyses_scope} "
        "AND INDEX_NAME = 'idx_analyses_updated_at_id' AND NON_UNIQUE = 1 AND SUB_PART IS NULL "
        "AND INDEX_TYPE = 'BTREE' AND ((SEQ_IN_INDEX = 1 AND COLUMN_NAME = 'updated_at') "
        "OR (SEQ_IN_INDEX = 2 AND COLUMN_NAME = 'id'))) = 2",
    )
    return (
        "SET SESSION autocommit = 1;\n"
        "SET SESSION time_zone = '+00:00';\n"
        "SET SESSION lock_wait_timeout = 30;\n"
        "SET SESSION innodb_lock_wait_timeout = 30;\n"
        "CREATE TEMPORARY TABLE `_harness_migration_guard` (`id` TINYINT NOT NULL PRIMARY KEY);\n"
        "INSERT INTO `_harness_migration_guard` VALUES (1);\n"
        f"SET @harness_lock = GET_LOCK('{lock_name}', 30);\n"
        + _guard("@harness_lock = 1", "lock")
        + _guard(f"DATABASE() = '{config.database}'", "database")
        + "CREATE TABLE IF NOT EXISTS `schema_migrations` (\n"
        "  `version` VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,\n"
        "  `checksum` CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,\n"
        "  `applied_at` DATETIME(6) NOT NULL\n"
        ") ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_unicode_ci;\n"
        + _guard(history_schema, "history_schema")
        + f"SET @harness_previous = (SELECT `checksum` FROM `schema_migrations` WHERE `version` = '{migration.version}');\n"
        + _guard(f"@harness_previous IS NULL OR BINARY @harness_previous = BINARY '{migration.checksum}'", "checksum")
        + _guard(
            "@harness_previous IS NULL OR "
            f"(SELECT COUNT(*) FROM information_schema.TABLES WHERE {analyses_scope}) = 1",
            "analyses_missing",
        )
        + migration.sql + "\n"
        + _guard(analyses_schema, "analyses_schema")
        + "INSERT INTO `schema_migrations` (`version`, `checksum`, `applied_at`) "
        f"SELECT '{migration.version}', '{migration.checksum}', UTC_TIMESTAMP(6) FROM DUAL "
        "WHERE @harness_previous IS NULL;\n"
        + f"SELECT CONCAT('{_MARKER}|SUCCESS|{migration.version}|', "
        "IF(@harness_previous IS NULL, 'applied', 'unchanged'));\n"
        + f"DO RELEASE_LOCK('{lock_name}');\n"
    )


def migrate(target: str, config_file: Path, *, migrations_dir: Path | None = None) -> dict[str, str]:
    if not re.fullmatch(r"(?:[A-Za-z0-9][A-Za-z0-9_.-]*@)?[A-Za-z0-9][A-Za-z0-9_.:\[\]-]*", target):
        raise MigrationError("Informe um destino SSH válido.")
    try:
        # Use this local file explicitly; unrelated MYSQL_* environment values
        # must not redirect an administrative operation to another database.
        config = MySQLConfig.from_env({}, config_file=config_file)
    except (MySQLError, OSError, ValueError):
        raise MigrationError("Não foi possível carregar a configuração local do MySQL.") from None
    migration = load_migration(migrations_dir)
    # Override option-file settings that could continue after an assertion
    # failure or reconnect without the original session's advisory lock.
    command = (
        "mysql --batch --raw --skip-column-names --unbuffered "
        f"--skip-force --skip-reconnect --database={config.database}"
    )
    try:
        result = subprocess.run(
            ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", target, command],
            input=build_script(config, migration), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=180, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise MigrationError("Não foi possível concluir a migração administrativa por SSH.") from None
    lines = result.stdout.splitlines()
    for line in lines:
        prefix = f"{_MARKER}|FAIL|"
        if line.startswith(prefix):
            reason = line[len(prefix):]
            if reason in _FAILURES:
                raise MigrationError(_FAILURES[reason])
    success = [line for line in lines if line.startswith(f"{_MARKER}|SUCCESS|{migration.version}|")]
    if result.returncode or len(success) != 1:
        # mysql errors can echo SQL or connection data. Never forward stderr,
        # stdout, exception reprs or the local configuration to the user.
        raise MigrationError("O servidor não confirmou a migração. Confira o acesso SSH e o schema existente.")
    status = success[0].rsplit("|", 1)[-1]
    if status not in {"applied", "unchanged"}:
        raise MigrationError("O servidor devolveu um resultado de migração inválido.")
    return {"database": config.database, "migration": migration.version, "status": status}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aplicar migrações MySQL revisadas usando administrador SSH.")
    parser.add_argument("--ssh-target", default="root@192.168.85.2")
    parser.add_argument("--config", type=Path, default=Path(".harness-eng/mysql.json"))
    args = parser.parse_args(argv)
    try:
        result = migrate(args.ssh_target, args.config)
    except MigrationError as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Migração interrompida; confirme o estado com uma nova execução.", file=sys.stderr)
        return 130
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
