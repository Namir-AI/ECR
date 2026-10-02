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
http://127.0.0.1:8000/dashboard
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

Downgrade Phase 3 to the Phase 2A branch-administration baseline:

```bash
alembic downgrade 2a7c9e4b1d30
```

Reapply it:

```bash
alembic upgrade head
```

Phase 3 adds relational ECR Package, Tower, and Cell Report identity tables.
Phase 4A adds Page 1 technical data; no attachment, Budget, or Bill tables exist.

## Phase 3 Draft flow

An active Supervisor can open the dashboard, select **New Erection &
Commissioning Report**, identify or reuse a Cooling Tower Serial No., and create
a cell-level Draft. The authenticated user and that user's branch at creation
are recorded by the server; neither value is accepted from the browser.

Draft erection dates autosave after changes and persist across reloads. A
Supervisor sees only their own reports. Branch Admins can open the read-only
Reports page for reports historically created in their branch, while the
Superadmin can view all branches. Page 2/Page 3 technical data, submission,
approval, attachments, and advanced report search remain later-phase work.

## Create the initial Superadmin

After applying migrations, run the interactive bootstrap command:

```bash
python -m app.users.create_superadmin
```

The command assigns HO-Kolkata as the home branch, prompts for required identity
fields, and uses hidden password input. The legacy
`python -m app.users.create_admin` command remains a compatibility wrapper.

For localhost HTTP, `SESSION_SECURE_COOKIE=false` is permitted. Set it to
`true` wherever the application is served over production HTTPS.

If an existing Superadmin or Branch Admin forgets their password, run the server-side interactive
recovery command:

```bash
python -m app.users.reset_admin_password
```

The command accepts no password argument. It uses hidden password prompts and
revokes all existing sessions for the selected administrator.

## Tests

```bash
pytest
```

## Phase 4A — Page 1 technical Draft data

Migration `4a8e2c7d901f` follows `3d6a1b8c4e20`. Run `alembic upgrade head`.
It adds `ecr_page1_technical` (one nullable scalar record per report) and
`ecr_fan_blade_serials` (optional ordered blade entries). Motor, Fan, Drive Shaft,
and Gearbox serials have dedicated non-unique indexes.

Open an own Draft to enter Motor, Fan, Fan Cylinder, Drive Shafts, Gearboxes and
Fill. All technical fields start blank. Stars indicate eventual completed-report
requirements, not requirements for saving an incomplete Draft. Autosave and
**Save now** use the same validated, committed snapshot. OAL values require an
explicit cm/inches unit, with no unit conversion; both OAL and unit may remain
blank, but neither may be supplied without the other. Blade serials may be added or
removed; blank entries are omitted and there is no eight-entry/count-match limit.
Branch Admins and Superadmins see technical values read-only within existing
historical branch scope. The visible label **Branch** retains snapshot semantics.

Final-optional fields are Motor Frame, Insulation and Mounting; Blade Sl. Nos.;
Drive Shaft OAL and OAL Unit (optional as a pair); Gearbox Model No.; and Fill
Type (Specify nomenclature). These have no required asterisk and may remain
permanently blank. All other Page 1 fields are final-required. The schema's
`final_required` metadata drives the asterisks and is the source for later final
completeness rules; no submission/completeness workflow is implemented now.
All technical columns remain nullable so incomplete Drafts can always be saved.

When supplied, Full Load Current must be **strictly greater than 0 Amps** and
Current Drawn must be **0 Amps or greater**. Current Drawn = 0 is meaningful data,
not blank/NULL, and is allowed for future final submission when commissioning
cannot occur because the site is not ready or the motor is faulty/not running.
Invalid supplied values reject the save atomically and retain committed data.
Decimal storage capacity is unchanged; zero/negative values are never silently
converted to positive values.

### Deferred controlled Current Drawn correction

After commissioning, a future specific post-submission action must allow an
authorized Branch Admin to correct Motor Current Drawn for reports in that
Admin's **historical report branch scope**, or a Superadmin to do so globally.
This must not unlock unrestricted editing of a submitted ECR. Audit at least:
report ID, old/new Current Drawn, changed-by user ID, role and timestamp.
A reason/comment is recommended but is **not yet approved as mandatory**.
Only this business requirement is recorded now: no route, button, permissions
workflow or audit schema for post-submission correction is implemented in Phase 4A.

Gearbox Series and Ratio intentionally remain text until the owner supplies
complete approved lookup lists; later dropdowns can use these same string columns.
Motor Serial No. is an approved digital addition, not a hardcopy Page 1 field.

Drive Shaft Series is now an owner-approved dropdown with exactly `6Q` and `175`;
it starts unselected, allows blank Drafts, and rejects other supplied values.
The existing string column is unchanged (no migration needed).
Motor HP and Current Drawn accept non-negative decimals; Full Load Current must
be strictly positive. A reusable local input guard prevents signs, exponent
notation and invalid paste/mobile entry without converting negative text to
positive text. Server validation remains authoritative. All three start blank.
HTML minima are 0 for HP/Current Drawn, and `0.000000000000000001` for Full Load
Current (the smallest positive value representable at the existing storage scale,
not a newly invented engineering limit). Zero Current Drawn remains meaningful.

Engineering decimals use `DECIMAL(38,18)` (20 whole-number and 18 fractional
digits). This is storage capacity, not an engineering acceptance limit or a
prescribed number of decimal places. Non-finite or out-of-capacity values are
rejected, never silently rounded. No engineering acceptance warnings are added.
Text fields hold up to 250 characters; integer counts use MySQL BIGINT capacity.

For migration verification on an empty technical schema:

```bash
alembic downgrade 3d6a1b8c4e20
alembic upgrade head
alembic current
alembic check
```

**Downgrade removes Page 1 technical data**; it retains Phase 3 reports, packages,
towers and common users/branches. Do not downgrade a populated installation
without an appropriate backup and explicit authorization.

Browser regressions use optional Playwright/Chromium tooling, separate from the
application runtime and shared development virtual environment:

```bash
uv tool run --from playwright playwright install chromium
PLAYWRIGHT_SITE=$(uv tool run --from playwright python -c 'import pathlib, playwright; print(pathlib.Path(playwright.__file__).resolve().parent.parent)')
PYTHONPATH="$PLAYWRIGHT_SITE" pytest
# Optional visible browser run on a machine with a display:
ECR_BROWSER_HEADED=1 PYTHONPATH="$PLAYWRIGHT_SITE" pytest tests/test_page1_browser.py
```

Without that optional tooling, pytest reports browser modules as skipped.
Page 2/Page 3, attachments, submission/approval, advanced search and PDF remain
outside Phase 4A.
