# Run from the repo root. Backend targets run in backend/ with uv, frontend ones in frontend/ with npm.
.PHONY: install migrate seed seed-catalogue api web eval eval-list

install:
	cd backend && uv sync
	cd frontend && npm install

# The API on http://localhost:8000. Run `make web` in a second terminal.
api:
	cd backend && uv run uvicorn app.main:app --port 8000

# The chat UI on http://localhost:3000 (passes /api to the API above).
web:
	cd frontend && npm run dev

migrate:
	cd backend && uv run alembic upgrade head

# Catalogues, test customers and both knowledge bases (needs MISTRAL_API_KEY for embeddings).
seed: migrate
	cd backend && uv run python -m app.seed

# Everything except the knowledge bases: no embedding calls.
seed-catalogue: migrate
	cd backend && uv run python -m app.seed --skip-kb

# The agent evals: the 24 scenarios in backend/evals/cases.yaml, pass/fail per scenario from fixed
# checks on the database, tools and reply. Open backend/evals/table_results.html (also .md).
# Options with ARGS, e.g. make eval ARGS="--org himali-tea" or ARGS="--only over_stock",
# or ARGS="--dry-run" to test the setup without LLM calls.
eval:
	cd backend && uv run python -m evals $(ARGS)

eval-list:
	cd backend && uv run python -m evals --list
