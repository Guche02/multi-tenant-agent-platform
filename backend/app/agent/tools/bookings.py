"""Tools for an appointment business's agent: the brief's eight booking tools.

Dates and times go in and out as studio-local time (the org's timezone). The model turns
"next Tuesday afternoon" into dates using today's date from the system prompt.
Booking, rescheduling and cancelling work in two calls, like orders: the first returns what will
happen (service, practitioner, date, time, price, or the cancellation fee) and the second, after
the customer confirms, does it.
"""

from datetime import date, datetime, time, timedelta
from typing import Annotated, Any

from llama_index.core.tools import FunctionTool
from pydantic import Field

from app.agent.base import AgentContext
from app.agent.tools.common import CONFIRM_NEXT_STEP, confirmation_given, knowledge_tool, make_tool, result
from app.db.session import session_factory
from app.services import bookings
from app.services.bookings import BookingError, Slot, SlotTaken

TOOL_LABELS = {
    "search_knowledge": "Searching our help articles…",
    "list_services": "Looking up our services…",
    "get_opening_hours": "Checking opening hours…",
    "find_available_slots": "Finding free slots…",
    "create_booking": "Preparing your booking…",
    "reschedule_booking": "Checking the new time…",
    "cancel_booking": "Checking your booking…",
    "list_my_bookings": "Fetching your bookings…",
}

DateArg = Annotated[str, Field(description="A date in YYYY-MM-DD, in studio time.")]
TimeArg = Annotated[str, Field(description="A start time in 24-hour HH:MM, in studio time, e.g. 14:30.")]
BookingNumber = Annotated[str, Field(description="The booking number, e.g. 7C1E09AB, from list_my_bookings.")]
PractitionerArg = Annotated[str | None, Field(description="A practitioner's name. Omit for anyone who is free.")]


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except ValueError as error:
        raise BookingError(f"{value!r} is not a date in YYYY-MM-DD form.") from error


def _parse_time(value: str) -> time:
    try:
        return time.fromisoformat(value.strip())
    except ValueError as error:
        raise BookingError(f"{value!r} is not a time in HH:MM form.") from error


