"""Authenticated REST client for a Dremio Software instance."""

from __future__ import annotations

import math
import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx


class DremioError(RuntimeError):
    """Base exception for Dremio connection and query failures."""


class DremioConfigurationError(DremioError, ValueError):
    """Dremio connection settings are missing or invalid."""


class DremioAPIError(DremioError):
    """Dremio rejected a request or returned an invalid response."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class DremioQueryError(DremioAPIError):
    """A submitted Dremio job failed or did not finish in time."""


@dataclass(frozen=True, repr=False)
class DremioConfig:
    """Dremio REST settings. The access token is never included in repr."""

    base_url: str
    access_token: str = field(repr=False)
    request_timeout: float = 10.0
    query_timeout: float = 120.0
    poll_interval: float = 0.5

    def __post_init__(self) -> None:
        raw_url = self.base_url.strip().rstrip("/")
        parsed = urlsplit(raw_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise DremioConfigurationError(
                "DREMIO_BASE_URL deve ser a URL HTTP(S) da instância, sem credenciais, query ou fragmento."
            )
        token = self.access_token.strip()
        if not token or "\r" in token or "\n" in token:
            raise DremioConfigurationError("Configure um token de acesso Dremio válido.")
        timeouts = (self.request_timeout, self.query_timeout, self.poll_interval)
        if any(not math.isfinite(value) or value <= 0 for value in timeouts):
            raise DremioConfigurationError("Os timeouts do Dremio devem ser positivos e finitos.")

        base_path = parsed.path.rstrip("/")
        if base_path.lower().endswith("/api/v3"):
            base_path = base_path[:-len("/api/v3")]
        normalized = urlunsplit((parsed.scheme, parsed.netloc, base_path, "", ""))
        object.__setattr__(self, "base_url", normalized.rstrip("/"))
        object.__setattr__(self, "access_token", token)

    @property
    def api_url(self) -> str:
        return f"{self.base_url}/api/v3"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> DremioConfig:
        """Load credentials from DREMIO_BASE_URL and DREMIO_ACCESS_TOKEN."""
        settings = os.environ if environ is None else environ
        base_url = settings.get("DREMIO_BASE_URL", "").strip()
        access_token = settings.get("DREMIO_ACCESS_TOKEN", "").strip()
        missing = [
            name for name, value in (
                ("DREMIO_BASE_URL", base_url),
                ("DREMIO_ACCESS_TOKEN", access_token),
            ) if not value
        ]
        if missing:
            raise DremioConfigurationError(
                "Configure as variáveis: " + ", ".join(missing) + "."
            )
        return cls(base_url=base_url, access_token=access_token)

    def __repr__(self) -> str:
        return f"DremioConfig(base_url={self.base_url!r}, access_token=<redacted>)"


class DremioClient:
    """Connect to Dremio Software through its version 3 REST API.

    Supply a Dremio Personal Access Token (PAT) or OAuth access token. The
    token is sent as a Bearer token and is never added to error messages.
    """

    def __init__(self, config: DremioConfig) -> None:
        self.config = config
        self._http = httpx.Client(
            base_url=f"{config.api_url}/",
            headers={
                "Authorization": f"Bearer {config.access_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(config.request_timeout),
        )

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> DremioClient:
        return cls(DremioConfig.from_env(environ))

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> DremioClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int] | None = None,
        json_body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = self._http.request(method, path, params=params, json=json_body)
        except httpx.TimeoutException as exc:
            raise DremioAPIError("Tempo esgotado ao aguardar o Dremio.") from exc
        except httpx.RequestError as exc:
            raise DremioAPIError("Não foi possível conectar à API do Dremio.") from exc

        if not response.is_success:
            messages = {
                401: "Token do Dremio inválido ou expirado.",
                403: "A conta não tem permissão para esta operação no Dremio.",
                404: "Endpoint ou recurso não encontrado no Dremio.",
            }
            message = messages.get(response.status_code, "A API do Dremio recusou a solicitação.")
            raise DremioAPIError(message, status_code=response.status_code)
        if response.status_code == 204 or not response.content:
            return {}
        try:
            payload = response.json()
        except ValueError as exc:
            raise DremioAPIError("A API do Dremio retornou uma resposta JSON inválida.") from exc
        if not isinstance(payload, dict):
            raise DremioAPIError("A API do Dremio retornou um formato de resposta inesperado.")
        return payload

    def catalog(self) -> dict[str, Any]:
        """Return the current user's root catalog from Dremio."""
        return self._request("GET", "catalog")

    def check_connection(self) -> dict[str, Any]:
        """Verify the endpoint and token by reading the root catalog."""
        catalog = self.catalog()
        entries = catalog.get("data", [])
        if not isinstance(entries, list):
            raise DremioAPIError("A API do Dremio retornou um catálogo inválido.")
        return {
            "connected": True,
            "endpoint": self.config.base_url,
            "catalog_entries": len(entries),
        }

    def query(
        self,
        sql: str,
        *,
        context: Sequence[str] | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Run SQL and fetch one bounded page of results (up to 500 rows)."""
        if not isinstance(sql, str) or not sql.strip():
            raise ValueError("Informe uma consulta SQL.")
        if not 1 <= limit <= 500:
            raise ValueError("O limite de resultados deve estar entre 1 e 500.")
        if context is not None and (
            isinstance(context, (str, bytes))
            or any(not isinstance(item, str) or not item.strip() for item in context)
        ):
            raise ValueError("O contexto da consulta deve conter nomes válidos do catálogo.")

        body: dict[str, Any] = {"sql": sql.strip()}
        if context:
            body["context"] = list(context)
        submitted = self._request("POST", "sql", json_body=body)
        job_id = submitted.get("id")
        if not isinstance(job_id, str) or not job_id:
            raise DremioAPIError("A API do Dremio não retornou o identificador da consulta.")

        deadline = time.monotonic() + self.config.query_timeout
        quoted_job_id = quote(job_id, safe="")
        while True:
            job = self._request("GET", f"job/{quoted_job_id}")
            state = job.get("jobState")
            if state == "COMPLETED":
                break
            if state in {"FAILED", "CANCELED"}:
                message = job.get("errorMessage")
                if not isinstance(message, str) or not message.strip():
                    message = f"A consulta Dremio terminou com estado {state}."
                raise DremioQueryError(message[:1000])
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                try:
                    self._request("POST", f"job/{quoted_job_id}/cancel")
                except DremioAPIError:
                    pass
                raise DremioQueryError("Tempo esgotado aguardando a consulta no Dremio.")
            time.sleep(min(self.config.poll_interval, remaining))

        results = self._request(
            "GET", f"job/{quoted_job_id}/results", params={"limit": limit, "offset": 0}
        )
        return {
            "job_id": job_id,
            "row_count": results.get("rowCount", job.get("rowCount", 0)),
            "schema": results.get("schema", []),
            "rows": results.get("rows", []),
        }
