# Erection & Commissioning Report (ECR) Digitalization

## 1. Project Overview

The Erection & Commissioning Report (ECR) application will digitize the existing Paharpur Cooling Towers Ltd. Erection & Commissioning Completion Report currently filled manually by site supervisors after erection and commissioning activities.

The application is intended for approximately 200 site supervisors and the Service/Admin team. The first release will be a mobile-first responsive web application running initially on a developer laptop/localhost. Production deployment will later use the company-provided AWS infrastructure and domain/subdomain. A native Android application may be added later while continuing to use the same backend API and database.

The system must preserve the engineering intent and final printable appearance of the existing hardcopy report while making data entry, storage, retrieval, search, review, approval, audit, and attachment handling digital.

### Main objectives

- Replace handwritten E&C reports with structured digital records.
- Make mobile data entry practical for site supervisors.
- Keep Cooling Tower Serial No. as the primary operational reference.
- Support multiple towers under one Cooling Tower Serial No. using suffixes A, B, C, D, etc.
- Support multiple cells under each tower with no hard upper cell limit.
- Store one technical E&C report per cell.
- Search by Cooling Tower Serial No. and key equipment serial numbers.
- Generate a browser print view/PDF closely matching the present hardcopy.
- Support one logical JCC document for the overall E&C package.
- Support controlled Tower Photo uploads.
- Provide secure persistent login per browser/device.
- Maintain review, submission, approval, and auditability.
- Keep the backend reusable for a future Android app.
- Keep local development simple while making production deployment compatible with company AWS infrastructure.

---

## 2. Core Business Hierarchy

```text
Cooling Tower Serial No.
        |
        +-- Optional Tower Suffix (A, B, C, D, ...)
                |
                +-- Cell-1
                +-- Cell-2
                +-- Cell-3
                +-- ...
                        |
                        +-- One E&C report per cell
```

### 2.1 Cooling Tower Serial No.

Example:

```text
26-2-0001
```

The term `Base Paharpur Serial` should not be used in the application UI. Use `Cooling Tower Serial No.`.

### 2.2 Tower suffix rules

If there is only one tower, no suffix is required.

If there is more than one tower under the same Cooling Tower Serial No., the tower identifiers start with suffix A:

```text
26-2-0001 A
26-2-0001 B
26-2-0001 C
26-2-0001 D
...
```

There is no separate unsuffixed `26-2-0001` tower in a multi-tower case. The application must not impose an A-D upper limit.

### 2.3 Cell rules

Each tower may contain multiple cells. Typical range is 1-10, but there must be no hard software upper limit. Cell number must be a positive integer.

Example:

```text
26-2-0001 A
 |- Cell-1
 |- Cell-2
 |- Cell-3
 `- Cell-4

26-2-0001 B
 |- Cell-1
 |- Cell-2
 `- Cell-3
```

Each individual cell has its own technical Erection & Commissioning Report.

### 2.4 Overall E&C package/group

The system should have a parent E&C package/group representing the overall Cooling Tower Serial No. record. Cell reports link to this package. Shared assets such as the JCC document belong to the package rather than being duplicated under each cell.

---

## 3. User Roles and Authentication

### Supervisor

A supervisor can:

- Sign up.
- Log in.
- Use Forgot Password / Reset Password.
- Remain logged in on the same browser/device through a secure persistent session.
- Create a new E&C report without job assignment.
- Save/edit Draft reports.
- Review and confirm before submission.
- Upload permitted attachments while Draft.
- View previous reports created by him.
- View submitted/approved reports in read-only mode.

### Admin

Admin can:

- View users and account details.
- Activate/deactivate users.
- Reset passwords.
- Force logout/revoke sessions.
- View all reports.
- Search using supported filters.
- Review submitted reports.
- Approve reports.
- View/download attachments.
- Generate final print/PDF output.
- Inspect audit history.

### Password security

Passwords must never be stored in plaintext and must never be retrievable by Admin. Use a strong password hash such as Argon2id. Admin gets password-reset capability, not password viewing.

### Persistent login

The requirement for `single time login per device/browser` means a secure persistent session, not a permanently valid token. Use HttpOnly/Secure/SameSite cookies or equivalent secure server-managed sessions, session rotation/revocation, manual logout, and admin force logout.

---

## 4. Supervisor Application Flow

There is no assigned-job workflow in the initial version.

Supervisor home:

```text
ERECTION & COMMISSIONING

[ + New Erection & Commissioning Report ]

My Previous Reports
```

