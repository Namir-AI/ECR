# Paharpur ECR

Internal web application for digitizing Paharpur Cooling Towers Ltd. Erection
& Commissioning Completion Reports.

The current implementation scope is the shared application foundation and the
ECR module only. Budget and Bill modules are not implemented.

Private ECR evidence storage is selectable: the existing local protected backend
remains the default; fresh installations may select private S3 using the AWS SDK
credential chain / EC2 IAM role. See [S3 installation and operations](install/S3_STORAGE.md).
MySQL keys/metadata and ECR authorization/workflows are unchanged. No existing
object migration is provided; S3 evidence backup/recovery is an IT responsibility
separate from the mandatory MySQL backup.

Current implemented scope: Phases 1–5 (technical Pages 1–3, optional customer
signature, dashboards, Review/Submit/Approval and scoped exact serial search),
and accepted Phase 6 package attachments. Phase 8B official browser Print / Save
as PDF is accepted. Earlier sections describe migration history, not missing
current workflow/search functionality.

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

The existing `APP_ENV` setting controls FastAPI API documentation. Development
keeps `/docs`, `/redoc`, and `/openapi.json` available. In production, these
routes are disabled and return HTTP 404; `/health` remains available unchanged.

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
Phase 4A adds Page 1 technical data. Phase 6 adds package attachment metadata;
Budget and Bill tables remain absent.

## Phase 3 Draft flow

An active Supervisor can open the dashboard, select **New Erection &
Commissioning Report**, identify or reuse a Cooling Tower Serial No., and create
a cell-level Draft. The authenticated user and that user's branch at creation
are recorded by the server; neither value is accepted from the browser.

Draft erection dates autosave after changes and persist across reloads. A
Supervisor sees only their own reports. Branch Admins can open the read-only
Reports page for reports historically created in their branch, while the
Superadmin can view all branches. Pages 1–3 extend this same Draft flow;
Phase 5 implements submission/approval and exact serial search; Phase 6 adds
optional shared package attachments.

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
permanently blank. All other Page 1 fields are final-required **when their section
applies**. The capture schema's `final_required` metadata is filtered through the
Series applicability helpers for asterisks and future completeness rules;
no submission/completeness workflow is implemented now.
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

For Phase 4A migration verification on an empty technical schema (not a populated
installation, and not the Phase 4B1 round trip described below):

```bash
alembic downgrade 3d6a1b8c4e20
alembic upgrade head
alembic current
alembic check
```

**Downgrade removes Page 1 technical data**; it retains Phase 3 reports, packages,
towers and common users/branches. Do not downgrade a populated installation
without an appropriate backup and explicit authorization.

## Phase 4B1 — Page 2 Batch A+B

Migration `4b1a9c2d7e60` follows `4a8e2c7d901f` and adds only
`ecr_page2_technical`, a one-to-one nullable scalar table for Eliminator, FC
Valves, Nozzles, Bearing Housing, Belt & Pulleys, Lubricant for GRDR / Bearing
Housing, and General Tower Hardware. Run `alembic upgrade head` before startup.
Measurements use the existing `DECIMAL(38,18)` capacity; counts use BIGINT.
Negative measurements, non-positive/fractional counts and unapproved choices are
rejected; blanks remain valid in Drafts. No engineering defaults are supplied.
Leakage **Yes means leakage observed**. General Tower Hardware is single-choice
`HDG`, `SS304`, or `SS316`; `STL/HDG` is not an approved digital value.

Cooling Tower Series uses these exact values, in this order:
`AQ-3800`, `CF-I`, `CF-II`, `CF-III`, `6.1 KF`, `9 KF`, `RXF`, `Series 9`,
`Series 10`, `Series 15`, `Series 18`. New selection starts blank.

| Series | Gearbox / Drive Shaft | Bearing Housing / Belt & Pulleys |
| --- | --- | --- |
| AQ-3800 | Hidden | Applicable |
| CF-I | Hidden | Hidden |
| Other approved Series | Applicable | Hidden |

