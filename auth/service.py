from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .db import auth_query, initialize_auth_store
from .google import (
    GoogleAuthError,
    build_google_authorization_url,
    create_pkce_pair,
    exchange_code_for_tokens,
    fetch_google_profile,
    google_login_available,
)
from .passwords import PasswordValidationError, hash_password, normalize_email, validate_email, verify_password


AUTH_COOKIE_NAME = "hybrid_desk_auth"
GUEST_MESSAGE_LIMIT = 10
AUTH_SESSION_DAYS = 30
STATE_TTL_MINUTES = 15


class AuthError(RuntimeError):
    pass


class AuthValidationError(AuthError):
    pass


class AuthConfigurationError(AuthError):
    pass


@dataclass
class AuthStatus:
    is_authenticated: bool
    guest_message_limit: int
    guest_messages_used: int
    guest_messages_remaining: int
    google_login_available: bool
    uploads_allowed: bool
    user_id: str | None = None
    email: str | None = None
    display_name: str | None = None
    avatar_url: str | None = None
    auth_provider: str | None = None

    def as_dict(self) -> dict:
        return {
            "isAuthenticated": self.is_authenticated,
            "guestMessageLimit": self.guest_message_limit,
            "guestMessagesUsed": self.guest_messages_used,
            "guestMessagesRemaining": self.guest_messages_remaining,
            "googleLoginAvailable": self.google_login_available,
            "uploadsAllowed": self.uploads_allowed,
            "fullAccessUnlocked": self.is_authenticated,
            "user": (
                {
                    "id": self.user_id,
                    "email": self.email,
                    "displayName": self.display_name,
                    "avatarUrl": self.avatar_url,
                    "provider": self.auth_provider,
                }
                if self.is_authenticated
                else None
            ),
        }


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sqlite_timestamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def cleanup_auth_data() -> None:
    auth_query(
        "DELETE FROM auth_sessions WHERE expires_at <= CURRENT_TIMESTAMP",
        fetch="none",
    )
    auth_query(
        "DELETE FROM oauth_states WHERE created_at <= datetime('now', ?)",
        (f"-{STATE_TTL_MINUTES} minutes",),
        fetch="none",
    )


def ensure_guest_usage(browser_session_id: str) -> None:
    auth_query(
        """
        INSERT INTO guest_usage (browser_session_id)
        VALUES (?)
        ON CONFLICT(browser_session_id) DO NOTHING
        """,
        (browser_session_id,),
        fetch="none",
    )


def guest_usage(browser_session_id: str) -> int:
    ensure_guest_usage(browser_session_id)
    row = auth_query(
        "SELECT messages_used FROM guest_usage WHERE browser_session_id = ?",
        (browser_session_id,),
        fetch="one",
    )
    return int(row["messages_used"]) if row else 0


def auth_session_row(browser_session_id: str, auth_token: str | None):
    if not auth_token:
        return None

    return auth_query(
        """
        SELECT
            s.id AS session_id,
            s.user_id,
            u.email,
            u.display_name,
            u.avatar_url,
            u.auth_provider
        FROM auth_sessions s
        JOIN auth_users u ON u.id = s.user_id
        WHERE s.session_token = ?
          AND s.browser_session_id = ?
          AND s.expires_at > CURRENT_TIMESTAMP
        """,
        (auth_token, browser_session_id),
        fetch="one",
    )


