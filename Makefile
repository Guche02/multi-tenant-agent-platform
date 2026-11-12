# Run from the repo root. Each target runs in backend/ with uv.
.PHONY: install migrate seed seed-catalogue eval eval-list

install:
	cd backend && uv sync

migrate:
	cd backend && uv run alembic upgrade head

# Catalogues, test customers and both knowledge bases (needs MISTRAL_API_KEY for embeddings).
seed: migrate
	cd backend && uv run python -m app.seed

# Everything except the knowledge bases: no embedding calls.
seed-catalogue: migrate
	cd backend && uv run python -m app.seed --skip-kb

# The agent evals: every case in backend/evals/cases.yaml, expected vs actual answer, tools and
# database state. Open backend/evals/table_results.html in a browser (also .md). Not graded.
# Options with ARGS, e.g. make eval ARGS="--org himali-tea" or ARGS="--only over_stock",
# or ARGS="--dry-run" to test the setup without LLM calls.
eval:
	cd backend && uv run python -m evals $(ARGS)

eval-list:
	cd backend && uv run python -m evals --list
