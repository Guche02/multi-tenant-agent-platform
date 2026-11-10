"""Seeds an org's test customers from customers.yaml: their logins, and past orders or bookings.

Logins are created through the Supabase admin API, the same way sign-up creates them, so the
passwords work on the login page. Safe to run again: an existing login is reused (its password is
left as it is), and past orders and bookings get fixed ids, made from the org's slug and the
entry's key, so each is inserted once and keeps the same order or booking number.
"""

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.supabase import EmailAlreadyRegistered, SupabaseAuth
from app.db.models import (
    Booking,
    Holiday,
    OpeningHours,
    Order,
    OrderItem,
    OrderStatusHistory,
    Practitioner,
    PractitionerService,
    Product,
    Service,
    User,
)
from app.seed.catalogue import read_yaml
from app.services.bookings import cancellation_fee
from app.services.orders import STATUS_FLOW
from app.tenancy.org_config import OrgConfig

# Fixed, so a seeded order or booking gets the same id (and number) on every machine.
SEED_NAMESPACE = uuid.UUID("6f1d2c3e-8a4b-4c5d-9e6f-7a8b9c0d1e2f")
# When each status was reached, after the order was placed: confirmed, shipped, delivered.
STATUS_DELAYS = [timedelta(0), timedelta(hours=2), timedelta(days=1), timedelta(days=3)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SeedItem(_Strict):
    sku: str
    quantity: int = Field(gt=0)
    unit_price: Decimal = Field(ge=0)  # the price when ordered


class SeedOrder(_Strict):
    key: str
    placed: datetime  # local time, no offset
    status: Literal["pending", "confirmed", "shipped", "delivered"]
    address: str
    items: list[SeedItem] = Field(min_length=1)


class SeedBooking(_Strict):
    key: str
    service: str
    practitioner: str
    starts: datetime  # local time, no offset
    cancelled: datetime | None = None  # when it was cancelled, if it was


class SeedCustomer(_Strict):
    email: str
    role: Literal["customer", "admin"] = "customer"
    orders: list[SeedOrder] = []
    bookings: list[SeedBooking] = []


class CustomersFile(_Strict):
    password: str
    customers: list[SeedCustomer]


def seed_id(org_slug: str, kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, f"{org_slug}/{kind}/{key}")


async def _ensure_login(db: AsyncSession, auth: SupabaseAuth, email: str, password: str) -> uuid.UUID:
    try:
        return await auth.create_user(email, password)
    except EmailAlreadyRegistered:
        user_id = await db.scalar(
            text("SELECT id FROM auth.users WHERE lower(email) = lower(:email)"), {"email": email}
        )
        if user_id is None:
            raise RuntimeError(f"Supabase says {email} exists but it isn't in auth.users") from None
        return user_id


async def _ensure_user(db: AsyncSession, user_id: uuid.UUID, org_id: uuid.UUID, email: str, role: str) -> None:
    existing = await db.get(User, user_id)
    if existing is not None and existing.org_id != org_id:
        # One email, one org: never move an account between orgs.
        raise ValueError(f"{email} already belongs to another org")
    stmt = insert(User).values(id=user_id, org_id=org_id, email=email, role=role)
    await db.execute(stmt.on_conflict_do_update(index_elements=["id"], set_={"role": stmt.excluded.role}))


async def _seed_orders(
    db: AsyncSession, org_id: uuid.UUID, org_slug: str, config: OrgConfig, user_id: uuid.UUID, orders: list[SeedOrder]
) -> int:
    products = {p.sku: p for p in await db.scalars(select(Product).where(Product.org_id == org_id))}
    added = 0
    for entry in orders:
        order_id = seed_id(org_slug, "order", entry.key)
        if await db.get(Order, order_id) is not None:
            continue
        unknown = [i.sku for i in entry.items if i.sku not in products]
        if unknown:
            raise ValueError(f"order {entry.key}: unknown SKUs {unknown}")
        placed = entry.placed.replace(tzinfo=config.tz)
        db.add(
            Order(
                id=order_id,
                org_id=org_id,
                user_id=user_id,
                conversation_id=None,
                status=entry.status,
                delivery_address=entry.address,
                total=sum(i.unit_price * i.quantity for i in entry.items),
                created_at=placed,
            )
        )
        await db.flush()
        db.add_all(
            OrderItem(
                id=seed_id(org_slug, "order-item", f"{entry.key}/{i.sku}"),
                org_id=org_id,
                order_id=order_id,
                product_id=products[i.sku].id,
                quantity=i.quantity,
                unit_price=i.unit_price,
            )
            for i in entry.items
        )
        reached = STATUS_FLOW[: STATUS_FLOW.index(entry.status) + 1]
        db.add_all(
            OrderStatusHistory(
                id=seed_id(org_slug, "order-status", f"{entry.key}/{status}"),
                org_id=org_id,
                order_id=order_id,
                from_status=reached[n - 1] if n else None,
                to_status=status,
                changed_at=placed + STATUS_DELAYS[n],
            )
            for n, status in enumerate(reached)
        )
        added += 1
    return added


async def _seed_bookings(
    db: AsyncSession,
    org_id: uuid.UUID,
    org_slug: str,
    config: OrgConfig,
    user_id: uuid.UUID,
    bookings: list[SeedBooking],
) -> int:
    services = {s.name: s for s in await db.scalars(select(Service).where(Service.org_id == org_id))}
    practitioners = {p.name: p for p in await db.scalars(select(Practitioner).where(Practitioner.org_id == org_id))}
    offers = set((await db.execute(
        select(PractitionerService.practitioner_id, PractitionerService.service_id)
        .where(PractitionerService.org_id == org_id)
    )).tuples())
    hours = {h.weekday: h for h in await db.scalars(select(OpeningHours).where(OpeningHours.org_id == org_id))}
    holidays = set(await db.scalars(select(Holiday.date).where(Holiday.org_id == org_id)))
    added = 0
    for entry in bookings:
        booking_id = seed_id(org_slug, "booking", entry.key)
        if await db.get(Booking, booking_id) is not None:
            continue
        service = services.get(entry.service)
        practitioner = practitioners.get(entry.practitioner)
        if service is None or practitioner is None:
            raise ValueError(f"booking {entry.key}: unknown service or practitioner")
        if (practitioner.id, service.id) not in offers:
            raise ValueError(f"booking {entry.key}: {practitioner.name} doesn't offer {service.name}")
        starts = entry.starts.replace(tzinfo=config.tz)
        ends = starts + timedelta(minutes=service.duration_minutes)
        # Past bookings skip the live rules (notice, booking window), but must still be possible.
        day = hours.get(starts.weekday())
        if (
            day is None
            or starts.date() in holidays
            or starts.time() < day.opens_at
            or ends.time() > day.closes_at
            or ends.date() != starts.date()
        ):
            raise ValueError(f"booking {entry.key}: {starts:%a %d %b %H:%M} isn't within opening hours")
        booking = Booking(
            id=booking_id,
            org_id=org_id,
            user_id=user_id,
            service_id=service.id,
            practitioner_id=practitioner.id,
            conversation_id=None,
            starts_at=starts,
            ends_at=ends,
            status="confirmed",
            price=service.price,
            created_at=starts - timedelta(days=5),
        )
        if entry.cancelled is not None:
            cancelled_at = entry.cancelled.replace(tzinfo=config.tz)
            booking.status = "cancelled"
            booking.cancellation_fee = cancellation_fee(booking, config, now=cancelled_at)[0]
            booking.cancelled_at = cancelled_at
        db.add(booking)
        added += 1
    # The exclusion constraints reject any overlap in the seed data here.
    await db.flush()
    return added


async def seed_customers(
    db: AsyncSession, auth: SupabaseAuth, org_id: uuid.UUID, org_slug: str, config: OrgConfig, org_dir: Path
) -> dict[str, int]:
    """Creates the org's test logins and their history. Returns a count per kind."""
    path = org_dir / "customers.yaml"
    if not path.exists():
        return {}
    data = CustomersFile.model_validate(read_yaml(path))
    counts = {"customers": 0, "past orders": 0, "past bookings": 0}
    for customer in data.customers:
        user_id = await _ensure_login(db, auth, customer.email, data.password)
        await _ensure_user(db, user_id, org_id, customer.email, customer.role)
        counts["customers"] += 1
        counts["past orders"] += await _seed_orders(db, org_id, org_slug, config, user_id, customer.orders)
        counts["past bookings"] += await _seed_bookings(db, org_id, org_slug, config, user_id, customer.bookings)
    return {kind: n for kind, n in counts.items() if n or kind == "customers"}
