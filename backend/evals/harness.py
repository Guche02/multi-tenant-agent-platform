"""The eval harness: what the runner (evals/table.py) uses to chat with the real app as a customer.

For each case, the runner uses a Run to:
  - sign up a fresh customer through the real sign-up endpoint (so its own data starts empty),
  - set up any history the case needs (orders, bookings, another customer) with the service functions,
  - chat through the real HTTP endpoint, in-process (auth cookies, tenant checks, saved messages,
    the server-side confirmation step and the rate limit all run as in production), recording the
    reply, the tools the agent called and what each returned,
  - delete every user it created, with their orders and bookings.

Himali Tea stock is reset from products.yaml before and after each case (reset_stock), so one case's
order can't change another's stock and a run leaves the catalogue as seeded. The seeded demo
customers are never touched.
"""

import json
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import delete, select

from app.auth.supabase import SupabaseAuth
from app.db.models import Booking, Holiday, OpeningHours, Order, Organization, User
from app.db.session import session_factory
from app.seed.catalogue import load_catalogue
from app.seed.customers import SeedItem, SeedOrder, _seed_orders, seed_id
from app.services import bookings as booking_rules
from app.services import orders as order_rules
from app.tenancy.org_config import OrgConfig

SEED_DIR = Path(__file__).resolve().parents[1] / "seed" / "orgs"
EVAL_EMAIL_PREFIX = "eval-"
EVAL_EMAIL_DOMAIN = "example.com"

# ---------------------------------------------------------------------------
# One chat turn
# ---------------------------------------------------------------------------


@dataclass
class Turn:
    message: str
    reply: str = ""
    tools: list[str] = field(default_factory=list)  # tools called, in order
    results: list[tuple[str, dict[str, Any]]] = field(default_factory=list)  # (tool, result data)
    error: str | None = None


def parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        name, data = None, []
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
        if name:
            events.append((name, json.loads("\n".join(data)) if data else {}))
    return events


# ---------------------------------------------------------------------------
# The context a case runs in
# ---------------------------------------------------------------------------


@dataclass
class OrgInfo:
    id: uuid.UUID
    slug: str
    name: str
    config: OrgConfig

    @property
    def tz(self):
        return self.config.tz


async def load_org(slug: str) -> OrgInfo:
    async with session_factory()() as db:
        org = await db.scalar(select(Organization).where(Organization.slug == slug))
    if org is None:
        raise RuntimeError(f"Org {slug!r} isn't in the database. Run the seed first (make seed).")
    return OrgInfo(org.id, org.slug, org.name, OrgConfig.model_validate(org.config))


