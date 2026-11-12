"""The evals: runs the scenarios in evals/cases.yaml and reports pass/fail per scenario.

Run from backend/ (or `make eval` from the repo root):
    uv run python -m evals                           every scenario (12 per agent)
    uv run python -m evals --org lotus-wellness      one org's scenarios
    uv run python -m evals --only order_happy_path,reschedule_conflict
    uv run python -m evals --list                    list the scenarios and stop
    uv run python -m evals --dry-run                 placeholder agent, no LLM calls, no report (tests the setup)

A scenario passes when every check in every turn passes: fixed code checks on the database state
afterwards, the tools' results and key phrases in the reply (no LLM judge). For each turn the report
also shows the question, and the expected and actual answer, tools and database state. Written as
    evals/table_results.html   open in a browser: pass/fail summary, then one card per scenario
    evals/table_results.md     the same in Markdown (GitHub, VS Code preview)

Uses evals/harness.py: a fresh customer per case signed up through the real endpoint, the real chat
endpoint in-process, the real LLM and database, and every user it creates deleted afterwards.
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time as clock
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import yaml
from sqlalchemy import select

from app.agent.llm import DEFAULT_MODELS
from app.config import get_settings
from app.db.models import Booking, Holiday, OpeningHours, Order, Product
from app.db.session import session_factory
from app.main import app
from app.services import bookings as booking_rules
from app.services import orders as order_rules
from evals.harness import (
    SEED_DIR,
    Run,
    Turn,
    booking_ref,
    delete_stale_eval_users,
    load_org,
    order_ref,
    reset_stock,
)

HERE = Path(__file__).parent
CASES = HERE / "cases.yaml"
REPORT_HTML = HERE / "table_results.html"
REPORT_MD = HERE / "table_results.md"
DEFAULT_ADDRESS = "House 5, Bakhundole Marg, Lalitpur"
ORDER_STEPS = {"pending": 0, "confirmed": 1, "shipped": 2, "delivered": 3}
MD_ANSWER_LIMIT = 1500  # the HTML keeps answers in full


# ---------------------------------------------------------------------------
# Placeholders
# ---------------------------------------------------------------------------


class Placeholders(dict):
    """Leaves an unknown {name} as it is instead of failing, so a typo shows up in the table."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def fill(text: Any, values: Placeholders) -> str:
    return str(text if text is not None else "").format_map(values)


async def date_values(run: Run) -> tuple[dict[str, date], Placeholders]:
    """The named dates for this org, and the placeholders made from them."""
    today = datetime.now(run.org.tz).date()
    next_monday = today + timedelta(days=7 - today.weekday())
    days = {
        "today": today,
        "tomorrow": today + timedelta(days=1),
        "day_after_tomorrow": today + timedelta(days=2),
        "coming_saturday": today + timedelta(days=(5 - today.weekday()) % 7 or 7),
        "next_tuesday": today + timedelta(days=(1 - today.weekday()) % 7 or 7),
        "tuesday_after_next": today + timedelta(days=((1 - today.weekday()) % 7 or 7) + 7),
        "next_week_start": next_monday,
        "next_week_end": next_monday + timedelta(days=6),
    }
    async with session_factory()() as db:
        hours = {h.weekday: h for h in await db.scalars(select(OpeningHours).where(OpeningHours.org_id == run.org.id))}
        holidays = {h.date: h.name for h in await db.scalars(select(Holiday).where(Holiday.org_id == run.org.id))}
    if hours:  # only a business with opening hours has an open day
        days["open_day"] = await run.open_day()

    values = Placeholders()
    for name, day in days.items():
        values[name] = f"{day:%A} {day.day} {day:%B %Y}"
        values[f"{name}_iso"] = day.isoformat()
        if not hours:
            values[f"{name}_hours"] = "no opening hours"
        elif day in holidays:
            values[f"{name}_hours"] = f"closed ({holidays[day]})"
        elif day.weekday() not in hours:
            values[f"{name}_hours"] = f"closed ({day:%A})"
        else:
            h = hours[day.weekday()]
            values[f"{name}_hours"] = f"open {h.opens_at:%H:%M}-{h.closes_at:%H:%M}"
    return days, values


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


@dataclass
class Case:
    id: str
    org: str
    category: str
    description: str
    turns: list[dict[str, Any]]
    setup: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""  # what was actually found, shown when the check fails


@dataclass
class Row:
    case: Case
    turn_number: int
    question: str = ""
    expected_answer: str = ""
    actual_answer: str = ""
    expected_tools: str = ""
    actual_tools: str = ""
    expected_db: str = ""
    actual_db: str = ""
    setup_text: str = ""
    checks: list[CheckResult] = field(default_factory=list)


def case_passed(rows: list[Row]) -> bool:
    checks = [c for r in rows for c in r.checks]
    return bool(checks) and all(c.ok for c in checks)