All other Batch A+B sections apply to every approved Series. Only applicable
fields participate in final-required metadata. Draft saves never enforce final
completeness. Hidden sections are excluded from active saves and read-only
presentation; their committed values are retained and reappear when applicable.
Series is taken from the authoritative package, never a browser visibility flag.

A Supervisor can edit Series **only** as the package creator while its only
report belongs to that Supervisor and remains DRAFT. Creating a second report
or leaving DRAFT locks Series. Creation and Series edits lock the package in a
consistent order to enforce this even during concurrent requests. A shared
package's technical Draft fields remain editable within existing ownership rules.
The future audited SUPERADMIN-only shared-Series correction is deferred.

Changing an already selected Cooling Tower Series requires a styled confirmation
dialog. First selection needs no confirmation; Cancel or Escape keeps the prior
selection, and Change Series passes the accepted value to the existing autosave
controller. A rejected or uncertain Series save re-reads the owner's authoritative
Series through a read-only, uncached endpoint; it restores that selection and
keeps failure feedback visible. If reconciliation is unavailable, further writes
require reload. Model remains read-only on the Draft page. No technical data is
reset by confirming, cancelling or changing Series.

Legacy Series strings are not rewritten. They remain safely readable/reusable
unchanged. An unknown Series has unclassified drivetrain applicability: the UI
explains this and preserves stored equipment data. An eligible sole-Draft creator
may explicitly choose an approved Series. No mapping or destructive conversion
occurs automatically.

Batch A+B uses the existing autosave/Save now controller and commit confirmation,
including stale-response protection. Branch Admins remain read-only within
historical report branch scope; Superadmins have global read-only report access.

For verification **only when the new Page 2 table is empty**:

```bash
alembic downgrade 4a8e2c7d901f
alembic upgrade head
alembic current
alembic check
pytest
```

Downgrade drops Batch A+B data only. Back up populated data before any deliberate
downgrade; accepted Page 1, identity, users and branch tables are not modified.
Workflow/search are implemented in Phase 5 and attachments in Phase 6.
Combined PDF dossier, final Gearbox lists and controlled post-submission Current Drawn correction
remain deferred.

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
Phase 5 implements submission/approval and serial search; Phase 6 implements
attachments. Official browser Print / Save as PDF is accepted in Phase 8B;
server PDF remains dormant.

## Phase 4B2 — Page 2 Batch C

Migration `4b2c8e1f903a` follows `4b1a9c2d7e60`. It extends
`ecr_page2_technical` with nullable alignment, DE/NDE and switch fields and adds
`ecr_fastener_torque_rows`. Earlier migrations and data are unchanged.

Fastener rows are ordered within Fan Hardware, Tower and Mechanical Equipment
Hold Down categories. Add/remove rows without a four-row limit. Only Fan Hardware
requires a completed row for the future final ECR; Draft rows may be partial.
Diameter remains text; torque uses `DECIMAL(38,18)` (20 whole/18 fractional
digits), with no engineering sign or acceptance limit. Unit choices are exactly
`ft-lbs` / `Nm`. Entirely blank rows are not persisted; repeated snapshots do not
append duplicates. Oversized decimals are rejected, not silently rounded.

Radial/Axial T.I.R are optional, manually typed signed decimals without spinners
or displayed units. Range is −1 through +1, at most three fractional places,
stored as `DECIMAL(4,3)`. DE and NDE each have four graphical readings in that
same range, in hundredths (`DECIMAL(3,2)`), with 0.01 adjustment controls. One
shared `inches` / `mm` selector applies to all eight. Zero is meaningful data;
all readings and selectors initially remain blank. No engineering judgment is
made. Vibration Limit Switch and Oil Level Switch require explicit Yes/No for
the future completed report, stored as nullable booleans during Draft.

