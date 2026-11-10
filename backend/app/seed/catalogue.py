"""Loads an org's structured data from its seed folder. Safe to run again.

The seed files are the source of truth: re-running updates names, prices, stock, hours and so
on to match them. Products and services removed from a file are not deleted, since past orders
and bookings may refer to them.
"""

import uuid
from datetime import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Holiday,
    OpeningHours,
    Organization,
    Practitioner,
    PractitionerService,
    Product,
    Service,
)
from app.tenancy.org_config import OrgConfig


def read_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


async def upsert_org(db: AsyncSession, org_dir: Path) -> uuid.UUID:
    org = read_yaml(org_dir / "org.yaml")
    slug, name = org.pop("slug"), org.pop("name")
    # Everything else is the org's config; validating here catches a typo before it's stored.
    config = OrgConfig.model_validate(org).model_dump(mode="json")
    stmt = insert(Organization).values(id=uuid.uuid4(), slug=slug, name=name, config=config)
    stmt = stmt.on_conflict_do_update(
        index_elements=["slug"], set_={"name": stmt.excluded.name, "config": stmt.excluded.config}
    )
    # RETURNING gives the id whether the row was inserted or already existed.
    return (await db.execute(stmt.returning(Organization.id))).scalar_one()


async def load_catalogue(db: AsyncSession, org_id: uuid.UUID, org_dir: Path) -> dict[str, int]:
    """Loads whichever catalogue files the org has. Returns a count per kind."""
    counts: dict[str, int] = {}

    if (path := org_dir / "products.yaml").exists():
        products = read_yaml(path)
        stmt = insert(Product).values(
            [
                {"id": uuid.uuid4(), "org_id": org_id, "sku": p["sku"], "name": p["name"],
                 "category": p["category"], "price": Decimal(str(p["price"])), "stock": p["stock"]}
                for p in products
            ]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["org_id", "sku"],
            set_={col: stmt.excluded[col] for col in ("name", "category", "price", "stock")},
        )
        await db.execute(stmt)
        counts["products"] = len(products)

    if (path := org_dir / "services.yaml").exists():
        services = read_yaml(path)
        stmt = insert(Service).values(
            [
                {"id": uuid.uuid4(), "org_id": org_id, "name": s["name"],
                 "duration_minutes": s["duration_minutes"], "price": Decimal(str(s["price"]))}
                for s in services
            ]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["org_id", "name"],
            set_={col: stmt.excluded[col] for col in ("duration_minutes", "price")},
        )
        await db.execute(stmt)
        counts["services"] = len(services)

    if (path := org_dir / "practitioners.yaml").exists():
        practitioners = read_yaml(path)
        await db.execute(
            insert(Practitioner)
            .values([{"id": uuid.uuid4(), "org_id": org_id, "name": p["name"]} for p in practitioners])
            .on_conflict_do_nothing(index_elements=["org_id", "name"])
        )
        practitioner_rows = await db.execute(
            select(Practitioner.name, Practitioner.id).where(Practitioner.org_id == org_id)
        )
        practitioner_ids = {name: id_ for name, id_ in practitioner_rows}
        service_rows = await db.execute(select(Service.name, Service.id).where(Service.org_id == org_id))
        service_ids = {name: id_ for name, id_ in service_rows}
        # Who offers what is replaced wholesale, so removing a service from a file takes effect.
        await db.execute(delete(PractitionerService).where(PractitionerService.org_id == org_id))
        links = []
        for p in practitioners:
            for service_name in p["services"]:
                if service_name not in service_ids:
                    raise ValueError(f"{p['name']} offers unknown service {service_name!r}")
                links.append({"org_id": org_id, "practitioner_id": practitioner_ids[p["name"]],
                              "service_id": service_ids[service_name]})
        await db.execute(insert(PractitionerService).values(links))
        counts["practitioners"] = len(practitioners)

    if (path := org_dir / "opening_hours.yaml").exists():
        hours = read_yaml(path)
        # Replaced wholesale: a weekday missing from the file becomes a closed day.
        await db.execute(delete(OpeningHours).where(OpeningHours.org_id == org_id))
        await db.execute(
            insert(OpeningHours).values(
                [
                    {"id": uuid.uuid4(), "org_id": org_id, "weekday": h["weekday"],
                     "opens_at": time.fromisoformat(h["opens_at"]), "closes_at": time.fromisoformat(h["closes_at"])}
                    for h in hours
                ]
            )
        )
        counts["opening hours"] = len(hours)

    if (path := org_dir / "holidays.yaml").exists():
        holidays = read_yaml(path)
        await db.execute(delete(Holiday).where(Holiday.org_id == org_id))
        await db.execute(
            insert(Holiday).values(
                [{"id": uuid.uuid4(), "org_id": org_id, "date": h["date"], "name": h["name"]} for h in holidays]
            )
        )
        counts["holidays"] = len(holidays)

    return counts
