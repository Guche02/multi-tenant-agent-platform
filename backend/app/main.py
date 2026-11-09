from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.auth.supabase import SupabaseAuth
from app.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    app.state.supabase_auth = SupabaseAuth(
        url=settings.supabase_url,
        publishable_key=settings.supabase_publishable_key.get_secret_value(),
        secret_key=settings.supabase_secret_key.get_secret_value(),
    )
    yield
    await app.state.supabase_auth.close()


app = FastAPI(title="Agent Platform", lifespan=lifespan)
app.include_router(auth_router, prefix="/api")