class Run:
    """What a case uses: the signed-in customer, the chat, fixtures and lookups.
    `label` (the case id) goes into the customer's email, to tell eval users apart."""

    def __init__(self, client: httpx.AsyncClient, auth: SupabaseAuth, org: OrgInfo, run_id: str, label: str):
        self.client = client
        self.auth = auth
        self.org = org
        self.run_id = run_id
        self.label = label
        self.turns: list[Turn] = []
        self.conversation_id: str | None = None
        self.user_id: uuid.UUID | None = None
        self.created_users: list[uuid.UUID] = []

    # -- users ---------------------------------------------------------------

    def _email(self, label: str) -> str:
        return f"{EVAL_EMAIL_PREFIX}{self.run_id}-{label}-{secrets.token_hex(3)}@{EVAL_EMAIL_DOMAIN}"

    async def sign_up(self) -> uuid.UUID:
        """The case's customer, created through the real sign-up endpoint and signed in."""
        email = self._email(self.label.replace("_", "-")[:20])
        response = await self.client.post(
            f"/api/orgs/{self.org.slug}/auth/signup", json={"email": email, "password": secrets.token_urlsafe(16)}
        )
        if response.status_code != 201:
            raise RuntimeError(f"sign-up failed: {response.status_code} {response.text}")
        self.user_id = uuid.UUID(response.json()["user"]["id"])
        self.created_users.append(self.user_id)
        return self.user_id

    async def other_customer(self) -> uuid.UUID:
        """Another customer of the same org, who never signs in: for cross-user cases."""
        email = self._email("other")
        user_id = await self.auth.create_user(email, secrets.token_urlsafe(16))
        self.created_users.append(user_id)
        async with session_factory()() as db:
            db.add(User(id=user_id, org_id=self.org.id, email=email, role="customer"))
            await db.commit()
        return user_id

    # -- chat ----------------------------------------------------------------

    async def say(self, message: str) -> Turn:
        """Sends one message in the case's conversation and collects the streamed reply."""
        turn = Turn(message)
        body: dict[str, Any] = {"message": message}
        if self.conversation_id:
            body["conversation_id"] = self.conversation_id
        response = await self.client.post(f"/api/orgs/{self.org.slug}/chat", json=body)
        if response.status_code != 200:
            turn.error = f"HTTP {response.status_code}: {response.text[:200]}"
        else:
            parts = []
            for name, data in parse_sse(response.text):
                if name == "conversation":
                    self.conversation_id = data["id"]
                elif name == "token":
                    parts.append(data["text"])
                elif name == "tool":
                    turn.tools.append(data["tool"])
                elif name == "tool_result":
                    turn.results.append((data["tool"], data["data"]))
                elif name == "error":
                    turn.error = data.get("message", "agent error")
            turn.reply = "".join(parts)
        self.turns.append(turn)
        return turn

    # -- fixtures: orders ----------------------------------------------------

    async def place_order(
        self, user_id: uuid.UUID, items: list[tuple[str, int]], address: str, *, advance: int = 0
    ) -> uuid.UUID:
        """A real order (stock reserved), moved `advance` steps along pending -> ... -> delivered."""
        async with session_factory()() as db:
            order = await order_rules.place_order(
                db, org_id=self.org.id, user_id=user_id, conversation_id=None,
                items=[order_rules.ItemRequest(sku, qty) for sku, qty in items], delivery_address=address,
            )
            for _ in range(advance):
                await order_rules.advance_status(db, order)
            await db.commit()
            return order.id

    async def past_order(
        self, user_id: uuid.UUID, items: list[tuple[str, int, Decimal]], address: str, placed: datetime
    ) -> uuid.UUID:
        """A delivered order from the past, at the prices given (stock untouched), as the seed makes them."""
        key = f"eval-{self.run_id}-{secrets.token_hex(4)}"
        entry = SeedOrder(
            key=key, placed=placed.replace(tzinfo=None), status="delivered", address=address,
            items=[SeedItem(sku=s, quantity=q, unit_price=p) for s, q, p in items],
        )
        async with session_factory()() as db:
            await _seed_orders(db, self.org.id, self.org.slug, self.org.config, user_id, [entry])
            await db.commit()
        return seed_id(self.org.slug, "order", key)

    # -- fixtures: bookings --------------------------------------------------

    async def book(self, user_id: uuid.UUID, service: str, practitioner: str, start: datetime) -> uuid.UUID:
        """A booking made through the booking rules, as the agent would make it."""
        async with session_factory()() as db:
            svc = await booking_rules.find_service(db, self.org.id, service)
            prac = await booking_rules.find_practitioner(db, self.org.id, practitioner)
            booking = await booking_rules.create_booking(
                db, org_id=self.org.id, config=self.org.config, user_id=user_id, conversation_id=None,
                service=svc, practitioner=prac, start=start,
            )
            await db.commit()
            return booking.id

    async def book_soon(self, user_id: uuid.UUID, service: str, practitioner: str, hours_ahead: int) -> uuid.UUID:
        """A booking `hours_ahead` from now, written directly: inside the reschedule cut-off and the
        late-cancellation window whatever the time of day the eval runs (opening hours are skipped)."""
        start = (datetime.now(self.org.tz) + timedelta(hours=hours_ahead)).replace(minute=0, second=0, microsecond=0)
        async with session_factory()() as db:
            svc = await booking_rules.find_service(db, self.org.id, service)
            prac = await booking_rules.find_practitioner(db, self.org.id, practitioner)
            booking = Booking(
                id=uuid.uuid4(), org_id=self.org.id, user_id=user_id, service_id=svc.id, practitioner_id=prac.id,
                conversation_id=None, starts_at=start, ends_at=start + timedelta(minutes=svc.duration_minutes),
                status="confirmed", price=svc.price,
            )
            db.add(booking)
            await db.commit()
            return booking.id

    async def open_day(self, min_days_ahead: int = 3) -> date:
        """The first day at least `min_days_ahead` away that the studio is open 09:00-17:00."""
        async with session_factory()() as db:
            hours = {h.weekday: h for h in await db.scalars(select(OpeningHours).where(OpeningHours.org_id == self.org.id))}
            holidays = set(await db.scalars(select(Holiday.date).where(Holiday.org_id == self.org.id)))
        day = datetime.now(self.org.tz).date() + timedelta(days=min_days_ahead)
        for _ in range(21):
            h = hours.get(day.weekday())
            if h and day not in holidays and h.opens_at <= time(9) and h.closes_at >= time(17):
                return day
            day += timedelta(days=1)
        raise RuntimeError("no open day in the next three weeks")

    def at(self, day: date, hh: int, mm: int = 0) -> datetime:
        return datetime.combine(day, time(hh, mm), self.org.tz)

    # -- lookups -------------------------------------------------------------

    async def booking(self, booking_id: uuid.UUID) -> Booking:
        async with session_factory()() as db:
            return await db.get_one(Booking, booking_id)

    def local(self, moment: datetime) -> datetime:
        return moment.astimezone(self.org.tz)

    # -- clean-up ------------------------------------------------------------

    async def clean_up(self) -> None:
        await delete_users(self.auth, self.created_users)


def order_ref(order_id: uuid.UUID) -> str:
    return order_rules.order_ref(order_id)


def booking_ref(booking_id: uuid.UUID) -> str:
    return booking_rules.booking_ref(booking_id)


async def delete_users(auth: SupabaseAuth, user_ids: list[uuid.UUID]) -> None:
    """Deletes users with everything they own. Orders and bookings first (they block the delete);
    conversations, messages and pending actions go with the user."""
    if not user_ids:
        return
    async with session_factory()() as db:
        await db.execute(delete(Booking).where(Booking.user_id.in_(user_ids)))
        await db.execute(delete(Order).where(Order.user_id.in_(user_ids)))
        await db.commit()
    for user_id in user_ids:
        try:
            await auth.delete_user(user_id)
        except Exception:
            # Not in Supabase any more: remove our row directly.
            async with session_factory()() as db:
                await db.execute(delete(User).where(User.id == user_id))
                await db.commit()


async def delete_stale_eval_users(auth: SupabaseAuth) -> int:
    """Eval users left behind by a run that was interrupted."""
    async with session_factory()() as db:
        stale = list(await db.scalars(
            select(User.id).where(User.email.like(f"{EVAL_EMAIL_PREFIX}%@{EVAL_EMAIL_DOMAIN}"))
        ))
    await delete_users(auth, stale)
    return len(stale)


async def reset_stock(org_slug: str) -> None:
    """Puts the org's catalogue (prices, stock) back to its seed file."""
    org = await load_org(org_slug)
    async with session_factory()() as db:
        await load_catalogue(db, org.id, SEED_DIR / org_slug)
        await db.commit()