Clicking `New Erection & Commissioning Report` starts a new Draft directly.

`My Previous Reports` should show at least:

- Cooling Tower Serial No.
- Tower suffix, if any.
- Cell No.
- Customer.
- Completion/submission date.
- Current status.
- View action.

Submitted/approved reports are read-only for the supervisor.

---

## 5. Report Header / Identification Fields

| Field | Mandatory | Input/Source | Notes |
|---|---:|---|---|
| Customer | Yes | Text/searchable input | Customer name |
| Cooling Tower Serial No. | Yes | Text | Primary reference |
| Multiple Towers? | Yes | Yes/No | Controls suffix field |
| Tower Suffix | Conditional | Select/text | Required only for multi-tower case |
| Cell No. | Yes | Positive integer | No hard upper limit |
| Cooling Tower Series | Yes | Text/select | Existing report field |
| Model | Yes | Text/select | Existing report field |
| Place of Installation | Yes | Text | Site/location |
| Erection Start Date | No | Date | Optional |
| Erection Completion Date | Yes | Date | Required |
| Supervisor / Erected By | Yes | Auto | Authenticated user |
| Submission Date | Auto | System | Set on final submission |

Derived display example:

```text
26-2-0001 B / Cell-3
```

Do not store duplicated formatted identity strings as the primary source of truth; derive them from structured values.

---

## 6. Technical Data Sections

The digital form must preserve all engineering information represented in the existing three-page E&C report.

Recommended mobile sections:

1. Report Identification
2. Motor
3. Fan
4. Fan Blades
5. Fan Cylinder
6. Drive Shaft
7. Gearbox
8. Fill
9. Eliminator
10. FC Valves
11. Nozzles
12. Bearing Housing
13. Belt & Pulleys
14. Lubricant / Oil Information
15. General / Tower Hardware
16. Fastener Torque Details
17. Mechanical Equipment Alignment
18. DE/NDE Alignment
19. Other Optionals
20. Detailed Report by Erection Team Leader
21. Customer Comments
22. Attachments
23. Signatures / Confirmation

The data-entry UI should be mobile-first and use collapsible/touch-friendly sections rather than reproducing the paper page layout on a phone.

### Searchable equipment serial numbers

Store these as dedicated searchable/indexed fields:

- Motor Serial No.
- Fan Serial No.
- Drive Shaft Serial No.
- Gearbox Serial No.

Do not bury them only inside free text or opaque JSON.

---

## 7. DE / NDE Interactive Alignment Control

Two interactive controls are required: `DE` and `NDE`, with the label at the center of the red ellipse/cross concept from the existing report.

Allowed values:

```text
-1.0 to +1.0
```

Increment/decrement:

```text
0.1
```

Values must be stored numerically. Do not add engineering pass/fail warnings or messages such as `Value exceeds recommended limit` unless explicitly added in a future requirement.

---

## 8. Uploads and Attachments

There are two different attachment categories with different scope and rules.

### 8.1 JCC Letter / JCC Document

The JCC is one logical document for the overall E&C package, not one JCC per cell.

Rules:

- One active logical JCC document per E&C package.
- It may contain one page or multiple pages.
- Accept PDF, including multi-page PDF.
- Accept approved image/photo formats.
- If a paper JCC is photographed page-by-page, the ordered images must be grouped and presented as one logical JCC document.
- JCC page images must not count as Tower Photos.
- JCC can be replaced while the package/report is editable/Draft.
- After submission, supervisor replacement/edit is locked.
- Admin can view/download the JCC from the overall E&C package context.
- No JCC size cap has currently been specified; do not invent one.

### 8.2 Tower Photos

Rules:

- Multiple photos allowed.
- Maximum 5 Tower Photos.
- Maximum combined total size: 5 MB.
- The 5 MB limit is aggregate across all Tower Photos, not 5 MB per photo.
- Enforce count and size in both frontend and backend.
- Allow add/remove/replace while Draft.
- Lock supervisor modification after submission.

### 8.3 Upload security

Backend validation must include:

- MIME type.
- Extension/content consistency where practical.
- File count.
- Aggregate Tower Photo size.
- Safe generated storage keys/names.
- Authorization.
- Prevention of path traversal/executable uploads.
- Correct JCC document/page grouping and ordering.

Original user filenames may be preserved as metadata but must not be used directly as filesystem paths.

---

## 9. Report Workflow

Required workflow:

```text
DRAFT
  |
  v
REVIEWED
  |
  v
CONFIRM
  |
  v
SUBMITTED
  |
  v
APPROVED
```

