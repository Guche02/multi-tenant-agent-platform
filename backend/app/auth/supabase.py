"""Server-side wrapper around Supabase Auth.

Supabase stores the users and their password hashes and issues the session tokens.
Our own `users` table holds what Supabase doesn't know about: the org and the role.
"""

import uuid
from typing import Annotated

import httpx
from fastapi import Depends, Request
from supabase_auth import AsyncGoTrueAdminAPI, AsyncGoTrueClient, Session
from supabase_auth.errors import AuthApiError, AuthError, AuthRetryableError, AuthWeakPasswordError


class AuthServiceError(Exception):
    """Supabase failed or refused for a reason that isn't the user's input."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class EmailAlreadyRegistered(Exception):
    pass


class InvalidCredentials(Exception):
    pass


class WeakPassword(Exception):
    pass


def _service_error(error: AuthError) -> AuthServiceError:
    if isinstance(error, AuthRetryableError):
        return AuthServiceError(503, "The sign-in service is unavailable. Try again shortly.")
    if error.code == "over_request_rate_limit" or getattr(error, "status", None) == 429:
        return AuthServiceError(429, "Too many attempts. Wait a minute and try again.")
    return AuthServiceError(502, "The sign-in service returned an error.")


class SupabaseAuth:
    def __init__(self, url: str, publishable_key: str, secret_key: str) -> None:
        self._auth_url = f"{url.rstrip('/')}/auth/v1"
        self._http = httpx.AsyncClient(timeout=10)
        self._public_headers = {"apikey": publishable_key, "Authorization": f"Bearer {publishable_key}"}
        self._admin = AsyncGoTrueAdminAPI(
            url=self._auth_url,
            headers={"apikey": secret_key, "Authorization": f"Bearer {secret_key}"},
            http_client=self._http,
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def create_user(self, email: str, password: str) -> uuid.UUID:
        # The admin API marks the email as confirmed, so no confirmation email is needed.
        try:
            response = await self._admin.create_user(
                {"email": email, "password": password, "email_confirm": True}
            )
        except AuthWeakPasswordError as error:
            raise WeakPassword(error.message) from error
        except AuthApiError as error:
            if error.code in ("email_exists", "user_already_exists"):
                raise EmailAlreadyRegistered from error
            if error.code == "weak_password":
                raise WeakPassword(error.message) from error
            raise _service_error(error) from error
        except AuthError as error:
            raise _service_error(error) from error
        return uuid.UUID(response.user.id)

    async def delete_user(self, user_id: uuid.UUID) -> None:
        await self._admin.delete_user(str(user_id))

    async def sign_in(self, email: str, password: str) -> Session:
        # A new client per sign-in: the library keeps the signed-in session in the client,
        # and a shared client would hold one user's session while serving another's request.
        client = AsyncGoTrueClient(
            url=self._auth_url,
            headers=self._public_headers,
            http_client=self._http,
            persist_session=False,
            auto_refresh_token=False,
        )
        try:
            response = await client.sign_in_with_password({"email": email, "password": password})
        except AuthApiError as error:
            if error.code == "invalid_credentials":
                raise InvalidCredentials from error
            raise _service_error(error) from error
        except AuthError as error:
            raise _service_error(error) from error
        if response.session is None:
            raise AuthServiceError(502, "The sign-in service returned no session.")
        return response.session

    async def sign_out(self, access_token: str) -> None:
        """Revokes the refresh token of this one session. Other devices stay signed in."""
        await self._admin.sign_out(access_token, scope="local")


def get_supabase_auth(request: Request) -> SupabaseAuth:
    return request.app.state.supabase_auth


SupabaseAuthDep = Annotated[SupabaseAuth, Depends(get_supabase_auth)]
