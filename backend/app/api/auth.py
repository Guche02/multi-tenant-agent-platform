import logging
import uuid

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy.exc import IntegrityError

from app.auth.cookies import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
    clear_session_cookies,
    set_session_cookies,
)
from app.auth.supabase import (
    AuthServiceError,
    EmailAlreadyRegistered,
    InvalidCredentials,
    InvalidToken,
    SupabaseAuthDep,
    WeakPassword,
)
from app.db.models import Organization, User
from app.db.session import DbSession
from app.tenancy.orgs import OrgFromPath

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

# One message for every failed login, so it doesn't reveal which emails exist or which org they're in.
INVALID_LOGIN = "Incorrect email or password."


class Credentials(BaseModel):
    email: EmailStr
    # Supabase hashes passwords with bcrypt, which only reads the first 72 bytes.
    password: str = Field(min_length=8, max_length=72)

    @field_validator("email")
    @classmethod
    def normalise_email(cls, email: str) -> str:
        return email.strip().lower()


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    org_slug: str


class AuthOut(BaseModel):
    user: UserOut


def _auth_out(user: User, org: Organization) -> AuthOut:
    return AuthOut(user=UserOut(id=user.id, email=user.email, role=user.role, org_slug=org.slug))


@router.post(
    "/orgs/{org_slug}/auth/signup", status_code=status.HTTP_201_CREATED, response_model=AuthOut
)
async def signup(
    body: Credentials, org: OrgFromPath, db: DbSession, supabase: SupabaseAuthDep, response: Response
) -> AuthOut:
    """Creates a customer account in the org named in the URL."""
    try:
        user_id = await supabase.create_user(body.email, body.password)
    except EmailAlreadyRegistered:
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists.")
    except WeakPassword as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error))
    except AuthServiceError as error:
        raise HTTPException(error.status_code, error.detail)

    # The org comes from the URL and the role is fixed: nothing in the request body can set them.
    user = User(id=user_id, org_id=org.id, email=body.email, role="customer")
    db.add(user)
    try:
        await db.commit()
    except Exception as error:
        await db.rollback()
        # Undo the Supabase account, so the email can sign up again.
        try:
            await supabase.delete_user(user_id)
        except Exception:
            logger.exception("Could not delete Supabase user %s after a failed signup", user_id)
        if isinstance(error, IntegrityError):
            raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists.")
        raise

    try:
        session = await supabase.sign_in(body.email, body.password)
    except (AuthServiceError, InvalidCredentials):
        # The account exists; only the automatic sign-in failed.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Account created, but signing in failed. Please log in."
        )
    set_session_cookies(response, session)
    return _auth_out(user, org)


@router.post("/orgs/{org_slug}/auth/login", response_model=AuthOut)
async def login(
    body: Credentials, org: OrgFromPath, db: DbSession, supabase: SupabaseAuthDep, response: Response
) -> AuthOut:
    """Signs in a user of the org named in the URL. Users of other orgs are refused."""
    try:
        session = await supabase.sign_in(body.email, body.password)
    except InvalidCredentials:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_LOGIN)
    except AuthServiceError as error:
        raise HTTPException(error.status_code, error.detail)

    user = await db.get(User, uuid.UUID(session.user.id))
    if user is None or user.org_id != org.id:
        # The password was right, but the account isn't in this org: end the session just created.
        try:
            await supabase.sign_out(session.access_token)
        except Exception:
            logger.exception("Could not revoke a session refused for org %s", org.slug)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_LOGIN)

    set_session_cookies(response, session)
    return _auth_out(user, org)


@router.post("/auth/refresh", status_code=status.HTTP_204_NO_CONTENT)
async def refresh(request: Request, supabase: SupabaseAuthDep, response: Response) -> Response:
    """Swaps the refresh-token cookie for a new pair of session cookies.

    Supabase refresh tokens are single-use, so the frontend should send one refresh at a time
    and retry the failed request after it succeeds.
    """
    refresh_token = request.cookies.get(REFRESH_COOKIE)
    if not refresh_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in.")
    try:
        session = await supabase.refresh(refresh_token)
    except InvalidToken:
        # Revoked, expired or already used: the user has to log in again. Drop the dead cookies.
        expired = JSONResponse({"detail": "Session expired. Please log in again."}, status_code=401)
        clear_session_cookies(expired)
        return expired
    except AuthServiceError as error:
        raise HTTPException(error.status_code, error.detail)
    set_session_cookies(response, session)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, supabase: SupabaseAuthDep, response: Response) -> None:
    """Ends the session. Always succeeds, so a user with an expired token can still log out."""
    access_token = request.cookies.get(ACCESS_COOKIE)
    if access_token:
        try:
            await supabase.sign_out(access_token)
        except Exception:
            # An expired or already-revoked token can't be revoked again; the cookies still go.
            logger.info("Supabase sign-out failed; clearing cookies anyway", exc_info=True)
    clear_session_cookies(response)