async def apply_setup(run: Run, case: Case, days: dict[str, date], values: Placeholders) -> list[str]:
    """Creates the case's history. Returns a line describing each item, for the Case column."""
    lines = []
    other = None
    for item in case.setup:
        if item.get("customer") == "other":
            other = other or await run.other_customer()
            owner, whose = other, "Other customer"
        else:
            owner, whose = run.user_id, "This customer"
        if "order" in item:
            spec = item["order"]
            items = [(sku, int(qty)) for sku, qty in spec["items"]]
            status = spec.get("status", "pending")
            created = await run.place_order(owner, items, spec.get("address", DEFAULT_ADDRESS),
                                            advance=ORDER_STEPS[status])
            ref = order_ref(created)
            lines.append(f"{whose}: order {ref}, {status}, " + ", ".join(f"{q} × {s}" for s, q in items))
        elif "past_order" in item:
            spec = item["past_order"]
            items = [(sku, int(qty), Decimal(str(price))) for sku, qty, price in spec["items"]]
            placed = datetime.now(run.org.tz) - timedelta(days=int(spec.get("days_ago", 20)))
            created = await run.past_order(owner, items, spec.get("address", DEFAULT_ADDRESS), placed)
            ref = order_ref(created)
            lines.append(f"{whose}: past order {ref}, delivered, placed {placed:%d %b}, "
                         + ", ".join(f"{q} × {s} at {p}" for s, q, p in items))
        elif "booking" in item:
            spec = item["booking"]
            hh, mm = (int(x) for x in spec["at"].split(":"))
            created = await run.book(owner, spec["service"], spec["practitioner"], run.at(days[spec["day"]], hh, mm))
            ref = booking_ref(created)
            lines.append(f"{whose}: booking {ref}, {spec['service']} with {spec['practitioner']}, "
                         f"{values[spec['day']]} {spec['at']}")
        elif "booking_soon" in item:
            spec = item["booking_soon"]
            created = await run.book_soon(owner, spec["service"], spec["practitioner"], int(spec["hours_ahead"]))
            ref = booking_ref(created)
            starts = run.local((await run.booking(created)).starts_at)
            lines.append(f"{whose}: booking {ref}, {spec['service']} with {spec['practitioner']}, "
                         f"{starts:%a %d %b %H:%M} (in about {spec['hours_ahead']} hours)")
        else:
            raise ValueError(f"{case.id}: unknown setup item {item}")
        if item.get("as"):
            values[item["as"]] = ref
    return lines


# ---------------------------------------------------------------------------
# What actually happened: tools and database
# ---------------------------------------------------------------------------


def _items(items: list[dict[str, Any]]) -> str:
    return ", ".join(f"{i['quantity']} × {i['sku']}" for i in items)


def describe_tool(name: str, data: dict[str, Any]) -> str:
    """One tool call as 'name → status (key details)'."""
    status = data.get("status")
    detail = ""
    if name == "search_knowledge":
        hits = data.get("results", [])
        detail = "; ".join(f"{h['document']} › {h['section']}" for h in hits[:3]) or "nothing found"
    elif name == "search_products":
        products = data.get("products", [])
        detail = "; ".join(f"{p['name']} {p['price']}, {p['in_stock']} in stock" for p in products[:4]) or "nothing found"
        if len(products) > 4:
            detail += f"; +{len(products) - 4} more"
    elif name in ("create_order", "reorder") and status == "needs_confirmation":
        s = data.get("summary", {})
        detail = f"{_items(s.get('items', []))}, total {s.get('total')}, to {s.get('delivery_address')}"
    elif name == "update_order" and status == "needs_confirmation":
        s = data.get("new_order", {})
        detail = f"{_items(s.get('items', []))}, total {s.get('total')}"
    elif "order" in data and isinstance(data["order"], dict):
        o = data["order"]
        detail = f"order {o.get('order_number')}, {o.get('status')}, {_items(o.get('items', []))}, total {o.get('total')}"
    elif name == "list_my_orders":
        detail = f"{len(data.get('orders', []))} orders"
    elif name == "find_available_slots":
        slots = data.get("slots", [])
        if slots:
            dates = sorted({s["date"] for s in slots})
            people = sorted({s["practitioner"] for s in slots})
            detail = (f"{len(slots)} slots{'+' if data.get('more_available') else ''}, {', '.join(dates)}, "
                      f"{slots[0]['start']}-{slots[-1]['start']}, {', '.join(people)}")
        elif not status:
            detail = "no free slots"
    elif name in ("create_booking", "reschedule_booking") and status == "needs_confirmation":
        s = data.get("summary") or {**data.get("new_time", {})}
        detail = f"{s.get('service', '')} {s.get('practitioner')}, {s.get('date')} {s.get('start')}-{s.get('end')}"
        if s.get("price"):
            detail += f", {s['price']}"
    elif name == "cancel_booking" and status == "needs_confirmation":
        detail = f"fee {data.get('cancellation_fee')}"
    elif "booking" in data and isinstance(data["booking"], dict):
        b = data["booking"]
        detail = f"{b.get('booking_number')}, {b.get('status')}, {b.get('starts')}"
        if b.get("cancellation_fee") is not None:
            detail += f", fee {b['cancellation_fee']}"
    elif name == "list_services":
        detail = f"{len(data.get('services', []))} services"
    elif name == "list_my_bookings":
        detail = f"{len(data.get('upcoming', []))} upcoming, {len(data.get('past_and_cancelled', []))} past"
    if data.get("nearest_alternatives"):
        alts = data["nearest_alternatives"]
        detail += (", " if detail else "") + "alternatives " + ", ".join(f"{a['date']} {a['start']}" for a in alts[:3])
    if data.get("price_changes"):
        detail += (", " if detail else "") + "price changes " + ", ".join(
            f"{p['sku']} {p['was']} → {p['now']}" for p in data["price_changes"])
    if data.get("problems"):
        detail += (", " if detail else "") + "; ".join(map(str, data["problems"]))
    if data.get("alternatives"):
        detail += (", " if detail else "") + "alternatives " + ", ".join(a["name"] for a in data["alternatives"])
    if data.get("reason") and status not in ("needs_confirmation",):
        detail += (", " if detail else "") + str(data["reason"])
    label = f"{name} → {status}" if status else name
    return f"{label} ({detail})" if detail else label


