"""Booking rules and the free-slot engine for an appointment business.

A slot is bookable when:
  - it starts on the slot grid (e.g. every 30 minutes) inside the day's opening hours,
    and the whole service fits before closing;
  - the day is not a holiday or a closed day;
  - it starts at least min_notice_hours from now and at most max_days_ahead away;
  - the practitioner offers the service and has no confirmed booking overlapping it;
  - the customer has no other confirmed booking overlapping it.

Two customers grabbing the same slot at once: the bookings table has exclusion constraints
(no_practitioner_overlap, no_customer_overlap) that make Postgres refuse an overlapping
confirmed booking, whatever the timing. The engine's checks give good messages; the constraints
are the guarantee. A booking that loses the race gets SlotTaken.
"""

import uuid
from typing import Any
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import ColumnElement, Text, cast, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Booking, Holiday, OpeningHours, Practitioner, PractitionerService, Service
from app.tenancy.org_config import BookingPolicy, OrgConfig

MAX_SEARCH_DAYS = 14  # one search covers at most two weeks


class BookingError(Exception):
    """A request the rules refuse. The message is written to be shown to the customer."""


class SlotTaken(BookingError):
    pass


@dataclass(frozen=True)
class Slot:
    practitioner_id: uuid.UUID
    practitioner: str
    start: datetime  # aware
    end: datetime


def booking_ref(booking_id: uuid.UUID) -> str:
    return booking_id.hex[:8].upper()


def format_time(moment: datetime, config: OrgConfig) -> str:
    return moment.astimezone(config.tz).strftime("%a %d %b %Y, %H:%M")


def _policy(config: OrgConfig) -> BookingPolicy:
    if config.booking_policy is None:
        raise BookingError("This business doesn't take bookings.")
    return config.booking_policy


async def find_service(db: AsyncSession, org_id: uuid.UUID, name: str) -> Service:
    """By name, ignoring case. Failing that, a unique partial match ("hot stone"), then a tag
    ("back pain"), then part of a tag ("pregnancy"). The first of these that finds anything is
    used, and it must find exactly one service."""
    name = name.strip().lower()
    services = (await db.scalars(select(Service).where(Service.org_id == org_id))).all()
    tags = {s.id: [t.lower() for t in s.tags] for s in services}
    tiers = [
        [s for s in services if s.name.lower() == name],
        [s for s in services if name in s.name.lower()],
        [s for s in services if name in tags[s.id]],
        [s for s in services if any(name in tag for tag in tags[s.id])],
    ]
    matches = next((tier for tier in tiers if tier), [])
    if len(matches) != 1:
        names = ", ".join(s.name for s in services)
        raise BookingError(f"I couldn't identify the service {name!r}. Services: {names}.")
    return matches[0]


async def find_practitioner(db: AsyncSession, org_id: uuid.UUID, name: str) -> Practitioner:
    """By full or first name, ignoring case."""
    name = name.strip().lower()
    practitioners = (await db.scalars(select(Practitioner).where(Practitioner.org_id == org_id))).all()
    matches = [p for p in practitioners if name == p.name.lower() or name in p.name.lower().split()]
    matches = matches or [p for p in practitioners if name in p.name.lower()]
    if len(matches) != 1:
        names = ", ".join(p.name for p in practitioners)
        raise BookingError(f"I couldn't identify the practitioner {name!r}. Practitioners: {names}.")
    return matches[0]


async def catalogue_options(db: AsyncSession, org_id: uuid.UUID) -> tuple[list[str], list[str]]:
    """The org's service names and practitioner names, so the agent's tool descriptions can list
    the values that actually exist. Names only: durations, prices and slots are looked up live."""
    services = (await db.scalars(select(Service.name).where(Service.org_id == org_id).order_by(Service.name))).all()
    practitioners = (
        await db.scalars(select(Practitioner.name).where(Practitioner.org_id == org_id).order_by(Practitioner.name))
    ).all()
    return list(services), list(practitioners)


async def practitioners_for(db: AsyncSession, service: Service) -> list[Practitioner]:
    rows = await db.scalars(
        select(Practitioner)
        .join(PractitionerService, PractitionerService.practitioner_id == Practitioner.id)
        .where(PractitionerService.service_id == service.id)
        .order_by(Practitioner.name)
    )
    return list(rows)


async def list_services(db: AsyncSession, org_id: uuid.UUID, config: OrgConfig) -> list[dict[str, Any]]:
    services = (await db.scalars(select(Service).where(Service.org_id == org_id).order_by(Service.name))).all()
    return [
        {
            "name": s.name,
            "duration_minutes": s.duration_minutes,
            "price": str(s.price),
            "currency": config.currency,
            "practitioners": [p.name for p in await practitioners_for(db, s)],
        }
        for s in services
    ]


WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


async def opening_hours(db: AsyncSession, org_id: uuid.UUID, config: OrgConfig, days_ahead: int = 60) -> dict[str, Any]:
    hours = {
        h.weekday: h
        for h in await db.scalars(select(OpeningHours).where(OpeningHours.org_id == org_id))
    }
    today = datetime.now(config.tz).date()
    holidays = await db.scalars(
        select(Holiday)
        .where(Holiday.org_id == org_id, Holiday.date >= today, Holiday.date <= today + timedelta(days=days_ahead))
        .order_by(Holiday.date)
    )
    return {
        "timezone": config.timezone,
        "weekly_hours": [
            {
                "day": WEEKDAYS[d],
                "hours": f"{hours[d].opens_at:%H:%M}-{hours[d].closes_at:%H:%M}" if d in hours else "closed",
            }
            for d in range(7)
        ],
        "upcoming_closures": [{"date": f"{h.date:%a %d %b %Y}", "name": h.name} for h in holidays],
    }


async def find_slots(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    config: OrgConfig,
    service: Service,
    start_date: date,
    end_date: date,
    practitioner: Practitioner | None = None,
    user_id: uuid.UUID | None = None,
    earliest: time | None = None,
    latest: time | None = None,
    exclude_booking_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> list[Slot]:
    """Every bookable start time in the date range (inclusive), earliest first.

    earliest/latest limit the start time of day, e.g. 12:00-17:00 for "afternoon".
    exclude_booking_id ignores one existing booking, for rescheduling it.
    """
    policy = _policy(config)
    tz = config.tz
    now = now or datetime.now(tz)
    first_start = now + timedelta(hours=policy.min_notice_hours)
    last_start = now + timedelta(days=policy.max_days_ahead)
    start_date = max(start_date, now.astimezone(tz).date())
    end_date = min(end_date, last_start.astimezone(tz).date(), start_date + timedelta(days=MAX_SEARCH_DAYS - 1))
    if end_date < start_date:
        return []

    practitioners = await practitioners_for(db, service)
    if practitioner is not None:
        if practitioner.id not in {p.id for p in practitioners}:
            raise BookingError(f"{practitioner.name} doesn't offer {service.name}.")
        practitioners = [practitioner]

    hours = {h.weekday: h for h in await db.scalars(select(OpeningHours).where(OpeningHours.org_id == org_id))}
    holidays = set(
        await db.scalars(
            select(Holiday.date).where(Holiday.org_id == org_id, Holiday.date.between(start_date, end_date))
        )
    )
    window_start = datetime.combine(start_date, time.min, tz)
    window_end = datetime.combine(end_date + timedelta(days=1), time.min, tz)
    busy_filter: list[ColumnElement[bool]] = [Booking.practitioner_id.in_([p.id for p in practitioners])]
    if user_id is not None:
        busy_filter.append(Booking.user_id == user_id)
    busy_query = select(Booking.practitioner_id, Booking.user_id, Booking.starts_at, Booking.ends_at).where(
        Booking.org_id == org_id,
        Booking.status == "confirmed",
        Booking.starts_at < window_end,
        Booking.ends_at > window_start,
        or_(*busy_filter),
    )
    if exclude_booking_id is not None:
        busy_query = busy_query.where(Booking.id != exclude_booking_id)
    busy_rows = await db.execute(busy_query)
    busy = busy_rows.all()

    def overlaps(start: datetime, end: datetime, who: str, whose_id: uuid.UUID) -> bool:
        return any(
            (row.practitioner_id if who == "practitioner" else row.user_id) == whose_id
            and row.starts_at < end
            and row.ends_at > start
            for row in busy
        )

    duration = timedelta(minutes=service.duration_minutes)
    step = timedelta(minutes=policy.slot_interval_minutes)
    slots: list[Slot] = []
    day = start_date
    while day <= end_date:
        day_hours = hours.get(day.weekday())
        if day_hours is not None and day not in holidays:
            start = datetime.combine(day, day_hours.opens_at, tz)
            closes = datetime.combine(day, day_hours.closes_at, tz)
            while start + duration <= closes:
                local = start.time()
                if (
                    first_start <= start <= last_start
                    and (earliest is None or local >= earliest)
                    and (latest is None or local <= latest)
                    and not (user_id and overlaps(start, start + duration, "user", user_id))
                ):
                    for p in practitioners:
                        if not overlaps(start, start + duration, "practitioner", p.id):
                            slots.append(Slot(p.id, p.name, start, start + duration))
                start += step
        day += timedelta(days=1)
    return slots


async def check_slot(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    config: OrgConfig,
    service: Service,
    practitioner: Practitioner,
    start: datetime,
    user_id: uuid.UUID,
    exclude_booking_id: uuid.UUID | None = None,
) -> None:
    """Raises BookingError with the reason if `start` can't be booked."""
    policy = _policy(config)
    tz = config.tz
    local = start.astimezone(tz)
    now = datetime.now(tz)
    if local < now + timedelta(hours=policy.min_notice_hours):
        raise BookingError(f"Bookings must be made at least {policy.min_notice_hours} hours ahead.")
    if local > now + timedelta(days=policy.max_days_ahead):
        raise BookingError(f"Bookings can be made at most {policy.max_days_ahead} days ahead.")
    holiday = await db.scalar(select(Holiday).where(Holiday.org_id == org_id, Holiday.date == local.date()))
    if holiday is not None:
        raise BookingError(f"The studio is closed on {local:%a %d %b} ({holiday.name}).")
    day_hours = await db.scalar(
        select(OpeningHours).where(OpeningHours.org_id == org_id, OpeningHours.weekday == local.weekday())
    )
    if day_hours is None:
        raise BookingError(f"The studio is closed on {WEEKDAYS[local.weekday()]}s.")
    end = local + timedelta(minutes=service.duration_minutes)
    if local.time() < day_hours.opens_at or end.time() > day_hours.closes_at or end.date() != local.date():
        raise BookingError(
            f"A {service.duration_minutes}-minute {service.name} at {local:%H:%M} doesn't fit within "
            f"opening hours ({day_hours.opens_at:%H:%M}-{day_hours.closes_at:%H:%M})."
        )
    minutes = local.hour * 60 + local.minute - (day_hours.opens_at.hour * 60 + day_hours.opens_at.minute)
    if minutes % policy.slot_interval_minutes or local.second:
        raise BookingError(f"Sessions start every {policy.slot_interval_minutes} minutes from opening time.")
    free = await find_slots(
        db,
        org_id=org_id,
        config=config,
        service=service,
        start_date=local.date(),
        end_date=local.date(),
        practitioner=practitioner,
        user_id=user_id,
        earliest=local.time(),
        latest=local.time(),
        exclude_booking_id=exclude_booking_id,
    )
    if not free:
        raise SlotTaken(
            f"{practitioner.name} isn't free for {service.name} at {local:%a %d %b %H:%M}, "
            "or you already have a booking at that time."
        )


async def nearest_slots(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    config: OrgConfig,
    service: Service,
    around: datetime,
    user_id: uuid.UUID,
    practitioner: Practitioner | None = None,
    exclude_booking_id: uuid.UUID | None = None,
    count: int = 3,
) -> list[Slot]:
    """The free slots closest in time to `around`, earlier or later, within a week either side."""
    local = around.astimezone(config.tz)
    slots = await find_slots(
        db,
        org_id=org_id,
        config=config,
        service=service,
        start_date=local.date() - timedelta(days=7),
        end_date=local.date() + timedelta(days=6),
        practitioner=practitioner,
        user_id=user_id,
        exclude_booking_id=exclude_booking_id,
    )
    return sorted(slots, key=lambda s: abs(s.start - local))[:count]


def _overlap_error(error: IntegrityError) -> bool:
    return "no_practitioner_overlap" in str(error) or "no_customer_overlap" in str(error)


async def create_booking(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    config: OrgConfig,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    service: Service,
    practitioner: Practitioner,
    start: datetime,
) -> Booking:
    await check_slot(
        db, org_id=org_id, config=config, service=service, practitioner=practitioner, start=start, user_id=user_id
    )
    booking = Booking(
        id=uuid.uuid4(),
        org_id=org_id,
        user_id=user_id,
        service_id=service.id,
        practitioner_id=practitioner.id,
        conversation_id=conversation_id,
        starts_at=start,
        ends_at=start + timedelta(minutes=service.duration_minutes),
        status="confirmed",
        price=service.price,
    )
    db.add(booking)
    try:
        # The exclusion constraints are checked here, even if another request booked the same
        # slot a moment ago.
        async with db.begin_nested():
            await db.flush()
    except IntegrityError as error:
        if _overlap_error(error):
            raise SlotTaken("Someone booked that time a moment ago.") from error
        raise
    return booking


async def get_own_booking(
    db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, ref: str, *, for_update: bool = False
) -> Booking | None:
    """The user's booking by full id or booking number. Other users' bookings are never found."""
    ref = ref.strip().lstrip("#").lower().replace("-", "")
    if not ref or any(c not in "0123456789abcdef" for c in ref) or not 6 <= len(ref) <= 32:
        return None
    stmt = select(Booking).where(
        Booking.org_id == org_id,
        Booking.user_id == user_id,
        func.replace(cast(Booking.id, Text), "-", "").like(f"{ref}%"),
    )
    if for_update:
        stmt = stmt.with_for_update()
    matches = (await db.scalars(stmt.limit(2))).all()
    return matches[0] if len(matches) == 1 else None


async def reschedule_booking(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    config: OrgConfig,
    booking: Booking,
    new_start: datetime,
    practitioner: Practitioner | None = None,
) -> Booking:
    """Moves a confirmed booking to a new free time, optionally with another practitioner."""
    policy = _policy(config)
    if booking.status != "confirmed":
        raise BookingError(f"Booking {booking_ref(booking.id)} is {booking.status} and can't be moved.")
    if datetime.now(config.tz) > booking.starts_at - timedelta(hours=policy.reschedule_cutoff_hours):
        raise BookingError(
            f"Bookings can't be moved within {policy.reschedule_cutoff_hours} hours of the session. "
            "You can keep this booking or cancel it under the cancellation policy."
        )
    service = await db.get_one(Service, booking.service_id)
    practitioner = practitioner or await db.get_one(Practitioner, booking.practitioner_id)
    await check_slot(
        db,
        org_id=org_id,
        config=config,
        service=service,
        practitioner=practitioner,
        start=new_start,
        user_id=booking.user_id,
        exclude_booking_id=booking.id,
    )
    booking.practitioner_id = practitioner.id
    booking.starts_at = new_start
    booking.ends_at = new_start + timedelta(minutes=service.duration_minutes)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError as error:
        if _overlap_error(error):
            raise SlotTaken("Someone booked that time a moment ago.") from error
        raise
    return booking


def cancellation_fee(booking: Booking, config: OrgConfig, now: datetime | None = None) -> tuple[Decimal, str]:
    """The fee for cancelling now, and the rule that applies."""
    policy = _policy(config)
    hours_left = (booking.starts_at - (now or datetime.now(config.tz))).total_seconds() / 3600
    if hours_left >= policy.free_cancellation_hours:
        return Decimal("0.00"), f"{policy.free_cancellation_hours} hours or more before: free"
    if hours_left >= policy.no_show_hours:
        fee = (booking.price * policy.late_cancellation_fee_percent / 100).quantize(Decimal("0.01"))
        return fee, (
            f"between {policy.no_show_hours} and {policy.free_cancellation_hours} hours before: "
            f"{policy.late_cancellation_fee_percent}% of the session price"
        )
    return booking.price, f"less than {policy.no_show_hours} hours before: the full session price"


async def cancel_booking(db: AsyncSession, *, config: OrgConfig, booking: Booking) -> Booking:
    if booking.status != "confirmed":
        raise BookingError(f"Booking {booking_ref(booking.id)} is already {booking.status}.")
    if booking.starts_at <= datetime.now(config.tz):
        raise BookingError(f"Booking {booking_ref(booking.id)} has already started and can't be cancelled.")
    fee, _ = cancellation_fee(booking, config)
    booking.status = "cancelled"
    booking.cancellation_fee = fee
    booking.cancelled_at = datetime.now(UTC)
    await db.flush()
    return booking


async def booking_details(db: AsyncSession, booking: Booking, config: OrgConfig) -> dict[str, Any]:
    service = await db.get_one(Service, booking.service_id)
    practitioner = await db.get_one(Practitioner, booking.practitioner_id)
    details = {
        "booking_number": booking_ref(booking.id),
        "service": service.name,
        "practitioner": practitioner.name,
        "starts": format_time(booking.starts_at, config),
        "ends": f"{booking.ends_at.astimezone(config.tz):%H:%M}",
        "status": booking.status,
        "price": str(booking.price),
        "currency": config.currency,
    }
    if booking.status == "cancelled":
        details["cancellation_fee"] = str(booking.cancellation_fee)
    return details


async def list_bookings(db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, config: OrgConfig) -> dict[str, Any]:
    bookings = (
        await db.scalars(
            select(Booking)
            .where(Booking.org_id == org_id, Booking.user_id == user_id)
            .order_by(Booking.starts_at.desc())
            .limit(30)
        )
    ).all()
    now = datetime.now(config.tz)
    upcoming = [b for b in bookings if b.status == "confirmed" and b.starts_at > now]
    past = [b for b in bookings if b not in upcoming]
    return {
        "upcoming": [await booking_details(db, b, config) for b in reversed(upcoming)],  # soonest first
        "past_and_cancelled": [await booking_details(db, b, config) for b in past],
    }