def get_auth_status(browser_session_id: str, auth_token: str | None = None) -> AuthStatus:
    initialize_auth_store()
    cleanup_auth_data()
    session_row = auth_session_row(browser_session_id, auth_token)

    if session_row:
        auth_query(
            "UPDATE auth_sessions SET last_seen_at = CURRENT_TIMESTAMP WHERE id = ?",
            (session_row["session_id"],),
            fetch="none",
        )
        return AuthStatus(
            is_authenticated=True,
            guest_message_limit=GUEST_MESSAGE_LIMIT,
            guest_messages_used=guest_usage(browser_session_id),
            guest_messages_remaining=max(0, GUEST_MESSAGE_LIMIT - guest_usage(browser_session_id)),
            google_login_available=google_login_available(),
            uploads_allowed=True,
            user_id=str(session_row["user_id"]),
            email=str(session_row["email"]),
            display_name=str(session_row["display_name"] or session_row["email"]),
            avatar_url=str(session_row["avatar_url"] or ""),
            auth_provider=str(session_row["auth_provider"] or "local"),
        )

    used = guest_usage(browser_session_id)
    return AuthStatus(
        is_authenticated=False,
        guest_message_limit=GUEST_MESSAGE_LIMIT,
        guest_messages_used=used,
        guest_messages_remaining=max(0, GUEST_MESSAGE_LIMIT - used),
        google_login_available=google_login_available(),
        uploads_allowed=False,
    )


