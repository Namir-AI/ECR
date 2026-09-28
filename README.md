# Paharpur ECR

Internal web application for digitizing Paharpur Cooling Towers Ltd. Erection
& Commissioning Completion Reports.

The current implementation scope is the shared application foundation and the
ECR module only. Budget and Bill modules are not implemented.

## Local prerequisites

- Ubuntu/Linux
- Python 3.12+
- `uv`
- MySQL 8.x with the development database and least-privilege application user

This workspace uses the existing virtual environment at
`/home/nik/service/.venv`. Do not create an `ECR/.venv` environment.

## Install dependencies

From `/home/nik/service/ECR`:

```bash
source ../.venv/bin/activate
uv sync --active --frozen
```

`uv` is development tooling, not an application runtime requirement. The
project uses standard Python packaging and can also be installed by other
PEP 517-compatible tooling.

## Configure the application

Create the uncommitted local settings file:

```bash
cp .env.example .env
```

Edit `.env` locally and replace `DB_PASSWORD` with the development database
password. Do not paste the password into terminal output, tests, source files,
or Git. The `.env` file is ignored by Git.

Configuration uses individual `DB_*` values so passwords containing special
characters do not need manual URL escaping.

## Run locally

With the shared virtual environment active:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Then open:

```text
http://127.0.0.1:8000/health
http://127.0.0.1:8000/auth/signup
http://127.0.0.1:8000/auth/login
```

Expected response:

```json
{"status":"ok"}
```

`/health` is a process-liveness endpoint and deliberately does not depend on
MySQL. Use the separate connectivity check below to verify database readiness.

## Database connectivity

With `.env` configured:

```bash
python -m app.db.check
```

The command reports success or failure without displaying connection
credentials.

## Alembic migrations

Apply all migrations:

```bash
alembic upgrade head
```

Downgrade Phase 2 to the Phase 1 baseline:

```bash
alembic downgrade 20260928_01
```

Reapply it:

```bash
alembic upgrade head
```

Phase 1 contains an empty baseline revision. Phase 2 adds only the shared
authentication and user tables. No ECR, Budget, or Bill business tables exist.

## Create the initial Admin

After applying migrations, run the interactive bootstrap command:

```bash
python -m app.users.create_admin
```

The command prompts for the required identity fields and uses hidden password
input. It does not accept the password as a command-line argument or require it
in `.env`.

For localhost HTTP, `SESSION_SECURE_COOKIE=false` is permitted. Set it to
`true` wherever the application is served over production HTTPS.

If an existing Admin forgets their password, run the server-side interactive
recovery command:

```bash
python -m app.users.reset_admin_password
```

The command accepts no password argument. It uses hidden password prompts and
revokes all existing sessions for the selected Admin.

## Tests

```bash
pytest
```
