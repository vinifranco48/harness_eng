from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import provision_mysql


class MySQLProvisionTests(unittest.TestCase):
    target = "root@192.168.85.2"
    current_host = "192.168.99.9"
    previous_host = "192.168.18.197"
    password = "private-password-never-printed"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "mysql.json"
        self.write_config()

    def write_config(self, **overrides):
        config = {
            "host": "192.168.85.2", "port": 3306, "database": "harness_eng",
            "user": "harness_app", "password": self.password,
        }
        config.update(overrides)
        self.path.write_text(json.dumps(config), encoding="utf-8")

    @contextlib.contextmanager
    def remote(self, existing_hosts: str, matching: str = "1"):
        with contextlib.ExitStack() as stack:
            ssh = stack.enter_context(patch.object(
                provision_mysql, "_ssh", return_value=f"{self.current_host} 50500 192.168.85.2 22",
            ))
            statements = []

            def execute_sql(target, statement):
                statements.append(statement)
                if statement.startswith("SELECT Host"):
                    return existing_hosts
                if statement.startswith("SELECT COUNT(*)"):
                    return matching
                return ""

            stack.enter_context(patch.object(provision_mysql, "_sql", side_effect=execute_sql))
            protect = stack.enter_context(patch.object(provision_mysql, "_protect_config"))
            client = stack.enter_context(patch.object(provision_mysql, "MySQLClient"))
            client.return_value.check_connection.return_value = {"connected": True}
            yield statements, ssh, protect, client

    def test_existing_current_host_is_idempotent_with_other_hosts_preserved(self):
        original_config = self.path.read_bytes()
        hosts = f"{self.previous_host}\n{self.current_host}"
        with self.remote(hosts) as (statements, ssh, protect, client):
            result = provision_mysql.provision(self.target, self.path)
        self.assertTrue(result["connected"])
        self.assertEqual(result["allowed_host"], self.current_host)
        ssh.assert_called_once_with(self.target, "printenv SSH_CONNECTION")
        protect.assert_called_once_with(self.path)
        client.return_value.check_connection.assert_called_once_with()
        metadata = next(sql for sql in statements if sql.startswith("SELECT COUNT(*)"))
        self.assertIn(f"Host='{self.current_host}'", metadata)
        self.assertIn("plugin IN ('','mysql_native_password')", metadata)
        mutation = statements[-1]
        self.assertNotIn("CREATE USER", mutation)
        self.assertNotIn("DROP USER", mutation)
        self.assertNotIn("ALTER USER", mutation)
        self.assertNotIn(self.previous_host, mutation)
        self.assertEqual(self.path.read_bytes(), original_config)

    def test_new_current_host_is_refused_by_default_without_mutation(self):
        with self.remote(self.previous_host) as (statements, _, _, client):
            with self.assertRaisesRegex(RuntimeError, "--allow-current-host") as failure:
                provision_mysql.provision(self.target, self.path)
        self.assertNotIn(self.password, str(failure.exception))
        self.assertEqual(len(statements), 1)
        self.assertTrue(statements[0].startswith("SELECT Host"))
        client.assert_not_called()

    def test_explicit_flag_adds_only_current_ip_with_matching_existing_credentials(self):
        original_config = self.path.read_bytes()
        with self.remote(self.previous_host, matching="2") as (statements, _, _, client):
            result = provision_mysql.provision(self.target, self.path, allow_current_host=True)
        self.assertEqual(result["allowed_host"], self.current_host)
        self.assertEqual(len(statements), 3)
        metadata, mutation = statements[1:]
        self.assertTrue(metadata.startswith("SELECT COUNT(*)"))
        self.assertIn("Password='*", metadata)
        self.assertIn("plugin IN ('','mysql_native_password')", metadata)
        self.assertIn(f"CREATE USER 'harness_app'@'{self.current_host}' IDENTIFIED BY PASSWORD", mutation)
        self.assertNotIn("CREATE USER IF NOT EXISTS", mutation)
        self.assertIn("GRANT SELECT, INSERT, UPDATE, DELETE ON `harness\\_eng`.*", mutation)
        self.assertIn(f"TO 'harness_app'@'{self.current_host}'", mutation)
        for forbidden in (self.previous_host, self.password, "ALTER USER", "DROP USER", " ON *.*", "@'%'", "GRANT ALL"):
            self.assertNotIn(forbidden, mutation)
        self.assertEqual(self.path.read_bytes(), original_config)
        client.return_value.check_connection.assert_called_once_with()

    def test_explicit_flag_refuses_new_host_when_credentials_do_not_match(self):
        for matching in ("0", "", "unexpected"):
            with self.subTest(matching=matching):
                with self.remote(self.previous_host, matching) as (statements, _, _, client):
                    with self.assertRaisesRegex(RuntimeError, "nenhum host foi adicionado") as failure:
                        provision_mysql.provision(self.target, self.path, allow_current_host=True)
                self.assertNotIn(self.password, str(failure.exception))
                self.assertEqual(len(statements), 2)
                self.assertTrue(all(sql.startswith("SELECT ") for sql in statements))
                client.assert_not_called()

    def test_existing_current_host_requires_its_own_matching_credentials(self):
        with self.remote(f"{self.previous_host}\n{self.current_host}", "0") as (statements, _, _, client):
            with self.assertRaisesRegex(RuntimeError, "nenhuma senha foi alterada"):
                provision_mysql.provision(self.target, self.path, allow_current_host=True)
        self.assertEqual(len(statements), 2)
        self.assertIn(f"Host='{self.current_host}'", statements[-1])
        client.assert_not_called()

    def test_configuration_for_another_server_database_or_user_is_refused(self):
        for override in ({"host": "192.168.85.3"}, {"database": "another_database"}, {"user": "another_user"}):
            with self.subTest(override=override):
                self.write_config(**override)
                with self.remote(self.previous_host) as (statements, _, protect, client):
                    with self.assertRaisesRegex(RuntimeError, "outro banco, servidor ou usuário"):
                        provision_mysql.provision(self.target, self.path, allow_current_host=True)
                self.assertEqual(len(statements), 1)
                protect.assert_not_called()
                client.assert_not_called()

    def test_flag_does_not_replace_missing_config_for_existing_accounts(self):
        self.path.unlink()
        with self.remote(self.previous_host) as (statements, _, _, client):
            with self.assertRaisesRegex(RuntimeError, "Recupere sua configuração local"):
                provision_mysql.provision(self.target, self.path, allow_current_host=True)
        self.assertEqual(len(statements), 1)
        self.assertFalse(self.path.exists())
        client.assert_not_called()

    def test_cli_passes_explicit_allow_current_host_flag(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                stdout = io.StringIO()
                argv = ["--config", str(self.path), "--ssh-target", self.target]
                if enabled:
                    argv.append("--allow-current-host")
                with patch.object(provision_mysql, "provision", return_value={"connected": True}) as provision:
                    with contextlib.redirect_stdout(stdout):
                        self.assertEqual(provision_mysql.main(argv), 0)
                provision.assert_called_once_with(self.target, self.path, allow_current_host=enabled)
                self.assertNotIn(self.password, stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