def create_auth_session(user_id: str, browser_session_id: str) -> tuple[str, AuthStatus]:
    session_id = str(uuid.uuid4())
    session_token = secrets.token_urlsafe(32)
    expires_at = sqlite_timestamp(utcnow() + timedelta(days=AUTH_SESSION_DAYS))
    auth_query(
        """
        INSERT INTO auth_sessions (id, session_token, browser_session_id, user_id, expires_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (session_id, session_token, browser_session_id, user_id, expires_at),
        fetch="none",
    )
    return session_token, get_auth_status(browser_session_id, session_token)


def create_local_account(email: str, password: str, browser_session_id: str, display_name: str = "") -> tuple[str, AuthStatus]:
    initialize_auth_store()
    try:
        normalized_email = validate_email(email)
        salt_hex, digest_hex = hash_password(password)
    except PasswordValidationError as exc:
        raise AuthValidationError(str(exc)) from exc

    display_name = display_name.strip() or normalized_email.split("@", 1)[0]
    existing = auth_query(
        "SELECT id FROM auth_users WHERE email = ?",
        (normalized_email,),
        fetch="one",
    )
    if existing:
        raise AuthValidationError("An account with that email already exists.")

    user_id = str(uuid.uuid4())
    auth_query(
        """
        INSERT INTO auth_users (
            id,
            email,
            password_salt,
            password_hash,
            auth_provider,
            display_name,
            email_verified
        )
        VALUES (?, ?, ?, ?, 'local', ?, 0)
        """,
        (user_id, normalized_email, salt_hex, digest_hex, display_name),
        fetch="none",
    )
    return create_auth_session(user_id, browser_session_id)


def login_local_account(email: str, password: str, browser_session_id: str) -> tuple[str, AuthStatus]:
    initialize_auth_store()
    normalized_email = normalize_email(email)
    user = auth_query(
        """
        SELECT id, password_salt, password_hash, auth_provider
        FROM auth_users
        WHERE email = ?
        """,
        (normalized_email,),
        fetch="one",
    )
    if not user or not user["password_salt"] or not user["password_hash"]:
        raise AuthValidationError("Email or password is incorrect.")

    if not verify_password(password, str(user["password_salt"]), str(user["password_hash"])):
        raise AuthValidationError("Email or password is incorrect.")

    return create_auth_session(str(user["id"]), browser_session_id)


def clear_auth_session(browser_session_id: str) -> AuthStatus:
    ensure_guest_usage(browser_session_id)
    return get_auth_status(browser_session_id, None)


def logout_auth_session(auth_token: str | None) -> None:
    if not auth_token:
        return
    auth_query(
        "DELETE FROM auth_sessions WHERE session_token = ?",
        (auth_token,),
        fetch="none",
    )


def record_guest_message(browser_session_id: str) -> AuthStatus:
    ensure_guest_usage(browser_session_id)
    auth_query(
        """
        UPDATE guest_usage
        SET messages_used = messages_used + 1,
            updated_at = CURRENT_TIMESTAMP
        WHERE browser_session_id = ?
        """,
        (browser_session_id,),
        fetch="none",
    )
    return get_auth_status(browser_session_id, None)


def store_oauth_state(state: str, browser_session_id: str, code_verifier: str, next_path: str) -> None:
    auth_query(
        """
        INSERT INTO oauth_states (state, browser_session_id, code_verifier, next_path)
        VALUES (?, ?, ?, ?)
        """,
        (state, browser_session_id, code_verifier, next_path),
        fetch="none",
    )


def consume_oauth_state(state: str, browser_session_id: str):
    row = auth_query(
        """
        SELECT state, code_verifier, next_path
        FROM oauth_states
        WHERE state = ?
          AND browser_session_id = ?
          AND created_at > datetime('now', ?)
        """,
        (state, browser_session_id, f"-{STATE_TTL_MINUTES} minutes"),
        fetch="one",
    )
    auth_query(
        "DELETE FROM oauth_states WHERE state = ?",
        (state,),
        fetch="none",
    )
    if not row:
        raise AuthValidationError("Google login session expired. Please try again.")
    return row


def upsert_google_user(profile: dict) -> str:
    email = normalize_email(str(profile.get("email", "")))
    google_sub = str(profile.get("sub", "")).strip()
    display_name = str(profile.get("name", "")).strip() or email.split("@", 1)[0]
    avatar_url = str(profile.get("picture", "")).strip()
    email_verified = 1 if profile.get("email_verified") else 0

    existing = auth_query(
        """
        SELECT id, auth_provider
        FROM auth_users
        WHERE google_sub = ? OR email = ?
        LIMIT 1
        """,
        (google_sub, email),
        fetch="one",
    )
    if existing:
        provider = str(existing["auth_provider"] or "google")
        merged_provider = "hybrid" if provider not in {"google", "hybrid"} else provider
        auth_query(
            """
            UPDATE auth_users
            SET email = ?,
                google_sub = ?,
                display_name = ?,
                avatar_url = ?,
                email_verified = ?,
                auth_provider = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (email, google_sub, display_name, avatar_url, email_verified, merged_provider, existing["id"]),
            fetch="none",
        )
        return str(existing["id"])

    user_id = str(uuid.uuid4())
    auth_query(
        """
        INSERT INTO auth_users (
            id,
            email,
            google_sub,
            auth_provider,
            display_name,
            avatar_url,
            email_verified
        )
        VALUES (?, ?, ?, 'google', ?, ?, ?)
        """,
        (user_id, email, google_sub, display_name, avatar_url, email_verified),
        fetch="none",
    )
    return user_id


def build_google_login_url(browser_session_id: str, request_base_url: str, next_path: str = "/") -> str:
    initialize_auth_store()
    if not google_login_available():
        raise AuthConfigurationError("Google login is not configured yet.")

    state = secrets.token_urlsafe(24)
    code_verifier, code_challenge = create_pkce_pair()
    store_oauth_state(state, browser_session_id, code_verifier, next_path or "/")
    return build_google_authorization_url(state, code_challenge, request_base_url=request_base_url)


def complete_google_login(browser_session_id: str, state: str, code: str, request_base_url: str) -> tuple[str, AuthStatus, str]:
    initialize_auth_store()
    if not google_login_available():
        raise AuthConfigurationError("Google login is not configured yet.")

    oauth_state = consume_oauth_state(state, browser_session_id)
    try:
        tokens = exchange_code_for_tokens(code, str(oauth_state["code_verifier"]), request_base_url=request_base_url)
        profile = fetch_google_profile(str(tokens["access_token"]))
    except GoogleAuthError as exc:
        raise AuthValidationError(str(exc)) from exc

    user_id = upsert_google_user(profile)
    session_token, auth_status = create_auth_session(user_id, browser_session_id)
    return session_token, auth_status, str(oauth_state["next_path"] or "/")
