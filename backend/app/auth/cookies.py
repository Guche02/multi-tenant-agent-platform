"""Session cookies.

Both tokens live in httpOnly cookies, so page scripts (and any injected script) can't read them.
"""

from fastapi import Response
from supabase_auth import Session

from app.config import get_settings

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"
REFRESH_COOKIE_PATH = "/api/auth"
REFRESH_COOKIE_MAX_AGE = 60 * 60 * 24 * 7  # 7 days


def set_session_cookies(response: Response, session: Session) -> None:
    secure = get_settings().cookie_secure
    response.set_cookie(
        ACCESS_COOKIE,
        session.access_token,
        max_age=session.expires_in,
        path="/",
        httponly=True,
        secure=secure,

        samesite="lax",
    )
    response.set_cookie(
        REFRESH_COOKIE,
        session.refresh_token,
        max_age=REFRESH_COOKIE_MAX_AGE,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=secure,
        samesite="strict",
    )


def clear_session_cookies(response: Response) -> None:
    # A cookie is only removed when the path and flags match the ones it was set with.
    secure = get_settings().cookie_secure
    response.delete_cookie(ACCESS_COOKIE, path="/", httponly=True, secure=secure, samesite="lax")
    response.delete_cookie(
        REFRESH_COOKIE, path=REFRESH_COOKIE_PATH, httponly=True, secure=secure, samesite="strict"
    )