All capture uses the existing serialized Draft autosave/Save now transaction,
CSRF and ownership rules. Branch Admin and Superadmin report access remains
read-only. Separate snapshot markers preserve Batch C if an older open A+B form
saves. Final-required metadata does not enforce completeness during Draft.

To verify a migration round-trip **only before Batch C data has been entered**:

```bash
alembic upgrade head
alembic downgrade 4b1a9c2d7e60
alembic upgrade head
alembic check
pytest
```

Downgrade removes Batch C only, including torque rows. Back up populated data
and obtain explicit approval before any deliberate downgrade. Existing Page 1,
Page 2 A+B, identity, users and branches are preserved.

Workflow/search and attachments are now implemented in Phases 5 and 6.
Deferred: combined PDF dossier, final Gearbox Series/Ratio lists, audited post-submission
Current Drawn correction, and audited Superadmin shared-Series correction.

## Phase 4C — Page 3 + optional customer signature

Migration `4c6d2e9a103f` follows accepted `4b2c8e1f903a` and adds only
`ecr_page3`, a one-to-one report relation. Plain-text Team-Leader Report and
Customer Comment use nullable MySQL `MEDIUMTEXT`; text preserves paragraphs,
normalizing CRLF/CR to LF. Each has a 100,000-character technical safety limit.
The Team-Leader Report is final-required but may remain blank in Draft;
Customer Comment is permanently optional. Both use existing autosave/Save now,
including commit confirmation and stale-response protection. Older forms without
the Page-3 snapshot marker do not erase Page-3 text or signatures.

**Erected / Commissioned by** and **Name (in Block Letters)** are read-only,
derived from the report's Supervisor, with the latter presented in uppercase.
No separate editable identity is stored. Phase 8B now snapshots the Supervisor's
name and Employee ID at submission for official Print/PDF (see below).

Customer Sign is optional. Sign using finger/stylus/mouse, then explicitly
choose **Save Sign**. **Save now does not save an unsaved sign**. The
responsive pad retains normalized strokes across resizing and exports a
1800×600 PNG. Once saved, only the saved sign and **Replace Sign / Remove Sign**
actions are shown, not another pad. **Replace Sign** opens one replacement pad;
**Clear Sign** discards only unsaved strokes and **Cancel** retains the saved sign.
**Remove Sign** uses confirmation and also clears its timestamp. Replacement is
supported only for the authenticated owner's Draft. No camera, document/image
upload, customer-name/designation field or separate digital seal is implemented.

The server independently decodes PNG pixels, rejects white/transparent blank
drawings, checks dimensions (2048×1024, at most 2 million pixels), strips metadata
and re-encodes. Technical request limits are 2 MiB PNG and 3 MiB JSON. The DB
stores only a random private object key and server-generated UTC `signed_at`
with microseconds. Signature and timestamp must either both exist or both be
NULL; browser timestamps/filenames/flags are rejected.

### Private storage configuration

Development defaults to ignored `var/protected/signatures`. Only an authorized
report route can retrieve a signature; there is no public static mount. Files
use 0600 and the signature directory 0700. Branch Admin access follows the
report's historical branch, not the Supervisor's current branch. Superadmin
has read-only global access. All mutations require owner + Draft + CSRF.

For the local backend, production requires an absolute persistent `STORAGE_ROOT`
in the private .env, outside application release directories. The installer
supplies this automatically. With the existing
installer layout an appropriate path is `/opt/ecr/shared/data/protected`
(adapt to your installation directory). It must be writable by the ECR service
account and must not be publicly served. The production signature routes fail
safely until this is configured. Back up
the protected files **together with the database**; a DB-only restore cannot
restore signature content. The existing shared-data backup layout can include it.

Storage retains its put/read/delete signature interface and streaming attachment
interface. Selectable S3 adapters now implement both; the local implementations
remain intact. See [backend configuration and recovery](install/S3_STORAGE.md).
DB commit precedes
old-object cleanup. A crash/ambiguous commit or cleanup failure may leave an
unreferenced private file. No automatic broad file deletion/garbage collection
is implemented; any later maintenance must reconcile DB references safely.

