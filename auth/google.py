from __future__ import annotations

import base64
import hashlib
import os
import secrets
from urllib.parse import urlencode

import requests


GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_SCOPES = ("openid", "email", "profile")


class GoogleAuthError(RuntimeError):
    pass


def google_client_id() -> str:
    return os.getenv("GOOGLE_CLIENT_ID", "").strip()


def google_client_secret() -> str:
    return os.getenv("GOOGLE_CLIENT_SECRET", "").strip()


def google_login_available() -> bool:
    return bool(google_client_id() and google_client_secret())


def resolve_base_url(request_base_url: str | None = None) -> str:
    configured = os.getenv("APP_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    return (request_base_url or "http://127.0.0.1:5000").rstrip("/")


def resolve_redirect_uri(request_base_url: str | None = None) -> str:
    configured = os.getenv("GOOGLE_REDIRECT_URI", "").strip()
    if configured:
        return configured
    return f"{resolve_base_url(request_base_url)}/auth/google/callback"


def create_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("utf-8")).digest()).decode("utf-8").rstrip("=")
    return verifier, challenge


def build_google_authorization_url(state: str, code_challenge: str, request_base_url: str | None = None) -> str:
    if not google_login_available():
        raise GoogleAuthError("Google login is not configured. Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.")

    params = {
        "client_id": google_client_id(),
        "redirect_uri": resolve_redirect_uri(request_base_url),
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "state": state,
        "access_type": "online",
        "include_granted_scopes": "true",
        "prompt": "select_account",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def exchange_code_for_tokens(code: str, code_verifier: str, request_base_url: str | None = None) -> dict:
    if not google_login_available():
        raise GoogleAuthError("Google login is not configured.")

    response = requests.post(
        GOOGLE_TOKEN_URL,
        data={
            "code": code,
            "client_id": google_client_id(),
            "client_secret": google_client_secret(),
            "redirect_uri": resolve_redirect_uri(request_base_url),
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        },
        timeout=30,
    )
    if not response.ok:
        raise GoogleAuthError(f"Google token exchange failed ({response.status_code}).")

    payload = response.json()
    if "access_token" not in payload:
        raise GoogleAuthError("Google token exchange did not return an access token.")
    return payload


def fetch_google_profile(access_token: str) -> dict:
    response = requests.get(
        GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    if not response.ok:
        raise GoogleAuthError(f"Google user profile fetch failed ({response.status_code}).")

    payload = response.json()
    if not payload.get("email") or not payload.get("sub"):
        raise GoogleAuthError("Google did not return the required account fields.")
    return payload
