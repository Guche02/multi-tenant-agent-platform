from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.auth.cookies import ACCESS_COOKIE
from app.auth.supabase import AuthServiceError, InvalidToken, SupabaseAuthDep
from app.db.models import User
from app.db.session import DbSession


async def get_current_user(request: Request, db: DbSession, supabase: SupabaseAuthDep) -> User:
    """The signed-in user, from the access-token cookie. 401 if there is none or it's invalid.

    On a 401 the frontend calls POST /api/auth/refresh once and retries.
    """
    access_token = request.cookies.get(ACCESS_COOKIE)
    if not access_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in.")
    try:
        user_id = await supabase.verify_access_token(access_token)
    except InvalidToken:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired or invalid.")
    except AuthServiceError as error:
        raise HTTPException(error.status_code, error.detail)

    # The token proves who the user is; our table says which org they belong to and their role.
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired or invalid.")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