`CONFIRM` should normally be treated as an explicit user action rather than necessarily a persisted database state.

Practical persisted statuses may be:

```text
DRAFT
REVIEWED
SUBMITTED
APPROVED
```

Rules:

- Draft: editable by creator; autosave enabled.
- Reviewed: preview/check stage.
- Confirm: explicit user confirmation immediately before submission.
- Submitted: supervisor can view but cannot edit; attachments locked for supervisor.
- Approved: final admin-approved state; read-only in normal supervisor flow.

Do not silently introduce a `Returned for Correction` workflow unless separately approved.

---

## 10. Autosave and Draft Recovery

Autosave is required for mobile/site use.

Suggested behaviour:

- Save after a short debounce interval.
- Save on section navigation.
- Save before review.
- Display `Saving...`, `Saved`, and visible retry/error states.
- Prevent duplicate Draft creation due to refresh/double-click.
- Restore Drafts after browser reopen when selected.

Offline-first browser synchronization is not required for the initial web release, but the architecture should not prevent future Android offline support.

---

## 11. Admin Search and Retrieval

Search fields:

```text
Cooling Tower Serial No.
Fan Serial No.
Drive Shaft Serial No.
Gearbox Serial No.
Motor Serial No.
Customer
Model
Date From
Date To
```

### Cooling Tower search behaviour

Searching `26-2-0001` does not require a suffix.

- If one matching report exists, open directly where appropriate.
- If multiple towers exist, show `26-2-0001 A`, `B`, `C`, etc.
- Selecting a tower shows available cells.
- A single unsuffixed tower with multiple cells should show cells directly.
- Selecting a cell opens the complete E&C report.
- Overall package view should expose shared JCC and Tower Photos.

### Equipment serial search

Searching Motor/Fan/Drive Shaft/Gearbox Serial No. should identify the associated:

- Cooling Tower Serial No.
- Tower suffix, if applicable.
- Cell No.
- Customer.
- Model.
- completion/submission information.
- full report link/view.

---

## 12. Final Report / Hardcopy Rendering

The structured database is the source of truth. The application must not be implemented as one large digital image/blob of the three-page form.

```text
Mobile Data Entry
      |
      v
Structured MySQL Data
      |
      v
Report Renderer
      |
      +-- Browser Print View
      `-- PDF Output
```

The final output should closely reproduce the current official E&C hardcopy. JCC and Tower Photos remain linked supporting records and may later be offered as a combined download package without changing the official three-page report layout.

---

## 13. Approved Technical Architecture

### 13.1 Backend

Recommended stack:

- Python 3.12+
- FastAPI
- SQLAlchemy 2.x
- Alembic
- Pydantic / Pydantic Settings
- MySQL-compatible driver (final driver chosen during implementation; e.g. PyMySQL or mysqlclient)
- Authentication/session module
- Storage service abstraction
- Report/PDF renderer

### 13.2 Database: MySQL

**Production target database is MySQL**, because company applications, including ERP and other internal systems, use company-managed MySQL on AWS.

Requirements:

- Use SQLAlchemy 2.x and Alembic.
- Target MySQL 8.x unless IT confirms another supported version.
- Avoid PostgreSQL-specific SQL, data types, extensions, or JSON operators.
- Keep SQLAlchemy models and migrations MySQL-safe.
- Use appropriate MySQL character set/collation, preferably `utf8mb4`.
- Use proper indexes and foreign keys.
- Production database credentials/host/port will be supplied by IT and must be environment variables.
- Prefer a dedicated ECR database/schema and least-privilege ECR DB user if IT permits.

### 13.3 Frontend

Initial frontend should be simple and maintainable:

- Server-rendered Jinja templates.
- HTMX where useful.
- Lightweight JavaScript.
- Responsive CSS.

A heavier SPA framework should only be introduced if a real project requirement justifies it.

### 13.4 Architecture style

Use a modular monolith. Do not introduce microservices for the initial system.

Suggested logical layers:

```text
Web/UI
  -> FastAPI routes/controllers
      -> schemas/validation
          -> services/business rules
              -> SQLAlchemy repositories/models
                  -> MySQL

File operations
  -> storage service interface
      -> local filesystem in development
      -> AWS/object storage later if required
