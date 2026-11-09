"""Seeds the database. Safe to run again: rows that already exist are left as they are.

Run from backend/:  uv run python -m app.seed
"""

import asyncio

from sqlalchemy.dialects.postgresql import insert

from app.db.models import Organization
from app.db.session import session_factory

ORGANIZATIONS = [
    {"slug": "himali-tea", "name": "Himali Tea House"},
    {"slug": "lotus-wellness", "name": "Lotus Wellness Studio"},
]


async def seed_organizations() -> None:
    async with session_factory()() as db:
        await db.execute(
            insert(Organization).values(ORGANIZATIONS).on_conflict_do_nothing(index_elements=["slug"])
        )
        await db.commit()


async def main() -> None:
    await seed_organizations()
    print(f"Organizations: {', '.join(org['slug'] for org in ORGANIZATIONS)}")


if __name__ == "__main__":
    asyncio.run(main())
