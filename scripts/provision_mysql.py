"""Create the Harness database and application account through SSH.

The remote administrator is used only for provisioning. Passwords are
generated locally, saved outside Git and never passed as command arguments.
Existing accounts are never silently reset or granted access from new hosts.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import ipaddress
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

from apps.web.api.adapter.mysql import MySQLClient, MySQLConfig, MySQLError


def _ssh(target: str, command: str, sql: str | None = None) -> str:
    try:
        result = subprocess.run(
            ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", target, command],
            input=sql, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=45, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError("Não foi possível executar o provisionamento por SSH.") from None
    if result.returncode:
        # MariaDB errors can echo SQL containing the authentication verifier.
        raise RuntimeError(
            f"O comando remoto falhou (código {result.returncode}). Confira o acesso SSH e as permissões administrativas."
        )
    return result.stdout.strip()


def _sql(target: str, statement: str) -> str:
    return _ssh(target, "mysql --batch --skip-column-names", statement)


def _protect_config(path: Path) -> None:
    if os.name != "nt":
        path.chmod(0o600)
        return
    domain = os.environ.get("USERDOMAIN", "")
    account = f"{domain}\\{getpass.getuser()}" if domain else getpass.getuser()
    result = subprocess.run(
        ["icacls", str(path), "/inheritance:r", "/grant:r", f"{account}:(F)",
         "/grant:r", "*S-1-5-18:(F)", "/grant:r", "*S-1-5-32-544:(F)"],
        capture_output=True, check=False,
    )
    if result.returncode:
        raise RuntimeError("Não foi possível restringir as permissões do arquivo de credenciais.")


def provision(target: str, path: Path, *, allow_current_host: bool = False) -> dict:
    connection = _ssh(target, "printenv SSH_CONNECTION").split()
    if len(connection) != 4:
        raise RuntimeError("O servidor não informou o endereço do cliente SSH.")
    client_host = str(ipaddress.ip_address(connection[0]))
    server_host = str(ipaddress.ip_address(connection[2]))
    accounts = _sql(target, "SELECT Host FROM mysql.user WHERE User='harness_app';")
    existing_hosts = set(accounts.splitlines()) if accounts else set()
    if path.exists():
        config = MySQLConfig.from_env({}, config_file=path)
        if config.host != server_host or config.database != "harness_eng" or config.user != "harness_app":
            raise RuntimeError("A configuração local aponta para outro banco, servidor ou usuário.")
    else:
        if existing_hosts:
            raise RuntimeError("O usuário harness_app já existe. Recupere sua configuração local antes de provisionar.")
        config = MySQLConfig(
            host=server_host, port=3306, database="harness_eng",
            user="harness_app", password=secrets.token_urlsafe(36),
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as output:
            json.dump(
                {"host": config.host, "port": config.port, "database": config.database,
                 "user": config.user, "password": config.password},
                output, ensure_ascii=False, indent=2,
            )
            output.write("\n")
    _protect_config(path)
    if existing_hosts and client_host not in existing_hosts and not allow_current_host:
        raise RuntimeError(
            "O usuário harness_app está vinculado a outro host. "
            "Use --allow-current-host para autorizar o IP atual informado pelo servidor SSH."
        )
    verifier = "*" + hashlib.sha1(hashlib.sha1(config.password.encode()).digest()).hexdigest().upper()
    if client_host in existing_hosts:
        matching = _sql(
            target,
            f"SELECT COUNT(*) FROM mysql.user WHERE User='harness_app' "
            f"AND Host='{client_host}' AND Password='{verifier}' AND plugin IN ('','mysql_native_password');",
        )
        if matching != "1":
            raise RuntimeError("As credenciais locais não correspondem à conta existente; nenhuma senha foi alterada.")
    elif existing_hosts:
        # Adding a host is an explicit administrative operation. Prove that
        # this local credential belongs to an existing account before copying
        # its verifier to the current, exact SSH client address.
        matching = _sql(
            target,
            "SELECT COUNT(*) FROM mysql.user WHERE User='harness_app' "
            f"AND Password='{verifier}' AND plugin IN ('','mysql_native_password');",
        )
        if not matching.isdecimal() or int(matching) < 1:
            raise RuntimeError(
                "As credenciais locais não correspondem a uma conta existente; nenhum host foi adicionado."
            )
    # Escape '_' in the GRANT database pattern, so only this exact schema matches.
    grant_database = config.database.replace("_", chr(92) + "_")
    statements = (
        f"CREATE DATABASE IF NOT EXISTS `{config.database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\n"
    )
    if client_host not in existing_hosts:
        statements += (
            f"CREATE USER 'harness_app'@'{client_host}' IDENTIFIED BY PASSWORD '{verifier}';\n"
        )
    statements += (
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON `{grant_database}`.* "
        f"TO 'harness_app'@'{client_host}';\n"
    )
    _sql(target, statements)
    status = MySQLClient(config).check_connection()
    status["allowed_host"] = client_host
    status["config_file"] = str(path.resolve())
    return status


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Criar banco e usuário do Harness Eng via SSH.")
    parser.add_argument("--ssh-target", default="root@192.168.85.2")
    parser.add_argument("--config", type=Path, default=Path(".harness-eng/mysql.json"))
    parser.add_argument(
        "--allow-current-host", action="store_true",
        help="Autorizar o IP atual do cliente SSH, preservando contas e credenciais existentes.",
    )
    args = parser.parse_args(argv)
    try:
        result = provision(args.ssh_target, args.config, allow_current_host=args.allow_current_host)
    except (MySQLError, RuntimeError, ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Provisionamento interrompido.", file=sys.stderr)
        return 130
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