```

### 13.5 Dependency management

`uv` may be used for fast dependency/environment management with `pyproject.toml` and `uv.lock`, but it is not a runtime requirement. The application must remain a normal Python application that can be installed through standard Python tooling if necessary.

---

## 14. Development and Production Environments

### 14.1 Local development

Development will first be performed on the user's laptop/localhost.

Requirements:

- Use `.env` locally.
- Commit `.env.example`, never real secrets.
- Do not hard-code Windows paths, usernames, local IPs, credentials, or machine-specific configuration.
- Local MySQL may be installed directly or run in a development container; the choice should not affect production business logic.
- The app must provide clear local startup instructions.

### 14.2 Production environment

Primary production target is the **company AWS environment**, because IT has confirmed that other company applications such as ERP are already hosted there and that Python applications can be hosted.

IT is expected to provide or confirm:

- Domain/subdomain.
- AWS/Linux hosting environment.
- MySQL host/database/user/credentials.
- Network access from app server to MySQL.
- HTTPS/reverse-proxy arrangement.
- File-storage/backup policy.
- Server or platform deployment access.

The application should remain portable enough to run on a standard Ubuntu/Linux server if required, but AWS/company infrastructure is the primary production target.

### 14.3 Reverse proxy / HTTPS

In production, FastAPI should not be directly exposed as the public TLS endpoint. Use the company-approved reverse proxy/load balancer/Nginx arrangement. External traffic normally uses ports 80/443, while FastAPI runs on an internal application port such as 8000.

---

## 15. File Storage Strategy

Development may use local filesystem-backed storage, but file access must be behind a storage-service abstraction.

Do not scatter direct filesystem operations throughout routes.

Store metadata in MySQL, for example:

```text
id
scope_type
scope_id
attachment_type
original_filename
storage_key
mime_type
size_bytes
uploaded_by
created_at
page_order (where applicable)
```

Production storage may be:

- company-approved local/network storage, or
- AWS S3/object storage if IT prefers.

The storage backend should be replaceable without rewriting ECR business logic.

---

## 16. Proposed Data Model

Indicative core tables:

```text
users
user_sessions
ecr_packages
reports
report_motor
report_fan
report_fan_blades
report_fan_cylinder
report_drive_shaft
report_gearbox
report_fill
report_eliminator
report_fc_valves
report_nozzles
report_bearing_housing
report_belt_pulleys
report_lubrication
report_fastener_torque
report_alignment
report_optionals
report_comments
report_signatures
attachments
jcc_documents
jcc_pages
audit_log
```

### E&C package

Indicative fields:

```text
id
cooling_tower_serial_no
customer
cooling_tower_series
model
place_of_installation
created_by
created_at
updated_at
```

### Cell report

Indicative fields:

```text
id
ecr_package_id
tower_suffix nullable
cell_no
erection_start_date nullable
erection_completion_date
supervisor_user_id
status
created_at
updated_at
submitted_at nullable
approved_at nullable
approved_by nullable
```

### JCC / attachments

Business rules:

- One active logical JCC document per E&C package.
- Multi-page PDF remains one logical document.
- Photo-based JCC pages are ordered and grouped.
- Tower Photos maximum 5.
- Tower Photo aggregate maximum 5 MB.

The exact normalized design should be finalized during requirements/form mapping before coding technical tables.

---

## 17. Search Index Strategy

Create appropriate MySQL indexes for:

- `cooling_tower_serial_no`
- customer
- model
- erection completion date
- submitted date
- Motor Serial No.
- Fan Serial No.
- Drive Shaft Serial No.
- Gearbox Serial No.

Serial matching should trim obvious whitespace and use documented case-insensitive behaviour without damaging original display values.

---

## 18. Audit and Data Integrity

Audit important actions:

- Signup.
- Login/logout/session revocation.
- Report creation and important edits.
- JCC upload/replacement/page changes.
- Tower Photo add/remove/replace.
- Review/confirm/submit.
- Approval.
- Admin changes.

Submitted/approved records must not be silently modified.

---

## 19. API Architecture Preview

Indicative routes only:

```text
POST /api/auth/signup
POST /api/auth/login
POST /api/auth/logout
POST /api/auth/forgot-password
POST /api/auth/reset-password

POST  /api/reports
GET   /api/reports/mine
GET   /api/reports/{report_id}
PATCH /api/reports/{report_id}
POST  /api/reports/{report_id}/review
POST  /api/reports/{report_id}/submit

POST   /api/ecr-packages/{package_id}/jcc
GET    /api/ecr-packages/{package_id}/jcc
DELETE /api/ecr-packages/{package_id}/jcc

POST   /api/ecr-packages/{package_id}/tower-photos
DELETE /api/ecr-packages/{package_id}/tower-photos/{attachment_id}

