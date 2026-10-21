from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymysql

from apps.web.api.adapter.mysql.client import (
    MySQLClient, MySQLConfig, MySQLConfigurationError, MySQLConnectionError,
)


class MySQLClientTests(unittest.TestCase):
    def setUp(self):
        self.config = MySQLConfig(host="db.internal", database="harness_eng", user="harness_app", password="test-private")
        self.client = MySQLClient(self.config)

    def test_config_repr_redacts_password(self):
        self.assertNotIn(self.config.password, repr(self.config))

    def test_config_file_and_environment_overrides(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "mysql.json"
            path.write_text(json.dumps({"host": "from-file", "database": "harness_eng",
                                        "user": "harness_app", "password": "test-private"}), encoding="utf-8")
            config = MySQLConfig.from_env({"MYSQL_HOST": "override", "MYSQL_PORT": "3307"}, config_file=path)
            self.assertEqual((config.host, config.port, config.password), ("override", 3307, "test-private"))
            self.assertTrue(MySQLConfig.is_configured(path, {}))
            path.write_text('{"unexpected": true}', encoding="utf-8")
            with self.assertRaises(MySQLConfigurationError):
                MySQLConfig.from_env({}, config_file=path)

    def test_invalid_config_and_missing_fields(self):
        for values in ({"database": "bad-name"}, {"port": 0}, {"port": True}, {"password": ""}):
            with self.subTest(values=values), self.assertRaises(MySQLConfigurationError):
                MySQLConfig(**{**vars(self.config), **values})
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "absent.json"
            self.assertFalse(MySQLConfig.is_configured(path, {}))
            with self.assertRaises(MySQLConfigurationError):
                MySQLConfig.from_env({}, config_file=path)

    @patch("apps.web.api.adapter.mysql.client.pymysql.connect")
    def test_success_commits_closes_and_uses_safe_connection_options(self, connect):
        connection = connect.return_value
        with self.client.connection() as value:
            self.assertIs(value, connection)
        connection.commit.assert_called_once()
        connection.rollback.assert_not_called()
        connection.close.assert_called_once()
        options = connect.call_args.kwargs
        self.assertFalse(options["autocommit"])
        self.assertFalse(options["local_infile"])
        self.assertEqual(options["init_command"], "SET time_zone = '+00:00'")
        self.assertEqual(options["charset"], "utf8mb4")

    @patch("apps.web.api.adapter.mysql.client.pymysql.connect")
    def test_failure_rolls_back_and_closes(self, connect):
        connection = connect.return_value
        with self.assertRaises(ValueError):
            with self.client.connection():
                raise ValueError("invalid operation")
        connection.rollback.assert_called_once()
        connection.commit.assert_not_called()
        connection.close.assert_called_once()

    @patch("apps.web.api.adapter.mysql.client.pymysql.connect")
    def test_commit_failure_rolls_back_and_closes(self, connect):
        connection = connect.return_value
        connection.commit.side_effect = pymysql.OperationalError(2006, "lost")
        with self.assertRaises(pymysql.OperationalError):
            with self.client.connection():
                pass
        connection.rollback.assert_called_once()
        connection.close.assert_called_once()

    @patch("apps.web.api.adapter.mysql.client.pymysql.connect")
    def test_connection_failure_hides_credentials(self, connect):
        connect.side_effect = pymysql.OperationalError(1045, "test-private")
        with self.assertRaises(MySQLConnectionError) as failure:
            with self.client.connection():
                pass
        self.assertNotIn("test-private", str(failure.exception))


if __name__ == "__main__":
    unittest.main()
