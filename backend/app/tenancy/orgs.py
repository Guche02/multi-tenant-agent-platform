from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy import select

from app.db.models import Organization
from app.db.session import DbSession


async def get_org(org_slug: str, db: DbSession) -> Organization:
    """The org named in the URL (e.g. /api/orgs/himali-tea/...)."""
    org = await db.scalar(select(Organization).where(Organization.slug == org_slug))
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found.")
    return org


OrgFromPath = Annotated[Organization, Depends(get_org)]