GET  /api/admin/reports/search
POST /api/admin/reports/{report_id}/approve
GET  /api/admin/users

GET /api/reports/{report_id}/print
GET /api/reports/{report_id}/pdf
```

Final route naming may change during implementation.

---

## 20. Implementation Phases

The project will use 10 controlled phases, including Phase 0. Each phase follows: implement -> verify -> user manual acceptance -> commit/push -> next phase.

### Phase 0 - Requirements & Architecture Freeze

- Read and reconcile project docs.
- Complete field-by-field form mapping.
- Confirm MySQL/AWS assumptions with known IT information.
- Confirm signature and password-recovery details.
- Confirm JCC/Tower Photo rules.
- No application feature coding.

### Phase 1 - Project Foundation + MySQL Infrastructure

- FastAPI project structure.
- `pyproject.toml`; optionally `uv.lock`.
- Settings/environment configuration.
- MySQL connection.
- SQLAlchemy 2.x.
- Alembic.
- Health endpoint.
- Baseline tests.
- Local startup documentation.

### Phase 2 - Authentication + User/Admin Foundation

- Signup/login/logout.
- Password hashing.
- Forgot/reset password foundation.
- Persistent browser/device sessions.
- Roles.
- Admin user management.

### Phase 3 - Supervisor Dashboard + ECR Identity + Draft/Autosave

- New Report / Previous Reports.
- E&C package and report identity.
- Single/multi-tower rules.
- Cell rules.
- Draft lifecycle.
- Autosave and recovery.

### Phase 4 - Complete Technical Form

- All main page 1/page 2 engineering fields.
- Repeating rows where required.
- Dedicated searchable equipment serials.

### Phase 5 - DE/NDE + Page 3 + Attachments

- Interactive DE/NDE.
- Detailed report/comments/signature fields.
- JCC logical document.
- Tower Photos.
- File validation/security.

### Phase 6 - Review / Confirm / Submit + Admin Approval + Audit

- Review screen.
- Mandatory-field validation.
- Confirm/submit locking.
- Admin approval.
- Audit trail.

### Phase 7 - Admin Search + Full Report Retrieval

- Cooling Tower hierarchy search.
- Equipment serial search.
- Customer/model/date filters.
- Full report/package views.

### Phase 8 - Official Print/PDF + Full Integration Testing

- Hardcopy-style print/PDF.
- Full lifecycle tests.
- Mobile, upload, search, permission, and migration tests.

### Phase 9 - Local Release Candidate + AWS Cloud Readiness

- README/setup.
- Fresh-clone local install test.
- Production settings.
- Backup/restore guidance.
- AWS/Linux deployment packaging.
- Optional one-command Ubuntu/Linux installer similar to OpenAlgo's `install.sh` flow if compatible with IT's AWS server access model.
- Reverse-proxy/HTTPS integration guidance.
- Do not perform actual production deployment until IT environment details and approval are available.

---

## 21. Deployment Automation Goal

For a standard Ubuntu/Linux server where sudo/SSH access is provided, the project should eventually support an installation flow similar to:

```bash
ssh user@your_server_ip
mkdir -p ~/ecr-install
cd ~/ecr-install
wget https://raw.githubusercontent.com/Namir-AI/ECR/main/install/install.sh
chmod +x install.sh
sudo ./install.sh
```

The installer may automate:

- prerequisite packages.
- Python/uv setup if chosen.
- app checkout/install.
- `.env` generation from interactive prompts.
- service configuration.
- Alembic migrations.
- reverse proxy/health checks where permitted.

However, because the company production environment is AWS-managed and uses company MySQL, the installer must not assume it owns the database server, DNS, TLS termination, or AWS networking. Those may be managed by IT. The production installer must therefore support externally supplied MySQL connection details and company-provided domain/reverse-proxy configuration.

---

## 22. Non-Goals for Initial Release

Unless later requested:

- Native Android app.
- AI engineering acceptance/rejection.
- Automatic alignment warnings.
- Full maintenance history beyond E&C.
- Job assignment workflow.
- Complex ERP integration.
- Offline-first browser synchronization.

---

## 23. Key Architectural Principle

The project is not a digital image of three paper pages. The source of truth is structured engineering data:

```text
E&C Package / Cooling Tower Serial No.
   -> Tower suffix when required
      -> Cell
         -> E&C Report
            -> Equipment / readings / comments / approval
   -> Shared JCC / supporting package assets
```

The official three-page E&C form is an output representation of this structured data. This architecture supports current reporting needs while creating a foundation for future component history, Android access, analytics, and integration.