Migration round-trip verification is safe **only while Page-3 data is empty**:

```bash
alembic downgrade 4b2c8e1f903a
alembic upgrade head
alembic current
alembic check
pytest
```

Downgrade removes Page-3 data, not existing Page-1/2 reports or private files.
Back up populated data and obtain approval before a deliberate downgrade.
Phase 5 locks normal Supervisor signature changes outside DRAFT and implements
Review/Confirm/Submit, Approval and search. Phase 6 adds JCC/Tower Photos.
Combined PDF dossier, final Gearbox lists, controlled Current Drawn correction and audited
shared-Series correction remain deferred.

## Phase 5 — operational dashboards, workflow, search and approval

Migration `5e7a2d9c104b` follows `4c6d2e9a103f`. It adds nullable review,
submission and approval timestamps, the approving user FK, and transactional
`ecr_audit_events`. Completion Date is now nullable for incomplete Cell Drafts;
final Review/Submit validation still requires it. Existing dates/data/statuses
are not converted. Run `alembic upgrade head` and the full suite including
the optional Playwright browser tests described above.

All three dashboards group Package → Tower → Cell. Supervisor visibility is
own reports, including past branches; Branch Admin uses historical report
branch scope; Superadmin may filter all branches. Missing declared Cells are
virtual UI rows, never stored reports. Occupied private Cells are unavailable,
not "Not started". Large declared counts page virtual rows in batches of 50
(a presentation limit, not a Cell No. limit).

Admin dashboard/list rows show **Erected / Submitted By** from the report's
Supervisor relationship (no editable duplicate or historical-name snapshot).
**Search by Erector** supports literal, case-insensitive partial names and
Completion-Date years, combined with existing branch/status filters. SQL counts
distinct Towers and matched Cell reports in the same authorized scope. All years
includes undated Draft/Reviewed work; specific years exclude NULL dates. Partial
names matching several accounts show each account's summary plus scoped totals.
Search results reuse the existing Package → Tower → Cell cards.

Supervisors can create a missing Cell or use **+ Add another Cell** (smallest
positive unused Cell No.). Confirmation creates a blank Draft with no copied
dates, technical data, text or signature. Declared cell count is descriptive and
is never silently increased. A multi-tower package creator can **Add Tower**;
automatic suffix progression stops at Z and creates a blank Cell-1 Draft.
Single-tower packages cannot be converted here. Shared-Series sole-Draft rules
remain unchanged.

Workflow is per Cell: **DRAFT → REVIEWED → SUBMITTED → APPROVED**. Review checks
the centralized owner-approved final-required metadata and supplied-value
validators, grouped by applicable sections. Zero Current Drawn and DE/NDE
readings are valid; optional fields/signature and optional package JCC/photos do not
block. Back to Edit returns REVIEWED to DRAFT and clears `reviewed_at` without
changing data. Confirm & Submit revalidates and locks Supervisor editing.
Branch Admin (historical branch) / Superadmin (global) can explicitly save
report-scoped edits in DRAFT/REVIEWED/SUBMITTED; status remains unchanged.
They cannot change shared identity/Series or signature evidence. Approval
revalidates SUBMITTED data, confirms in an accessible modal, records actor/time
and locks ordinary edits for all roles. Package/report locks serialize these
operations; audit and business writes commit together. Workflow transitions
and meaningful Admin field edits record old/new values, actor/role/report/time;
signature pixels, storage references and authentication secrets are not logged.

**Search Reports** is Admin-only: exact, case-insensitive Cooling Tower base or
operational Serial No., Motor Serial No., Gearbox Serial No., Drive Shaft Serial
No., or All. Queries use existing structured serial indexes and scope before
matching. One authorized result opens the existing full read-only report;
multiple matches show a grouped chooser. Search never bypasses edit locks.

