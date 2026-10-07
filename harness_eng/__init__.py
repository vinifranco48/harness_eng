"""Compatibility imports for the original Harness Eng public API."""

from apps.web.api.domain.auth import AuthResult, AuthState, AuthStatus, Provider
from apps.web.api.services.auth import AuthManager

__all__ = ["AuthManager", "AuthResult", "AuthState", "AuthStatus", "Provider"]
