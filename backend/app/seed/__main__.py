"""Seeds every org in backend/seed/orgs/: its catalogue, test customers and knowledge base.

Run from backend/:
    uv run python -m app.seed              catalogue, customers and knowledge base
    uv run python -m app.seed --skip-kb    catalogue and customers only (no Mistral API key needed)

Safe to run again. Each org's knowledge base is rebuilt from its kb/*.md files.
"""

import argparse
import asyncio
from pathlib import Path

from app.auth.supabase import SupabaseAuth
from app.config import get_settings
from app.db.session import session_factory
from app.retrieval.ingest import lock_down_kb_table, rebuild_org_kb
from app.retrieval.store import get_embed_model
from app.seed.catalogue import load_catalogue, read_yaml, upsert_org
from app.seed.customers import seed_customers
from app.tenancy.org_config import OrgConfig

SEED_DIR = Path(__file__).resolve().parents[2] / "seed" / "orgs"


async def seed(skip_kb: bool) -> None:
    if not skip_kb:
        get_embed_model()  # fails now, with a clear message, if MISTRAL_API_KEY is missing
    settings = get_settings()
    auth = SupabaseAuth(
        url=settings.supabase_url,
        publishable_key=settings.supabase_publishable_key.get_secret_value(),
        secret_key=settings.supabase_secret_key.get_secret_value(),
    )
    try:
        org_dirs = sorted(path for path in SEED_DIR.iterdir() if (path / "org.yaml").exists())
        for org_dir in org_dirs:
            # The catalogue is one transaction per org: seeded completely or not at all.
            async with session_factory()() as db:
                org_id = await upsert_org(db, org_dir)
                counts = await load_catalogue(db, org_id, org_dir)
                await db.commit()
            # Customers need the catalogue (products, services) committed first.
            org = read_yaml(org_dir / "org.yaml")
            config = OrgConfig.model_validate({k: v for k, v in org.items() if k not in ("slug", "name")})
            async with session_factory()() as db:
                counts |= await seed_customers(db, auth, org_id, org["slug"], config, org_dir)
                await db.commit()
            summary = ", ".join(f"{n} {kind}" for kind, n in counts.items())
            if not skip_kb and (org_dir / "kb").is_dir():
                chunks = await rebuild_org_kb(org_id, org_dir / "kb")
                summary += f", {chunks} knowledge-base chunks"
            print(f"{org_dir.name}: {summary}")
        if not skip_kb:
            await lock_down_kb_table()
    finally:
        await auth.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed organisations, catalogues, customers and knowledge bases.")
    parser.add_argument("--skip-kb", action="store_true", help="don't rebuild the knowledge bases")
    asyncio.run(seed(parser.parse_args().skip_kb))


main()