def describe_tools(turn: Turn) -> str:
    """Every tool the agent called in a turn, in order, with what it returned."""
    if not turn.tools:
        return "none"
    results = list(turn.results)
    lines = []
    for tool in turn.tools:
        match = next((i for i, (name, _) in enumerate(results) if name == tool), None)
        lines.append(describe_tool(tool, results.pop(match)[1]) if match is not None else f"{tool} (no result)")
    return "\n".join(f"{n}. {line}" for n, line in enumerate(lines, 1))


async def seed_stock(org_slug: str) -> dict[str, int]:
    products = yaml.safe_load((SEED_DIR / org_slug / "products.yaml").read_text(encoding="utf-8")) or []
    return {p["sku"]: int(p["stock"]) for p in products}


async def database_state(run: Run, seed: dict[str, int]) -> str:
    """The orders and bookings of every user the case created, and stock that differs from the seed."""
    others = [u for u in run.created_users if u != run.user_id]
    lines = []
    async with session_factory()() as db:
        for user, whose in [(run.user_id, "This customer"), *[(u, "Other customer") for u in others]]:
            orders = list(await db.scalars(select(Order).where(Order.user_id == user).order_by(Order.created_at)))
            for order in orders:
                d = await order_rules.order_details(db, order, run.org.tz)
                lines.append(f"{whose}: order {d['order_number']}, {d['status']}, "
                             + ", ".join(f"{i['quantity']} × {i['name']} ({i['sku']})" for i in d["items"])
                             + f", total {d['total']}, to {d['delivery_address']}")
            found = list(await db.scalars(select(Booking).where(Booking.user_id == user).order_by(Booking.starts_at)))
            for booking in found:
                d = await booking_rules.booking_details(db, booking, run.org.config)
                line = (f"{whose}: booking {d['booking_number']}, {d['status']}, {d['service']} with "
                        f"{d['practitioner']}, {d['starts']}-{d['ends']}, {d['price']}")
                if booking.status == "cancelled":
                    line += f", fee {d.get('cancellation_fee')}"
                lines.append(line)
            if user == run.user_id:
                if run.org.config.agent.toolset == "orders" and not orders:
                    lines.append("This customer: no orders")
                if run.org.config.agent.toolset == "bookings" and not found:
                    lines.append("This customer: no bookings")
        if seed:
            products = await db.scalars(select(Product).where(Product.org_id == run.org.id).order_by(Product.sku))
            changed = [f"{p.sku} {seed[p.sku]} → {p.stock}" for p in products if p.sku in seed and p.stock != seed[p.sku]]
            lines.append("Stock vs seed: " + (", ".join(changed) if changed else "unchanged"))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Checks: what decides pass or fail. Fixed code, no LLM judge.
# ---------------------------------------------------------------------------
#
# A turn's `checks:` is a list; each item is one check, with an optional `label` to name it:
#   tool_called: search_knowledge                  the agent called this tool
#   tool_status: create_order needs_confirmation   some call of the tool returned this status
#   tool_never: create_order placed|updated        no call of the tool returned any of these
#   tool_has: {tool, path, pattern?}               some result has a non-empty value at path (matching pattern)
#   tool_lacks: {tool, pattern}                    no result of the tool contains the pattern
#   reply_has: <regex>    reply_lacks: <regex>     the reply matches / doesn't match (case-insensitive)
#   reply_has_ref: order|booking                   the reply gives the customer's newest order/booking number
#   reply_offers_other_time: ["14:00", ...]        the reply gives a clock time other than these
#   orders: N    bookings: N                       how many orders/bookings this customer has
#   order: {ref?, status?, items?, total?}         an order (by number, or the customer's newest)
#   booking: {ref?, status?, day?, start?, end?, practitioner?, fee?}
#   stock: {SKU: N}                                live stock
#   slots: {practitioner?, days?, from?, to?}      find_available_slots returned slots, all matching
#   other_org_chat: <org slug>                     this customer gets 403 from that org's chat endpoint


def _statuses(turn: Turn, tool: str) -> list[str]:
    return [str(d.get("status")) for name, d in turn.results if name == tool]


def _at_path(data: Any, path: str) -> Any:
    for part in path.split("."):
        data = data.get(part) if isinstance(data, dict) else None
    return data


async def _order_of(run: Run, ref: str | None) -> Order | None:
    """An order by number among the case's customers, or this customer's newest."""
    async with session_factory()() as db:
        if ref:
            found = await db.scalars(select(Order).where(Order.user_id.in_(run.created_users)))
            return next((o for o in found if order_ref(o.id) == ref), None)
        return await db.scalar(select(Order).where(Order.user_id == run.user_id).order_by(Order.created_at.desc()))


async def _booking_of(run: Run, ref: str | None) -> Booking | None:
    async with session_factory()() as db:
        if ref:
            found = await db.scalars(select(Booking).where(Booking.user_id.in_(run.created_users)))
            return next((b for b in found if booking_ref(b.id) == ref), None)
        return await db.scalar(select(Booking).where(Booking.user_id == run.user_id).order_by(Booking.created_at.desc()))