The common responsive sidebar uses the actual owner-supplied Paharpur logo
from `app/static/brand`, local SVG icons, and disabled coming-soon Budget/Bills
entries. The shared footer reads "Developed by: Nazmul Khan". Dashboard reference
images remain outside the repository; production does not depend on owner_data.

Migration round trips must be performed before entering Phase-5 operational
data. Downgrade removes workflow/audit metadata and refuses unresolved NULL
completion dates rather than fabricating dates. Back up populated data before
any deliberate downgrade. Existing Page-1/2/3 content is preserved.

Official Print/PDF and submission snapshots are implemented in Phase 8B below.
Deferred: Budget/Bills, final Gearbox lookup lists,
controlled post-approval Current Drawn correction, audited shared-Series
correction.

## Phase 6 (accepted) — package attachments (JCC + Tower Photos)

Migration `6a2c9e4f107b` follows `5e7a2d9c104b`. It adds
`ecr_jcc_documents`, `ecr_package_attachments` and `ecr_package_audit_events`.
Attachments belong to the **Package**, shared by all Towers/Cells, not to an
individual report. Open **Attachments JCC & Tower Photo** from Draft or full report
views; dashboards/search continue to use the existing report renderer.
The **JCC and Tower Photo Upload** page uses accessible compact file pickers and
one decimal KB/MB presentation helper; exact stored bytes still enforce limits.
The add-photo form is hidden at five photos and returns after removal.

- JCC is one logical document: one single/multi-page PDF, or ordered JPG/JPEG/PNG
  pages. Add/remove/reorder photographed pages or replace the whole document.
  No application-level JCC byte-size cap is imposed; JCC pages never consume the
  Tower Photo allowance. Pages are not converted to PDF.
- Tower Photos form a separate ordered pool, maximum **5 photos / 5 MB combined**
  (5,000,000 original uploaded/stored bytes). Server limits are authoritative;
  browser checks mirror count/size. Images are never compressed to fit the limit.
