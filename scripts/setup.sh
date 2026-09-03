#!/usr/bin/env bash
# One-shot local setup for Soirée's backend.
#
#   ./scripts/setup.sh
#
# Brings up Postgres + Redis, installs Python deps, applies migrations,
# and seeds the demo user. Safe to re-run.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

echo "→ starting Postgres + Redis"
docker compose up -d db redis

echo "→ waiting for Postgres"
until docker compose exec -T db pg_isready -U postgres >/dev/null 2>&1; do
  sleep 1
done

cd backend

if [ ! -f .env ]; then
  echo "→ creating backend/.env from .env.example (fill in ANTHROPIC_API_KEY)"
  cp .env.example .env
fi

echo "→ installing Python dependencies"
pip install -r requirements.txt

echo "→ applying database migrations"
alembic upgrade head

echo "→ seeding demo data"
python ../scripts/seed.py

cat <<'EOF'

Done. Start the API with:

  cd backend && uvicorn app.main:app --reload

EOF
