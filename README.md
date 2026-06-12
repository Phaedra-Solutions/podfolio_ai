# Podfolio v2 – FastAPI Backend

FastAPI backend connected to Supabase (PostgreSQL).

## Project Structure

```
podfolio_v2/
├── app/
│   ├── api/v1/
│   │   ├── endpoints/      # Route handlers
│   │   └── router.py       # API v1 router
│   ├── core/
│   │   └── config.py       # Settings (pydantic-settings)
│   ├── db/
│   │   ├── base.py         # SQLAlchemy Base + model imports
│   │   └── session.py      # Async engine + session factory
│   ├── models/             # SQLAlchemy ORM models
│   ├── schemas/            # Pydantic request/response schemas
│   ├── services/           # Business logic
│   └── main.py             # FastAPI app entry point
├── migrations/             # Alembic migrations
├── tests/
├── .env                    # Local environment variables (git-ignored)
├── .env.example
├── alembic.ini
└── requirements.txt
```

## Quickstart

```bash
# 1. Create and activate virtualenv
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Copy env and fill in credentials
cp .env.example .env

# 4. Run the dev server
uvicorn app.main:app --reload

# 5. Open docs
open http://127.0.0.1:8000/docs
```

## Useful Commands

```bash
# Run dev server
uvicorn app.main:app --reload

# Create a new migration
alembic revision --autogenerate -m "describe change"

# Apply migrations
alembic upgrade head

# Rollback one migration
alembic downgrade -1
```

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Welcome message |
| GET | `/api/v1/health` | App health check |
| GET | `/api/v1/health/db` | Database connectivity check |
| GET | `/docs` | Swagger UI |
| GET | `/redoc` | ReDoc UI |