- Actual content is fully parsed/decoded, not trusted from filename/MIME. PDFs
  with scripts/active actions/embedded files, encrypted/uninspectable PDFs,
  malformed images/documents and unsupported formats are rejected. Parsing uses
  [pypdf's strict reader](https://pypdf.readthedocs.io/en/stable/modules/PdfReader.html).
  Known harmless Adobe Scan `pageEntities.json` page metadata is removed during
  JCC PDF upload, then the sanitized PDF is strictly revalidated. Arbitrary
  embedded files and active PDF content remain prohibited; only sanitized output
  is stored. The removable UTF-8 JSON payload is limited to 4096 bytes per page,
  with only `type` and `isBackSide` scalar fields; this is not a JCC file-size cap.
  Multipart files are disk-backed from the first byte, validated through seekable
  streams and staged one at a time. Storage copies and private HTTP downloads use
  64 KiB chunks, not whole-file byte arrays. PDF dictionaries/references are
  inspected without indiscriminately decompressing arbitrary content streams.
  Pillow still fully decodes each image; parser memory depends on document/image
  complexity, and operators must provision temporary/protected disk and proxy
  upload settings. No deployment settings or JCC size cap are introduced.

Only the package creator Supervisor **still owning at least one package report**
may mutate evidence. DRAFT and REVIEWED Cells allow editing; **any SUBMITTED or
APPROVED Cell anywhere in the package locks all Supervisor attachment mutations**.
Attachments changing does not reset REVIEWED status. Evidence remains optional
for Review/Submit/Approval. Admins are read-only (historical branch scope for
Branch Admin; global for Superadmin). Other Supervisors need an existing owned
package report to view and can never modify its shared evidence.

Preliminary authorization is non-locking, and its read transaction ends before
multipart reception, expensive validation or protected-object staging. Only then
does publication lock the package and all Cell statuses, recheck ownership,
editability and current count/size/action semantics, and commit metadata/audit.
This serializes final publication/reorders against submission and concurrent
count/size changes without holding business locks during a slow upload. If a Cell
submits first, publication is rejected and definitely unreferenced staged objects
are removed. Private downloads authorize before opening a bounded streaming
response, with private/no-store, nosniff, sandbox and stored Content-Length headers.
Package audit records commit with metadata, recording actor/role/action, positions,
safe filenames/media/sizes, without signature/file bytes, secrets, absolute paths
or protected object keys. Existing report audits are not repurposed.

Binary objects use the extended protected-storage interface in a separate
`STORAGE_ROOT/attachments` namespace; existing signatures stay in `signatures`.
Server-generated immutable keys, private directories/files (0700/0600),
descriptor-relative no-symlink IO and authenticated authorization-before-read
routes prevent public/static access and traversal. Back up the **database and
entire persistent STORAGE_ROOT together** using existing deployment backup/restore
tools; do not put storage inside the Git application directory.

Replacement commits the new reference before cleaning the old object. Failures
before commit remove staged objects when safe; uncertain commit results retain
them rather than risk deleting valid content. Cleanup failures may leave private
orphans but do not invalidate the new reference. Reviewed orphan reconciliation
is deferred; no broad garbage collection is implemented. Migration downgrade
removes only Phase-6 metadata, **never protected files**; file reconciliation
requires operator review. Do not downgrade populated production data for testing.

Verification: `pytest` (optional Playwright/Chromium browser setup above),
`alembic upgrade head`, `alembic check`, Python compilation and changed-file Ruff.

## Phase 8B — official browser Print / Save as PDF (accepted)

The active owner-approved output is **Print → browser print dialog → physical
printer or Chrome/Edge Save as PDF**. Server PDF is retained but dormant with
`SERVER_PDF_ENABLED = False`: its route returns 404 before evidence/rendering work
and Download PDF links are hidden. Re-enabling requires separate owner approval.
The authoritative print document title suggests `<Operational Serial> <Place of
Installation> ECR.pdf` (without Cell No.); unsafe title characters are sanitized
for this suggestion only, never for stored or printed installation values.

Open **Print** from the full read-only report. The protected
`/ecr/reports/{id}/print` and `/pdf` routes use the existing own-Supervisor,
historical Branch Admin and global Superadmin visibility rules for every status.
One selected Cell's structured MySQL data feeds one presentation model, one
official Jinja template and one CSS source. No workflow state changes, screenshots,
reference-PDF backgrounds, public PDF files or persistent PDF records are used.
JCC/Tower Photos are **excluded**; a combined dossier remains deferred.

The official core is **US Letter portrait (612 × 792 pt)**, with `1 of 3`,
`2 of 3`, `3 of 3` numbering. Operational Serial + explicit Cell No. identify
every page. Draft/Reviewed output prominently says **NOT SUBMITTED**; Submitted
and Approved are distinctly labelled, without invented seals/approval signatures.
The existing authorized logo is embedded at modest size, not extracted artwork.

Continuation policy (presentation only, never a database/engineering limit):

- First 8 ordered blade serials and first 4 torque rows **per category** fit the
  core. Extra entries appear on labelled, independently numbered continuations.
- Narrative text is conservatively wrapped using installed DejaVu Sans metrics,
  with a 12% width reserve. Core Detailed Report allows 28 lines at 9 pt/14 pt
  leading, Customer Comment 6 lines. Wider/longer text uses **See Continuation**,
  and the **complete text** is printed on continuation sheets (44 lines/sheet at
  9 pt/12 pt leading). Paragraph breaks remain; tabs use four-column tab stops.
- Overlong scalar values are also moved intact to a named continuation rather
  than clipped or shrunk. Optional blanks, zero, False/NULL and Series N/A remain
  distinct. Unknown legacy Series does not fabricate N/A or expose hidden data.
- DE/NDE uses server-generated red ellipse/cross SVG and the unchanged B1 visual
  projection; readings print to two decimals and retain the stored shared unit.
  Each projected axis requires both paired readings: an incomplete pair is
  neutrally positioned with missing labels blank; explicit zero is a real reading.
  Per-row torque and OAL units are printed without conversion.

Migration `8b3f1a7c902d` follows `6a2c9e4f107b`, adding only nullable Supervisor
name (150) / Employee-ID (50) snapshots. Submission copies authoritative User
identity atomically with status/timestamp/audit. Approval/editing does not replace
it. Legacy SUBMITTED/APPROVED rows are backfilled from the **current** related
User, the best available value (not a reconstruction of past profile history).
Draft/Reviewed remain NULL. Missing historical snapshots fail official output
explicitly, without a live-name fallback. Disposable migration tests exercise
upgrade/backfill/downgrade/upgrade; never downgrade populated production to test.

Customer Sign stays private, is embedded once, and prints **Sign Date** in
Asia/Kolkata (`DD-MM-YYYY HH:MM IST`). No seal is rendered. Missing/corrupt
referenced evidence fails output rather than silently dropping a saved sign.

### Dormant server PDF runtime and later production provisioning review

WeasyPrint **70.0** is pinned in `pyproject.toml`/`uv.lock`. The same HTML/CSS is
rendered in a short-lived Python worker, not Chromium: one PDF at a time **per
application process**, 90-second wall timeout, 60-second CPU limit and 768 MiB
address-space limit. Busy/unavailable renderers return a clear 503. The worker
receives no production environment secrets; its resource fetcher accepts only
exact registered in-memory PNG assets, never arbitrary HTTP/file URLs. PDFs stay
in memory and use private/no-store, nosniff and safe download filenames.

Both **Ubuntu 24.04 (noble)** and **26.04 (resolute)** provide the required native
package names: `libpango-1.0-0`, `libpangoft2-1.0-0`, `libharfbuzz0b`,
`libharfbuzz-subset0`; install `fonts-dejavu-core` for the selected open font and
`fontconfig` for font discovery. Their dependencies provide GLib/FreeType/libffi.
`uv` installs Python wheels; system Python/pip is not the application runtime.
If wheels cannot be used, the upstream build prerequisites additionally include
`libffi-dev`, `libjpeg-dev`, `libopenjp2-7-dev`.
See [WeasyPrint's Ubuntu installation guide](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#ubuntu-20-04)
and the [Ubuntu 24.04](https://packages.ubuntu.com/noble/libpangoft2-1.0-0) /
[26.04](https://packages.ubuntu.com/resolute/amd64/libs/libpango-1.0-0) package indexes.

Local Pango 1.52.1 and DejaVu Sans were verified; the local HarfBuzz-Subset library
is absent, so WeasyPrint 70 uses its existing FontTools fallback (and warns).
Include HarfBuzz-Subset in later production provisioning. **AWS has not been
inspected or changed**; whether it needs one-time provisioning must be checked
before deployment. Deployment scripts are unchanged in Phase 8B.

Production font availability and existing production environment validation
remain pre-deployment prerequisites, even while server PDF is disabled.

Browser print: choose Letter portrait, 100% scale, disable browser-added
headers/footers. Screen controls/sidebar/application footer are not printed.
Automated tests cover shared geometry/print media, parsed Letter PDF pages,
continuations, snapshots, signature privacy, malicious plain text and scope.
Local sample PDFs are non-committed owner-review artifacts under `/tmp`.

## Future Enhancements — Planned / Not Yet Implemented

The current application remains focused on the accepted ECR module.

- **Budget Management:** service-job/site budgeting, preparation, approval and
  tracking, with actual-versus-budget comparison where appropriate. Detailed
  scope must be defined separately before implementation.
- **Erector / Supervisor Billing:** service-related bill preparation, review and
  tracking. Detailed workflow, commercial rules and authorization must be defined
  separately before implementation.

Neither module has business tables, routes, screens, workflows or calculations;
the existing Budget/Bills navigation entries remain coming-soon placeholders.
