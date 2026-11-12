"""Org pages and the signed-in customer's own records.

  GET /orgs                    public: the orgs, for the landing page
  GET /orgs/{slug}             public: one org's name, greeting and colours, for its entry page
  GET /orgs/{slug}/me          the signed-in user, if they belong to this org (403 otherwise)
  GET /orgs/{slug}/orders      the "My orders" panel (an online store only)
  GET /orgs/{slug}/bookings    the "My bookings" panel (an appointment business only)

The panels use the same service functions as the agent's list_my_orders/list_my_bookings and
order_details tools, so the panel and the agent always agree.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from app.api.auth import AuthOut, _auth_out
from app.db.models import Order, Organization
from app.db.session import DbSession
from app.services import bookings as booking_rules
from app.services import orders as order_rules
from app.tenancy.context import Tenant
from app.tenancy.org_config import Branding, OrgConfig
from app.tenancy.orgs import OrgFromPath

router = APIRouter(tags=["orgs"])

PANEL_ORDERS = 20


class OrgSummary(BaseModel):
    slug: str
    name: str
    branding: Branding


class OrgPublic(OrgSummary):
    greeting: str
    toolset: str
    timezone: str
    currency: str


def _config(org: Organization) -> OrgConfig:
    return OrgConfig.model_validate(org.config)


@router.get("/orgs", response_model=list[OrgSummary])
async def orgs(db: DbSession) -> list[OrgSummary]:
    found = await db.scalars(select(Organization).order_by(Organization.name))
    return [OrgSummary(slug=o.slug, name=o.name, branding=_config(o).branding) for o in found]


@router.get("/orgs/{org_slug}", response_model=OrgPublic)
async def org(org: OrgFromPath) -> OrgPublic:
    config = _config(org)
    return OrgPublic(slug=org.slug, name=org.name, branding=config.branding, greeting=config.agent.greeting,
                     toolset=config.agent.toolset, timezone=config.timezone, currency=config.currency)


@router.get("/orgs/{org_slug}/me", response_model=AuthOut)
async def me(tenant: Tenant) -> AuthOut:
    """Who is signed in. 401 when nobody is; 403 when the user belongs to another org."""
    return _auth_out(tenant.user, tenant.org)


@router.get("/orgs/{org_slug}/orders")
async def my_orders(tenant: Tenant, db: DbSession) -> dict[str, Any]:
    """The customer's orders, newest first, each with its items, total and status history."""
    config = _config(tenant.org)
    if config.agent.toolset != "orders":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This organization doesn't take orders.")
    found = await db.scalars(
        select(Order)
        .where(Order.org_id == tenant.org.id, Order.user_id == tenant.user.id)
        .order_by(Order.created_at.desc())
        .limit(PANEL_ORDERS)
    )
    return {"currency": config.currency,
            "orders": [await order_rules.order_details(db, o, config.tz) for o in found]}


@router.get("/orgs/{org_slug}/bookings")
async def my_bookings(tenant: Tenant, db: DbSession) -> dict[str, Any]:
    """The customer's upcoming bookings (soonest first) and past or cancelled ones."""
    config = _config(tenant.org)
    if config.agent.toolset != "bookings":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This organization doesn't take bookings.")
    return {"currency": config.currency, "timezone": config.timezone,
            **await booking_rules.list_bookings(db, tenant.org.id, tenant.user.id, config)}