def build_tools(ctx: AgentContext) -> list[FunctionTool]:
    config = ctx.config
    tz = config.tz

    def slot_out(slot: Slot) -> dict[str, Any]:
        return {"date": f"{slot.start:%Y-%m-%d}", "day": f"{slot.start:%A}", "start": f"{slot.start:%H:%M}",
                "end": f"{slot.end:%H:%M}", "practitioner": slot.practitioner}

    def at(day: str, start: str) -> datetime:
        return datetime.combine(_parse_date(day), _parse_time(start), tz)

    async def list_services() -> str:
        async with session_factory()() as db:
            return result(services=await bookings.list_services(db, ctx.org_id, config))

    async def get_opening_hours() -> str:
        async with session_factory()() as db:
            return result(**await bookings.opening_hours(db, ctx.org_id, config))

    async def find_available_slots(
        service: Annotated[str, Field(description="The service's name, e.g. 'Deep Tissue Massage'.")],
        start_date: DateArg,
        end_date: Annotated[str | None, Field(description="Last date to search (YYYY-MM-DD), at most 14 days on. Omit for one day.")] = None,
        practitioner: PractitionerArg = None,
        earliest_time: Annotated[str | None, Field(description="Earliest start time, HH:MM, e.g. 12:00 for the afternoon.")] = None,
        latest_time: Annotated[str | None, Field(description="Latest start time, HH:MM.")] = None,
    ) -> str:
        try:
            async with session_factory()() as db:
                found_service = await bookings.find_service(db, ctx.org_id, service)
                found_practitioner = (
                    await bookings.find_practitioner(db, ctx.org_id, practitioner) if practitioner else None
                )
                first = _parse_date(start_date)
                slots = await bookings.find_slots(
                    db, org_id=ctx.org_id, config=config, service=found_service,
                    start_date=first, end_date=_parse_date(end_date) if end_date else first,
                    practitioner=found_practitioner, user_id=ctx.user_id,
                    earliest=_parse_time(earliest_time) if earliest_time else None,
                    latest=_parse_time(latest_time) if latest_time else None,
                )
            return result(service=found_service.name, duration_minutes=found_service.duration_minutes,
                          price=str(found_service.price), currency=config.currency, timezone=config.timezone,
                          slots=[slot_out(s) for s in slots[:20]], more_available=len(slots) > 20)
        except BookingError as error:
            return result(status="refused", reason=str(error))

    async def alternatives(db, service, around: datetime, practitioner=None, exclude=None) -> list[dict[str, Any]]:
        slots = await bookings.nearest_slots(
            db, org_id=ctx.org_id, config=config, service=service, around=around, user_id=ctx.user_id,
            practitioner=practitioner, exclude_booking_id=exclude,
        )
        return [slot_out(s) for s in slots]

    async def create_booking(
        service: Annotated[str, Field(description="The service's name.")],
        date: DateArg,
        start_time: TimeArg,
        practitioner: PractitionerArg = None,
    ) -> str:
        try:
            async with session_factory()() as db:
                found_service = await bookings.find_service(db, ctx.org_id, service)
                start = at(date, start_time)
                if practitioner:
                    chosen = await bookings.find_practitioner(db, ctx.org_id, practitioner)
                else:
                    free = await bookings.find_slots(
                        db, org_id=ctx.org_id, config=config, service=found_service, start_date=start.date(),
                        end_date=start.date(), user_id=ctx.user_id, earliest=start.time(), latest=start.time(),
                    )
                    if not free:
                        return result(status="unavailable", reason=f"Nobody is free for {found_service.name} then.",
                                      nearest_alternatives=await alternatives(db, found_service, start))
                    chosen = await db.get_one(bookings.Practitioner, free[0].practitioner_id)
                try:
                    await bookings.check_slot(db, org_id=ctx.org_id, config=config, service=found_service,
                                              practitioner=chosen, start=start, user_id=ctx.user_id)
                except SlotTaken as error:
                    return result(status="unavailable", reason=str(error),
                                  nearest_alternatives=await alternatives(db, found_service, start))
                summary = {
                    "service": found_service.name, "practitioner": chosen.name,
                    "date": f"{start:%A %d %b %Y}", "start": f"{start:%H:%M}",
                    "end": f"{start + timedelta(minutes=found_service.duration_minutes):%H:%M}",
                    "price": str(found_service.price), "currency": config.currency, "timezone": config.timezone,
                }
                request = {"service": str(found_service.id), "practitioner": str(chosen.id),
                           "start": start.isoformat(), "price": summary["price"]}
                if not await confirmation_given(db, ctx, "create_booking", request):
                    await db.commit()
                    return result(status="needs_confirmation", summary=summary, next_step=CONFIRM_NEXT_STEP)
                try:
                    booking = await bookings.create_booking(
                        db, org_id=ctx.org_id, config=config, user_id=ctx.user_id,
                        conversation_id=ctx.conversation_id, service=found_service, practitioner=chosen, start=start,
                    )
                except SlotTaken as error:
                    await db.rollback()
                    async with session_factory()() as fresh:
                        options = await alternatives(fresh, found_service, start)
                    return result(status="unavailable", reason=str(error), nearest_alternatives=options)
                details = await bookings.booking_details(db, booking, config)
                await db.commit()
                return result(status="booked", booking=details)
        except BookingError as error:
            return result(status="refused", reason=str(error))

    async def reschedule_booking(
        booking_number: BookingNumber,
        new_date: DateArg,
        new_start_time: TimeArg,
        practitioner: Annotated[str | None, Field(description="Switch to another practitioner. Omit to keep the same one.")] = None,
    ) -> str:
        try:
            async with session_factory()() as db:
                booking = await bookings.get_own_booking(db, ctx.org_id, ctx.user_id, booking_number, for_update=True)
                if booking is None:
                    return result(status="not_found", reason=f"You have no booking {booking_number}.")
                current = await bookings.booking_details(db, booking, config)
                service = await db.get_one(bookings.Service, booking.service_id)
                chosen = (
                    await bookings.find_practitioner(db, ctx.org_id, practitioner) if practitioner
                    else await db.get_one(bookings.Practitioner, booking.practitioner_id)
                )
                new_start = at(new_date, new_start_time)
                policy = config.booking_policy
                assert policy is not None
                if datetime.now(tz) > booking.starts_at - timedelta(hours=policy.reschedule_cutoff_hours):
                    return result(status="refused", booking=current,
                                  reason=f"Bookings can't be moved within {policy.reschedule_cutoff_hours} hours "
                                         "of the session. The customer can keep it or cancel it.")
                if booking.status != "confirmed":
                    return result(status="refused", booking=current, reason=f"This booking is {booking.status}.")
                try:
                    await bookings.check_slot(db, org_id=ctx.org_id, config=config, service=service,
                                              practitioner=chosen, start=new_start, user_id=ctx.user_id,
                                              exclude_booking_id=booking.id)
                except BookingError as error:
                    return result(status="conflict", reason=str(error), booking=current,
                                  nearest_alternatives=await alternatives(db, service, new_start, chosen, booking.id))
                new = {"practitioner": chosen.name, "date": f"{new_start:%A %d %b %Y}", "start": f"{new_start:%H:%M}",
                       "end": f"{new_start + timedelta(minutes=service.duration_minutes):%H:%M}"}
                request = {"booking": str(booking.id), "practitioner": str(chosen.id), "start": new_start.isoformat()}
                if not await confirmation_given(db, ctx, "reschedule_booking", request):
                    await db.commit()
                    return result(status="needs_confirmation", current_booking=current, new_time=new,
                                  price_unchanged=current["price"], next_step=CONFIRM_NEXT_STEP)
                try:
                    await bookings.reschedule_booking(db, org_id=ctx.org_id, config=config, booking=booking,
                                                      new_start=new_start, practitioner=chosen)
                except SlotTaken as error:
                    await db.rollback()
                    async with session_factory()() as fresh:
                        options = await alternatives(fresh, service, new_start, chosen, booking.id)
                    return result(status="conflict", reason=str(error), nearest_alternatives=options)
                details = await bookings.booking_details(db, booking, config)
                await db.commit()
                return result(status="rescheduled", booking=details)
        except BookingError as error:
            return result(status="refused", reason=str(error))

    async def cancel_booking(booking_number: BookingNumber) -> str:
        try:
            async with session_factory()() as db:
                booking = await bookings.get_own_booking(db, ctx.org_id, ctx.user_id, booking_number, for_update=True)
                if booking is None:
                    return result(status="not_found", reason=f"You have no booking {booking_number}.")
                current = await bookings.booking_details(db, booking, config)
                if booking.status != "confirmed":
                    return result(status="refused", booking=current, reason=f"This booking is already {booking.status}.")
                fee, rule = bookings.cancellation_fee(booking, config)
                if not await confirmation_given(db, ctx, "cancel_booking", {"booking": str(booking.id), "fee": str(fee)}):
                    await db.commit()
                    return result(status="needs_confirmation", booking=current, cancellation_fee=str(fee),
                                  currency=config.currency, rule_applied=rule, next_step=CONFIRM_NEXT_STEP)
                await bookings.cancel_booking(db, config=config, booking=booking)
                details = await bookings.booking_details(db, booking, config)
                await db.commit()
                return result(status="cancelled", booking=details, rule_applied=rule)
        except BookingError as error:
            return result(status="refused", reason=str(error))

    async def list_my_bookings() -> str:
        async with session_factory()() as db:
            return result(timezone=config.timezone, **await bookings.list_bookings(db, ctx.org_id, ctx.user_id, config))

    return [
        knowledge_tool(ctx),
        make_tool(list_services, "list_services",
                  "Lists every service with its live duration, price and the practitioners who offer it."),
        make_tool(get_opening_hours, "get_opening_hours",
                  "Returns the weekly opening hours and upcoming holiday closures."),
        make_tool(find_available_slots, "find_available_slots",
                  "Finds free start times for a service in a date range (up to 14 days), optionally with one "
                  "practitioner and between two times of day. Only returns times that fit opening hours, avoid "
                  "holidays, fit the whole session and don't clash with the customer's other bookings."),
        make_tool(create_booking, "create_booking",
                  "Books a session for the signed-in customer. First call returns the details to confirm "
                  "(service, practitioner, date, time, price); the call after the customer confirms books it. "
                  "If the time is taken, returns the nearest free alternatives."),
        make_tool(reschedule_booking, "reschedule_booking",
                  "Moves one of the customer's bookings to a new time, only if it is free and the session is "
                  "more than the cut-off away. A conflict is refused with the nearest free alternatives. "
                  "First call returns the change to confirm."),
        make_tool(cancel_booking, "cancel_booking",
                  "Cancels one of the customer's bookings, applying the cancellation policy. First call returns "
                  "the fee that applies, to confirm."),
        make_tool(list_my_bookings, "list_my_bookings", "Lists the customer's upcoming and past bookings."),
    ]
