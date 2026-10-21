from __future__ import annotations

import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from apps.web.api.adapter.mysql import MySQLConfig, MySQLConfigurationError
from scripts import migrate_mysql


class MySQLMigrationTests(unittest.TestCase):
    def setUp(self):
        self.config = MySQLConfig(
            host="192.168.85.2", database="harness_eng", user="harness_app",
            password="private-password-must-not-leak",
        )
        self.path = Path(".harness-eng/mysql.json")

    def run_migration(self, output: str, returncode: int = 0):
        with patch.object(MySQLConfig, "from_env", return_value=self.config) as config_loader:
            with patch.object(migrate_mysql.subprocess, "run", return_value=SimpleNamespace(
                stdout=output, stderr=self.config.password, returncode=returncode,
            )) as process:
                result = migrate_mysql.migrate("root@192.168.85.2", self.path)
        config_loader.assert_called_once_with({}, config_file=self.path)
        return result, process

    def test_admin_batch_keeps_lock_ddl_and_record_in_one_connection_without_credentials(self):
        result, process = self.run_migration("__HARNESS_MIGRATION__|SUCCESS|001_create_analyses|applied\n")
        self.assertEqual(result["status"], "applied")
        process.assert_called_once()
        args, kwargs = process.call_args
        self.assertEqual(args[0][0], "ssh")
        self.assertIn("--database=harness_eng", args[0][-1])
        self.assertNotIn("--force", args[0][-1])
        self.assertIn("--skip-force", args[0][-1])
        self.assertIn("--skip-reconnect", args[0][-1])
        script = kwargs["input"]
        self.assertTrue(script.startswith("SET SESSION autocommit = 1;"))
        self.assertLess(script.index("GET_LOCK("), script.index("CREATE TABLE IF NOT EXISTS `schema_migrations`"))
        self.assertLess(script.index("FAIL|checksum"), script.index("CREATE TABLE IF NOT EXISTS `analyses`"))
        self.assertLess(script.index("FAIL|analyses_schema"), script.index("INSERT INTO `schema_migrations`"))
        self.assertLess(script.index("INSERT INTO `schema_migrations`"), script.index("RELEASE_LOCK("))
        self.assertIn("CREATE TEMPORARY TABLE", script)
        self.assertIn("UTC_TIMESTAMP(6)", script)
        for forbidden in (self.config.password, "GRANT ", "CREATE USER", "ALTER USER", "DROP TABLE"):
            self.assertNotIn(forbidden, str(process.call_args))

    def test_existing_matching_migration_is_reported_unchanged_without_reinserting_record(self):
        result, process = self.run_migration("__HARNESS_MIGRATION__|SUCCESS|001_create_analyses|unchanged\n")
        self.assertEqual(result["status"], "unchanged")
        script = process.call_args.kwargs["input"]
        self.assertIn("WHERE @harness_previous IS NULL", script)
        self.assertIn("@harness_previous IS NULL OR BINARY", script)

    def test_checksum_drift_is_rejected_even_if_remote_exit_code_is_zero(self):
        with self.assertRaisesRegex(migrate_mysql.MigrationError, "checksum"):
            self.run_migration("__HARNESS_MIGRATION__|FAIL|checksum\n")

    def test_lock_contention_is_rejected(self):
        with self.assertRaisesRegex(migrate_mysql.MigrationError, "lock administrativo"):
            self.run_migration("__HARNESS_MIGRATION__|FAIL|lock\n", 1)

    def test_existing_or_partially_created_table_requires_full_schema_validation(self):
        with self.assertRaisesRegex(migrate_mysql.MigrationError, "diverge do schema esperado"):
            self.run_migration("__HARNESS_MIGRATION__|FAIL|analyses_schema\n", 1)
        script = migrate_mysql.build_script(self.config, migrate_mysql.load_migration())
        self.assertIn("COLUMN_TYPE = 'datetime(6)'", script)
        self.assertIn("COLLATION_NAME = 'ascii_bin'", script)
        self.assertIn("COLUMN_DEFAULT IS NULL AND EXTRA = ''", script)
        self.assertIn("SEQ_IN_INDEX = 2 AND COLUMN_NAME = 'id'", script)
        self.assertIn("FAIL|analyses_missing", script)

    def test_remote_errors_and_missing_success_do_not_echo_secrets(self):
        for output, code in ((self.config.password, 1), ("", 0),
                             ("__HARNESS_MIGRATION__|SUCCESS|001_create_analyses|applied\n", 1)):
            with self.subTest(code=code, has_success="SUCCESS" in output):
                with self.assertRaises(migrate_mysql.MigrationError) as failure:
                    self.run_migration(output, code)
                self.assertNotIn(self.config.password, str(failure.exception))

    def test_timeout_and_os_errors_are_sanitized(self):
        errors = (OSError(self.config.password),
                  subprocess.TimeoutExpired("ssh", 180, output=self.config.password, stderr=self.config.password))
        for error in errors:
            with self.subTest(error=type(error).__name__):
                with patch.object(MySQLConfig, "from_env", return_value=self.config):
                    with patch.object(migrate_mysql.subprocess, "run", side_effect=error):
                        with self.assertRaises(migrate_mysql.MigrationError) as failure:
                            migrate_mysql.migrate("root@192.168.85.2", self.path)
                self.assertNotIn(self.config.password, str(failure.exception))

    def test_configuration_errors_are_sanitized_without_starting_ssh(self):
        with patch.object(MySQLConfig, "from_env", side_effect=MySQLConfigurationError(self.config.password)):
            with patch.object(migrate_mysql.subprocess, "run") as process:
                with self.assertRaises(migrate_mysql.MigrationError) as failure:
                    migrate_mysql.migrate("root@192.168.85.2", self.path)
        self.assertNotIn(self.config.password, str(failure.exception))
        process.assert_not_called()

    def test_ssh_option_injection_is_rejected_before_loading_configuration(self):
        with patch.object(MySQLConfig, "from_env") as loader:
            for target in ("-oProxyCommand=unsafe", "-F@host", "root@host; echo unsafe"):
                with self.subTest(target=target), self.assertRaises(migrate_mysql.MigrationError):
                    migrate_mysql.migrate(target, self.path)
        loader.assert_not_called()

    def test_checksum_is_stable_across_checkout_newlines_and_changes_with_sql(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            migration = root / "001_create_analyses.sql"
            migration.write_bytes(b"SELECT 1;\r\n")
            windows = migrate_mysql.load_migration(root)
            migration.write_bytes(b"SELECT 1;\n")
            self.assertEqual(windows.checksum, migrate_mysql.load_migration(root).checksum)
            migration.write_bytes(b"SELECT 2;\n")
            self.assertNotEqual(windows.checksum, migrate_mysql.load_migration(root).checksum)

    def test_conditional_constant_selects_support_mariadb_10_3(self):
        guard = migrate_mysql._guard("@harness_lock = 1", "lock")
        self.assertIn("FROM DUAL WHERE NOT COALESCE", guard)
        self.assertIn("SELECT 1 FROM DUAL WHERE", guard)
        script = migrate_mysql.build_script(self.config, migrate_mysql.load_migration())
        self.assertIn("UTC_TIMESTAMP(6) FROM DUAL WHERE @harness_previous IS NULL", script)

    def test_cli_failure_returns_safe_error_without_traceback(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(migrate_mysql, "migrate", side_effect=migrate_mysql.MigrationError("Schema divergente.")):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = migrate_mysql.main([])
        self.assertEqual(code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue().strip(), "Schema divergente.")


if __name__ == "__main__":
    unittest.main()