async def _count(run: Run, model: Any) -> int:
    async with session_factory()() as db:
        return len(list(await db.scalars(select(model.id).where(model.user_id == run.user_id))))


async def check_one(kind: str, arg: Any, turn: Turn, run: Run, days: dict[str, date],
                    values: Placeholders) -> CheckResult:
    reply = turn.reply or ""
    if kind == "tool_called":
        return CheckResult(f"called {arg}", arg in turn.tools, f"tools: {', '.join(turn.tools) or 'none'}")
    if kind in ("tool_status", "tool_never"):
        tool, wanted = str(arg).split()
        statuses = _statuses(turn, tool)
        options = wanted.split("|")
        if kind == "tool_status":
            return CheckResult(f"{tool} returned {wanted}", any(s in options for s in statuses),
                               f"{tool} returned: {', '.join(statuses) or 'not called'}")
        return CheckResult(f"{tool} never returned {' or '.join(options)}", not any(s in options for s in statuses),
                           f"{tool} returned: {', '.join(statuses)}")
    if kind == "tool_has":
        values_found = [_at_path(d, arg["path"]) for name, d in turn.results if name == arg["tool"]]
        pattern = arg.get("pattern")
        ok = any(v and (not pattern or re.search(pattern, str(v), re.I)) for v in values_found)
        name = f"{arg['tool']} result has {arg['path']}" + (f" matching '{pattern}'" if pattern else "")
        return CheckResult(name, ok, f"found: {values_found}"[:300])
    if kind == "tool_lacks":
        hits = [name for name, d in turn.results if name == arg["tool"]
                and re.search(arg["pattern"], json.dumps(d, ensure_ascii=False), re.I)]
        return CheckResult(f"{arg['tool']} results don't contain '{arg['pattern']}'", not hits,
                           f"{len(hits)} result(s) contain it")
    if kind == "reply_has":
        return CheckResult(f"reply mentions '{arg}'", re.search(arg, reply, re.I) is not None, reply[:200])
    if kind == "reply_lacks":
        found = re.search(arg, reply, re.I)
        return CheckResult(f"reply doesn't mention '{arg}'", found is None, f"found '{found.group(0)}'" if found else "")
    if kind == "reply_has_ref":
        record = await (_order_of(run, None) if arg == "order" else _booking_of(run, None))
        ref = (order_ref if arg == "order" else booking_ref)(record.id) if record else None
        return CheckResult(f"reply gives the {arg} number", bool(ref and ref in reply), f"{arg} number: {ref or 'none'}")
    if kind == "reply_offers_other_time":
        times = {t.zfill(5) for t in re.findall(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b", reply)} - set(arg)
        return CheckResult(f"reply offers a time other than {', '.join(arg)}", bool(times),
                           f"times in reply: {', '.join(sorted(times)) or 'none'}")
    if kind in ("orders", "bookings"):
        n = await _count(run, Order if kind == "orders" else Booking)
        return CheckResult(f"customer has {arg} {kind}", n == int(arg), f"has {n}")
    if kind == "order":
        ref = fill(arg["ref"], values) if arg.get("ref") else None
        order = await _order_of(run, ref)
        name = f"order {ref or '(newest)'}: " + ", ".join(
            f"{k} {v}" for k, v in arg.items() if k not in ("ref", "label"))
        if order is None:
            return CheckResult(name, False, "no such order")
        async with session_factory()() as db:
            d = await order_rules.order_details(db, order, run.org.tz)
        items = sorted((i["sku"], i["quantity"]) for i in d["items"])
        problems = []
        if "status" in arg and d["status"] != arg["status"]:
            problems.append(f"status {d['status']}")
        if "items" in arg and items != sorted((s, int(q)) for s, q in arg["items"]):
            problems.append("items " + ", ".join(f"{q} × {s}" for s, q in items))
        if "total" in arg and Decimal(d["total"]) != Decimal(str(arg["total"])):
            problems.append(f"total {d['total']}")
        return CheckResult(name, not problems, "; ".join(problems))
    if kind == "booking":
        ref = fill(arg["ref"], values) if arg.get("ref") else None
        booking = await _booking_of(run, ref)
        name = f"booking {ref or '(newest)'}: " + ", ".join(
            f"{k} {v}" for k, v in arg.items() if k not in ("ref", "label"))
        if booking is None:
            return CheckResult(name, False, "no such booking")
        async with session_factory()() as db:
            d = await booking_rules.booking_details(db, booking, run.org.config)
        starts, ends = run.local(booking.starts_at), run.local(booking.ends_at)
        problems = []
        if "status" in arg and booking.status != arg["status"]:
            problems.append(f"status {booking.status}")
        if "day" in arg and starts.date() != days[arg["day"]]:
            problems.append(f"day {starts:%a %d %b}")
        if "start" in arg and f"{starts:%H:%M}" != arg["start"]:
            problems.append(f"starts {starts:%H:%M}")
        if "end" in arg and f"{ends:%H:%M}" != arg["end"]:
            problems.append(f"ends {ends:%H:%M}")
        if "practitioner" in arg and arg["practitioner"].lower() not in d["practitioner"].lower():
            problems.append(f"practitioner {d['practitioner']}")
        if "fee" in arg and (booking.cancellation_fee is None or booking.cancellation_fee != Decimal(str(arg["fee"]))):
            problems.append(f"fee {booking.cancellation_fee}")
        return CheckResult(name, not problems, "; ".join(problems))
    if kind == "stock":
        async with session_factory()() as db:
            live = {p.sku: p.stock for p in await db.scalars(
                select(Product).where(Product.org_id == run.org.id, Product.sku.in_(list(arg))))}
        wrong = [f"{sku} {live.get(sku)}" for sku, n in arg.items() if live.get(sku) != int(n)]
        return CheckResult("stock " + ", ".join(f"{s} = {n}" for s, n in arg.items()), not wrong, "; ".join(wrong))
    if kind == "slots":
        slots = [s for name, d in turn.results if name == "find_available_slots" for s in d.get("slots", [])]
        allowed_days = {days[n].isoformat() for n in arg.get("days", [])}
        bad = [s for s in slots if (arg.get("practitioner") and s["practitioner"] != arg["practitioner"])
               or (allowed_days and s["date"] not in allowed_days)
               or (arg.get("from") and s["start"] < arg["from"]) or (arg.get("to") and s["start"] > arg["to"])]
        rules = [f"with {arg['practitioner']}"] if arg.get("practitioner") else []
        if allowed_days:
            rules.append("on " + " or ".join(sorted(allowed_days)))
        if arg.get("from") or arg.get("to"):
            rules.append(f"starting {arg.get('from', '')}-{arg.get('to', '')}")
        detail = f"{len(slots)} slots, {len(bad)} outside: " + ", ".join(
            f"{s['date']} {s['start']} {s['practitioner']}" for s in bad[:3])
        return CheckResult("free slots found, all " + ", ".join(rules), bool(slots) and not bad, detail)
    if kind == "other_org_chat":
        response = await run.client.post(f"/api/orgs/{arg}/chat", json={"message": "Hello"})
        return CheckResult(f"{arg} chat endpoint refuses this customer (403)", response.status_code == 403,
                           f"got {response.status_code}")
    raise ValueError(f"unknown check {kind!r}")


async def run_checks(specs: list[dict[str, Any]], turn: Turn, run: Run, days: dict[str, date],
                     values: Placeholders) -> list[CheckResult]:
    results = []
    for spec in specs or []:
        label = spec.get("label")
        for kind, arg in spec.items():
            if kind == "label":
                continue
            result = await check_one(kind, arg, turn, run, days, values)
            if label:
                result.name = label
            results.append(result)
    return results


# ---------------------------------------------------------------------------
# Running a case
# ---------------------------------------------------------------------------


async def run_case(client: httpx.AsyncClient, case: Case, run_id: str) -> tuple[list[Row], str | None]:
    org = await load_org(case.org)
    run = Run(client, app.state.supabase_auth, org, run_id, label=case.id)
    client.cookies.clear()
    rows: list[Row] = []
    error = None
    try:
        seed: dict[str, int] = {}
        if org.config.agent.toolset == "orders":
            await reset_stock(case.org)
            seed = await seed_stock(case.org)
        await run.sign_up()
        days, values = await date_values(run)
        setup = await apply_setup(run, case, days, values)
        for n, turn_spec in enumerate(case.turns, 1):
            question = fill(turn_spec["ask"], values)
            turn = await run.say(question)
            checks = [CheckResult("turn completed", False, turn.error)] if turn.error else []
            checks += await run_checks(turn_spec.get("checks", []), turn, run, days, values)
            rows.append(Row(
                case=case, turn_number=n, question=question,
                expected_answer=fill(turn_spec.get("expect_answer"), values),
                actual_answer=turn.reply or (f"(error: {turn.error})" if turn.error else "(no reply)"),
                expected_tools=fill(turn_spec.get("expect_tools"), values),
                actual_tools=describe_tools(turn),
                expected_db=fill(turn_spec.get("expect_db"), values),
                actual_db=await database_state(run, seed),
                setup_text="\n".join(setup),
                checks=checks,
            ))
    except Exception as exc:  # setup or the chat crashed: reported in the table
        error = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            await run.clean_up()
            if org.config.agent.toolset == "orders":
                # Deleting the case's orders doesn't release their stock: put the catalogue back.
                await reset_stock(case.org)
        except Exception as exc:
            error = (error or "") + f" | clean-up failed: {exc}"
    if error and len(rows) < len(case.turns):
        rows.append(Row(case=case, turn_number=len(rows) + 1, actual_answer=f"(case crashed: {error})",
                        checks=[CheckResult("case ran to the end", False, error)]))
    return rows, error


async def run_with_retry(client: httpx.AsyncClient, case: Case, run_id: str) -> list[Row]:
    """Runs a case once more if a database or network connection dropped: that's infrastructure."""
    rows, error = await run_case(client, case, run_id)
    if error and "connection" in error.lower():
        print(f"         (connection dropped: {error[:100]}; retrying once)")
        rows, error = await run_case(client, case, run_id)
    return rows


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


# Each turn is laid out as a small Expected | Actual table with these rows, so every expected value
# sits next to its actual one instead of eight wide columns side by side.
FIELDS = [("Answer", "expected_answer", "actual_answer"),
          ("Tools", "expected_tools", "actual_tools"),
          ("Database after the turn", "expected_db", "actual_db")]
EXPLANATION = (
    "Each scenario starts with a fresh customer. A scenario passes when every check in every turn passes: "
    "fixed code checks on the database state, the tools' results and key phrases in the reply (no LLM judge). "
    "For every turn the report shows what the customer said, then the expected and actual answer, tools, "
    "database state and checks side by side. Actual tools are in call order, each with its status and key "
    "details. The database state is read right after the turn: every order and booking of the customers the "
    "scenario created, and any stock that differs from the seed."
)
ORG_NAMES = {"himali-tea": "Himali Tea House", "lotus-wellness": "Lotus Wellness Studio"}


def by_case(rows: list[Row]) -> list[tuple[Case, list[Row]]]:
    cases: dict[str, list[Row]] = {}
    for r in rows:
        cases.setdefault(r.case.id, []).append(r)
    return [(turns[0].case, turns) for turns in cases.values()]


def summary(rows: list[Row]) -> list[tuple[str, list[tuple[Case, bool, list[str]]]]]:
    """[(org name, [(case, passed, failed check names)])]."""
    out: dict[str, list[tuple[Case, bool, list[str]]]] = {}
    for case, turns in by_case(rows):
        failed = [f"turn {r.turn_number}: {c.name}" for r in turns for c in r.checks if not c.ok]
        out.setdefault(ORG_NAMES.get(case.org, case.org), []).append((case, case_passed(turns), failed))
    return list(out.items())


def checks_text(row: Row) -> tuple[str, str]:
    """The Checks row: what is checked, and how each check came out."""
    expected = "\n".join(f"• {c.name}" for c in row.checks) or "(no checks)"
    actual = "\n".join(("✅ " if c.ok else "❌ ") + c.name + ("" if c.ok or not c.detail else f"  →  {c.detail}")
                       for c in row.checks) or "(no checks)"
    return expected, actual


def grouped(rows: list[Row]) -> list[tuple[str, list[tuple[Case, list[Row]]]]]:
    """[(category, [(case, its rows in turn order)])], in the order the cases ran."""
    by_category: dict[str, dict[str, list[Row]]] = {}
    for r in rows:
        by_category.setdefault(r.case.category, {}).setdefault(r.case.id, []).append(r)
    return [(category, [(turns[0].case, turns) for turns in cases.values()]) for category, cases in by_category.items()]


def headline(rows: list[Row], model: str, started: datetime) -> str:
    return (f"Run {started:%Y-%m-%d %H:%M} · model {model} · {len({r.case.id for r in rows})} cases, "
            f"{len(rows)} turns · generated by python -m evals from evals/cases.yaml")


def md_cell(text: str, limit: int | None = None) -> str:
    text = text.strip()
    if limit and len(text) > limit:
        text = text[:limit] + f" … (cut at {limit} characters; full text in table_results.html)"
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\r", "").replace("\n", "<br>")


def write_markdown(rows: list[Row], model: str, started: datetime) -> None:
    lines = ["# Eval results", "", headline(rows, model, started), "", EXPLANATION, ""]
    for org, results in summary(rows):
        lines += [f"## {org}: {sum(p for _, p, _ in results)}/{len(results)} passed", "",
                  "| # | Scenario | Category | Result | Failed checks |", "|---|---|---|---|---|"]
        for n, (case, passed, failed) in enumerate(results, 1):
            lines.append(f"| {n} | [{case.id}](#{case.id}): {md_cell(case.description)} | {case.category} | "
                         f"{'✅ PASS' if passed else '❌ FAIL'} | {md_cell('; '.join(failed))} |")
        lines.append("")
    passed_by_case = {case.id: case_passed(turns) for case, turns in by_case(rows)}
    for category, cases in grouped(rows):
        lines += [f"## {category}", ""]
        for case, turns in cases:
            badge = "✅ PASS" if passed_by_case[case.id] else "❌ FAIL"
            lines += [f'<a id="{case.id}"></a>', "", f"### {badge} · {case.id}: {case.description}", "",
                      f"`{case.org}`", ""]
            if turns[0].setup_text:
                lines += ["**Setup:**", ""] + [f"- {s}" for s in turns[0].setup_text.split("\n")] + [""]
            for r in turns:
                lines += [f"**Turn {r.turn_number}.** Customer: *{r.question.strip() or '(not sent)'}*", "",
                          "| | Expected | Actual |", "|---|---|---|"]
                for label, expected, actual in FIELDS:
                    limit = MD_ANSWER_LIMIT if actual == "actual_answer" else None
                    lines.append(f"| **{label}** | {md_cell(getattr(r, expected))} | {md_cell(getattr(r, actual), limit)} |")
                expected_checks, actual_checks = checks_text(r)
                lines += [f"| **Checks** | {md_cell(expected_checks)} | {md_cell(actual_checks)} |", ""]
    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")


HTML_STYLE = """
:root { --bg:#f7f7f5; --card:#fff; --text:#1d1d1b; --muted:#6b6b66; --line:#e3e2dd; --expected:#f1f0eb;
        --accent:#2f5d8a; --you:#e8eef5; }
@media (prefers-color-scheme: dark) { :root { --bg:#161615; --card:#1f1f1d; --text:#e8e7e3; --muted:#9b9a94;
        --line:#34342f; --expected:#262623; --accent:#8db4dc; --you:#22303d; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text); font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }
main { max-width:1200px; margin:0 auto; padding:24px 16px 64px; }
h1 { margin:0 0 4px; font-size:26px; } h2 { margin:40px 0 12px; font-size:20px; border-bottom:1px solid var(--line); padding-bottom:6px; }
h3 { margin:0; font-size:16px; } a { color:var(--accent); text-decoration:none; } a:hover { text-decoration:underline; }
.meta, .muted { color:var(--muted); font-size:13px; }
nav { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 16px; margin:16px 0; }
nav div { margin:4px 0; } nav b { display:inline-block; min-width:150px; }
nav a { display:inline-block; margin:2px 10px 2px 0; font-size:13px; }
article { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px; margin:14px 0; }
.head { display:flex; flex-wrap:wrap; gap:8px; align-items:baseline; margin-bottom:8px; }
.tag { font-size:12px; padding:1px 8px; border-radius:99px; border:1px solid var(--line); color:var(--muted); }
.setup { font-size:13px; margin:6px 0 10px; padding-left:18px; color:var(--muted); }
.turn { margin-top:14px; }
.ask { background:var(--you); border-radius:8px; padding:8px 12px; margin-bottom:8px; }
.ask b { font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); margin-right:6px; }
table { width:100%; border-collapse:collapse; table-layout:fixed; }
th, td { border:1px solid var(--line); padding:8px 10px; vertical-align:top; text-align:left; }
th { font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); font-weight:600; }
th.label, td.label { width:120px; font-weight:600; font-size:13px; }
td.expected { background:var(--expected); }
.text { white-space:pre-wrap; overflow-wrap:anywhere; }
.answer { max-height:24em; overflow:auto; }
.badge { font-size:12px; font-weight:700; padding:2px 9px; border-radius:99px; }
.pass { background:#dcefe0; color:#1f6b35; } .fail { background:#f6dede; color:#9b2c2c; }
@media (prefers-color-scheme: dark) { .pass { background:#1f3a27; color:#8fd3a3; } .fail { background:#432222; color:#f0a3a3; } }
.summary { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 16px; margin:16px 0; }
.summary h2 { margin:6px 0 10px; border:0; font-size:18px; }
.summary table { margin-bottom:12px; } .summary td { font-size:13px; }
.summary td.n { width:36px; } .summary td.res { width:80px; }
.check-fail { color:#9b2c2c; } @media (prefers-color-scheme: dark) { .check-fail { color:#f0a3a3; } }
@media (max-width:700px) { th.label, td.label { width:80px; } th, td { padding:6px; font-size:13px; } }
"""


def write_html(rows: list[Row], model: str, started: datetime) -> None:
    from html import escape

    def badge(passed: bool) -> str:
        return "<span class='badge pass'>PASS</span>" if passed else "<span class='badge fail'>FAIL</span>"

    groups = grouped(rows)
    out = ["<!doctype html><html lang='en'><head><meta charset='utf-8'>",
           "<meta name='viewport' content='width=device-width, initial-scale=1'>",
           f"<title>Eval results</title><style>{HTML_STYLE}</style></head><body><main>",
           "<h1>Eval results</h1>", f"<div class='meta'>{escape(headline(rows, model, started))}</div>",
           f"<p class='muted'>{escape(EXPLANATION)}</p><div class='summary'>"]
    for org, results in summary(rows):
        out.append(f"<h2>{escape(org)}: {sum(p for _, p, _ in results)}/{len(results)} passed</h2><table>"
                   "<tr><th>#</th><th>Scenario</th><th>Category</th><th>Result</th><th>Failed checks</th></tr>")
        for n, (case, passed, failed) in enumerate(results, 1):
            out.append(f"<tr><td class='n'>{n}</td><td><a href='#{escape(case.id)}'>{escape(case.id)}</a><br>"
                       f"<span class='muted'>{escape(case.description)}</span></td><td>{escape(case.category)}</td>"
                       f"<td class='res'>{badge(passed)}</td>"
                       f"<td><div class='text'>{escape(chr(10).join(failed))}</div></td></tr>")
        out.append("</table>")
    out.append("</div>")
    passed_by_case = {case.id: case_passed(turns) for case, turns in by_case(rows)}
    for category, cases in groups:
        out.append(f"<h2>{escape(category)}</h2>")
        for case, turns in cases:
            out.append(f"<article id='{escape(case.id)}'><div class='head'>{badge(passed_by_case[case.id])}"
                       f"<h3>{escape(case.id)}</h3>"
                       f"<span>{escape(case.description)}</span><span class='tag'>{escape(case.org)}</span></div>")
            if turns[0].setup_text:
                out.append("<div class='muted'>Setup</div><ul class='setup'>"
                           + "".join(f"<li>{escape(s)}</li>" for s in turns[0].setup_text.split("\n")) + "</ul>")
            for r in turns:
                out.append(f"<div class='turn'><div class='ask'><b>Turn {r.turn_number} · customer</b>"
                           f"{escape(r.question.strip() or '(not sent)')}</div>"
                           "<table><tr><th class='label'></th><th>Expected</th><th>Actual</th></tr>")
                for label, expected, actual in FIELDS:
                    extra = " answer" if actual == "actual_answer" else ""
                    out.append(f"<tr><td class='label'>{escape(label)}</td>"
                               f"<td class='expected'><div class='text'>{escape(getattr(r, expected).strip())}</div></td>"
                               f"<td><div class='text{extra}'>{escape(getattr(r, actual).strip())}</div></td></tr>")
                expected_checks, _ = checks_text(r)
                outcomes = "".join(
                    f"<div{'' if c.ok else ' class=check-fail'}>{'✅' if c.ok else '❌'} {escape(c.name)}"
                    + ("" if c.ok or not c.detail else f"<br><span class='muted'>&nbsp;&nbsp;→ {escape(c.detail)}</span>")
                    + "</div>" for c in r.checks) or "(no checks)"
                out.append(f"<tr><td class='label'>Checks</td><td class='expected'><div class='text'>"
                           f"{escape(expected_checks)}</div></td><td>{outcomes}</td></tr>")
                out.append("</table></div>")
            out.append("</article>")
    out.append("</main></body></html>")
    REPORT_HTML.write_text("\n".join(out), encoding="utf-8")


def write_reports(rows: list[Row], model: str, started: datetime) -> None:
    write_html(rows, model, started)
    write_markdown(rows, model, started)


# ---------------------------------------------------------------------------


def load_cases() -> list[Case]:
    raw = (yaml.safe_load(CASES.read_text(encoding="utf-8")) or {}).get("scenarios") or []
    cases = [Case(id=c["id"], org=c["org"], category=c["category"], description=c.get("description", ""),
                  turns=c["turns"], setup=c.get("setup") or []) for c in raw]
    ids = [c.id for c in cases]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise SystemExit(f"Duplicate case ids in {CASES.name}: {', '.join(sorted(duplicates))}")
    return cases


async def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # the Windows console otherwise can't print → or ×
    parser = argparse.ArgumentParser(description="Run evals/cases.yaml and write the expected-vs-actual table.")
    parser.add_argument("--org", choices=["himali-tea", "lotus-wellness"], help="only this org's cases")
    parser.add_argument("--only", help="comma-separated case ids")
    parser.add_argument("--list", action="store_true", help="list the cases and stop")
    parser.add_argument("--dry-run", action="store_true", help="placeholder agent (no LLM calls), to test the setup")
    args = parser.parse_args()
    if args.dry_run:
        os.environ["LLM_PROVIDER"] = "placeholder"
        get_settings.cache_clear()

    cases = [c for c in load_cases() if not args.org or c.org == args.org]
    if args.only:
        names = {n.strip() for n in args.only.split(",")}
        unknown = names - {c.id for c in cases}
        if unknown:
            raise SystemExit(f"Unknown case ids: {', '.join(sorted(unknown))}")
        cases = [c for c in cases if c.id in names]
    if args.list:
        for c in cases:
            print(f"{c.category:22} {c.org:15} {c.id:32} {len(c.turns)} turn(s)  {c.description}")
        return

    settings = get_settings()
    if settings.llm_provider == "placeholder" and not args.dry_run:
        raise SystemExit("LLM_PROVIDER=placeholder gives canned replies: set a real provider to run the evals.")
    model = f"{settings.llm_provider}/{settings.llm_model or DEFAULT_MODELS.get(settings.llm_provider, 'canned')}"
    started = datetime.now()
    run_id = started.strftime("%m%d%H%M")
    print(f"{len(cases)} cases · {model}\n")

    rows: list[Row] = []
    async with app.router.lifespan_context(app):
        try:
            stale = await delete_stale_eval_users(app.state.supabase_auth)
        except Exception as exc:  # a dropped connection at start-up: try once more before giving up
            print(f"(start-up clean-up failed: {type(exc).__name__}; retrying once)")
            await asyncio.sleep(5)
            stale = await delete_stale_eval_users(app.state.supabase_auth)
        if stale:
            print(f"(removed {stale} eval users left by an interrupted run)\n")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://eval.test", timeout=300) as client:
            for case in cases:
                began = clock.monotonic()
                case_rows = await run_with_retry(client, case, run_id)
                rows += case_rows
                crashed = any(r.actual_answer.startswith("(case crashed") for r in case_rows)
                checks = [c for r in case_rows for c in r.checks]
                status = "ERROR" if crashed else ("PASS" if case_passed(case_rows) else "FAIL")
                print(f"{status:5}  {case.org:15} {case.id:32} {sum(c.ok for c in checks):2}/{len(checks):<2} checks "
                      f"{clock.monotonic() - began:5.0f}s")
                if crashed:
                    print(f"         {case_rows[-1].actual_answer[:200]}")
                for r in case_rows:
                    for c in r.checks:
                        if not c.ok:
                            print(f"         x turn {r.turn_number}: {c.name}" + (f"  [{c.detail[:140]}]" if c.detail else ""))
                if args.dry_run:  # what the setup made, since a dry run writes no report
                    if case_rows[0].setup_text:
                        print("         setup: " + case_rows[0].setup_text.replace("\n", "\n                "))
                    print("         db:    " + case_rows[-1].actual_db.replace("\n", "\n                "))

    if args.dry_run:
        crashed = sum(r.actual_answer.startswith("(case crashed") for r in rows)
        print(f"\nDry run: {crashed} case(s) crashed (should be 0). No report written, so the last real one is kept.")
        return
    write_reports(rows, model, started)
    for org, results in summary(rows):
        print(f"\n{org}: {sum(p for _, p, _ in results)}/{len(results)} passed", end="")
    print()
    print_report_paths()


def print_report_paths() -> None:
    print(f"\nOpen in a browser: {REPORT_HTML}\nMarkdown:          {REPORT_MD}")


if __name__ == "__main__":
    asyncio.run(main())
