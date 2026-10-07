"""Portuguese CLI for the standalone harness authentication layer."""

from __future__ import annotations

import argparse
import json
import math
import sys

from .domain.auth import AuthResult, AuthState, AuthStatus, Provider
from .services.auth import AuthManager


def _timeout(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Informe um número positivo de segundos.") from exc
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("Informe um número positivo e finito de segundos.")
    return number


def _show_status(status: AuthStatus) -> None:
    labels = {
        AuthState.CONNECTED: "conectado", AuthState.DISCONNECTED: "desconectado",
        AuthState.MISSING_CLI: "CLI ausente", AuthState.ERROR: "erro", AuthState.TIMEOUT: "timeout",
    }
    method = f" ({status.auth_method})" if status.auth_method else ""
    print(f"{status.provider.value:7} {labels[status.state]}{method} — {status.message}")


def _show_result(result: AuthResult) -> int:
    print(result.message)
    _show_status(result.status)
    return 0 if result.success else 1


def _setup(manager: AuthManager) -> int:
    if not sys.stdin.isatty():
        print("Use setup em um terminal interativo, ou use login claude/login codex.", file=sys.stderr)
        return 2
    while True:
        print("\nHarness Eng · autenticação\n")
        for provider in Provider:
            _show_status(manager.status(provider))
        print("\n1. Login Claude\n2. Login Codex (navegador)\n3. Login Codex (código de dispositivo)")
        print("4. Logout Claude\n5. Logout Codex\n6. Atualizar status\n0. Sair")
        try:
            choice = input("\nEscolha: ").strip()
        except EOFError:
            return 0
        if choice == "0":
            return 0
        if choice in {"1", "2", "3"}:
            _show_result(manager.login(Provider.CLAUDE if choice == "1" else Provider.CODEX, device_auth=choice == "3"))
        elif choice in {"4", "5"}:
            _show_result(manager.logout(Provider.CLAUDE if choice == "4" else Provider.CODEX))
        elif choice != "6":
            print("Escolha uma opção de 0 a 6.")


def main(argv: list[str] | None = None) -> int:
    # Windows redirected streams otherwise use the legacy ANSI code page.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Login de Claude Code e Codex para Harness Eng.")
    commands = parser.add_subparsers(dest="command")
    serve = commands.add_parser("serve", help="Abrir a aplicação web local")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--auth-home", help="Pasta opcional para contas isoladas")
    setup = commands.add_parser("setup", help="Menu interativo de autenticação")
    status = commands.add_parser("status", help="Consultar os logins salvos nos CLIs")
    status.add_argument("provider", nargs="?", choices=[p.value for p in Provider])
    status.add_argument("--json", action="store_true", help="Retornar apenas JSON com metadados de status")
    status.add_argument("--timeout", type=_timeout, default=15)
    login = commands.add_parser("login", help="Login pelo CLI do provedor")
    login.add_argument("provider", choices=[p.value for p in Provider])
    login.add_argument("--device-auth", action="store_true", help="Usar código de dispositivo (Codex)")
    login.add_argument("--force", action="store_true", help="Iniciar novo login mesmo com um login salvo")
    login.add_argument("--timeout", type=_timeout, default=600)
    logout = commands.add_parser("logout", help="Sair da conta salva no CLI")
    logout.add_argument("provider", choices=[p.value for p in Provider])
    logout.add_argument("--timeout", type=_timeout, default=60)
    for command in (setup, status, login, logout):
        command.add_argument("--auth-home", help="Pasta para logins isolados; padrão: configuração atual dos CLIs")
    args = parser.parse_args(argv)
    if args.command in {None, "serve"}:
        import uvicorn
        from .main import create_app

        port = getattr(args, "port", 8080)
        if not 1 <= port <= 65535:
            parser.error("A porta deve estar entre 1 e 65535.")
        print(f"Harness Eng: http://localhost:{port}", flush=True)
        uvicorn.run(
            create_app(AuthManager(auth_home=getattr(args, "auth_home", None))),
            host="127.0.0.1", port=port, log_level="warning",
        )
        return 0
    if args.command == "login" and args.device_auth and args.provider != "codex":
        parser.error("--device-auth só pode ser usado com codex.")
    manager = AuthManager(
        auth_home=getattr(args, "auth_home", None),
        status_timeout=args.timeout if args.command == "status" else 15,
    )
    try:
        if args.command == "setup":
            return _setup(manager)
        if args.command == "status":
            providers = [Provider(args.provider)] if getattr(args, "provider", None) else list(Provider)
            statuses = [manager.status(provider) for provider in providers]
            if getattr(args, "json", False):
                print(json.dumps([s.to_dict() for s in statuses], ensure_ascii=False, indent=2))
            else:
                for current in statuses:
                    _show_status(current)
                if args.command is None:
                    print("\nUse: harness-eng setup | login claude | login codex | status | logout <provedor>")
            return 0 if all(s.connected for s in statuses) else 1
        if args.command == "login":
            return _show_result(manager.login(args.provider, device_auth=args.device_auth, force=args.force, timeout=args.timeout))
        return _show_result(manager.logout(args.provider, timeout=args.timeout))
    except KeyboardInterrupt:
        print("\nOperação interrompida.", file=sys.stderr)
        return 130
    except (OSError, ValueError):
        print("Não foi possível concluir a operação. Verifique a configuração e as permissões.", file=sys.stderr)
        return 1
