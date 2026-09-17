# Erection & Commissioning Report (ECR) Digitalization

## 1. Project Overview

### 1.1 Purpose

The Erection & Commissioning Report (ECR) application will digitize the existing Paharpur Cooling Towers Ltd. Erection & Commissioning Completion Report currently filled manually by site supervisors after erection and commissioning activities.

The application is intended for approximately 200 site supervisors and the Service/Admin team. The first release will be a mobile-first responsive web application. A native Android application may be added later while continuing to use the same backend API and database.

The system must preserve the engineering intent and final printable appearance of the existing hardcopy report while making data entry, storage, retrieval, search, review, approval, and attachment handling fully digital.

### 1.2 Main Objectives

- Replace handwritten Erection & Commissioning Completion Reports with structured digital records.
- Make mobile data entry practical for site supervisors.
- Store each cell's E&C data as an independent report.
- Retain Cooling Tower Serial No. as the primary operational reference.
- Support multiple towers under one Cooling Tower Serial No. using suffixes A, B, C, D, etc.
- Support multiple cells under each tower.
- Allow fast retrieval by Cooling Tower Serial No. or equipment serial numbers.
- Generate a printable/PDF report closely matching the present hardcopy format.
- Support one logical JCC Letter/document for the overall E&C record/package, independent of the individual cell reports.
- Support tower-photo evidence with controlled count and total size.
- Maintain report workflow and auditability.
- Provide a reusable backend for a later Android app.

---

## 2. Core Business Hierarchy

The central hierarchy of the application is:

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
                        +-- One Erection & Commissioning Report per cell
```

### 2.1 Cooling Tower Serial No.

Example:

```text
26-2-0001
```

This is the primary Cooling Tower Serial No. and replaces the earlier working term `Base Paharpur Serial`.

### 2.2 Tower Suffix Rules

If there is only one tower, no suffix is required.

If there is more than one tower under the same Cooling Tower Serial No., tower identification starts with suffix A:

```text
26-2-0001 A
26-2-0001 B
26-2-0001 C
26-2-0001 D
...
```

There is no separate unsuffixed tower in a multi-tower case.

The application must not impose a fixed A-D limit. Additional suffixes must remain possible.

### 2.3 Cell Rules

Each tower may contain multiple cells.

Typical range: 1-10 cells.

No hard upper software limit should be imposed.

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

Each individual cell has its own Erection & Commissioning Report.

### 2.4 Shared E&C Package Assets

Not every uploaded document belongs to one cell.

The **JCC Letter is one logical document for the overall E&C record/package**, not one JCC Letter per cell. It may contain one page or several pages. The data-entry records remain cell-wise, but the JCC document is shared and must not be duplicated for every cell.

Implementation should therefore provide a parent/group scope for assets that belong to the overall Cooling Tower Serial No. E&C package, while cell-specific data remains under the individual cell report.

---

## 3. User Roles

### 3.1 Supervisor

A supervisor can:

- Sign up.
- Log in.
- Use Forgot Password.
- Stay logged in on the same browser/device after successful login.
- Create a new Erection & Commissioning Report without waiting for job assignment.
- Save a report as Draft.
- Review and confirm a report before submission.
- Upload permitted attachments.
- View his own previous submitted/completed reports.
- View completed/submitted reports in read-only mode.

### 3.2 Admin

Admin can:

- View users and account details.
- Activate/deactivate users.
- Reset passwords or force re-login.
- View all reports.
- Search reports using the supported search fields.
- Review submitted reports.
- Approve reports.
- View/download attachments.
- Generate the final report/PDF.
- Inspect audit history.

### 3.3 Password Security Requirement

User passwords must never be stored in plain text and therefore must not be readable by Admin.

Passwords should be stored only as secure hashes, preferably Argon2id (bcrypt acceptable if needed). Admin should have password reset capability rather than password viewing capability.

---

## 4. Authentication Experience

### 4.1 Sign Up

Suggested fields:

- Full Name *
- Employee ID *
- Mobile Number *
- Email (optional or required depending on final password recovery method)
- Password *
- Confirm Password *

### 4.2 Login

Suggested login identifiers:

- Employee ID or Mobile Number
- Password

### 4.3 Forgot Password

A simple password recovery flow must be available.

Initial implementation options:

1. Email OTP/link, or
2. Mobile OTP if an SMS service is integrated, or
3. Admin-assisted reset for the first internal deployment.

The exact recovery method can be finalized during implementation.

### 4.4 One-Time Login Per Device/Browser

After successful login, the supervisor should remain logged in on that browser/device and should not need to enter credentials on every visit.

Recommended implementation:

- Secure HttpOnly refresh/session cookie.
- Long-lived refresh token.
- Short-lived access token/session.
- Token rotation.
- Device/browser session record in database.
- Manual Logout option.
- Admin Force Logout option.
- Automatic logout after password reset or security event.

The phrase `single time login` means persistent login, not a permanently valid insecure session.

---

## 5. Supervisor Application Flow

The supervisor should not receive assigned jobs in the first version.

### 5.1 Supervisor Home

```text
ERECTION & COMMISSIONING

[ + New Erection & Commissioning Report ]

My Previous Reports
```

### 5.2 New Report

Clicking `New Erection & Commissioning Report` opens the data-entry form directly.

There is no `My Assigned Jobs` dependency.

### 5.3 Previous Reports

The supervisor can view previous reports created by him.

Suggested list information:

- Cooling Tower Serial No.
- Tower suffix, if any.
- Cell No.
- Customer.
- Completion/submission date.
- Current status.
- View button.

Submitted/completed reports are read-only for the supervisor.

---

## 6. Report Header / Identification Fields

### 6.1 Main Fields

| Field | Mandatory | Input/Source | Notes |
|---|---:|---|---|
| Customer | Yes | Text / searchable input | Customer name |
| Cooling Tower Serial No. | Yes | Text | Primary operational reference |
| Multiple Towers? | Yes | Yes/No | Controls tower suffix field |
| Tower Suffix | Conditional | Select/text | Required only for multi-tower case |
| Cell No. | Yes | Numeric selector | One report per cell |
| Cooling Tower Series | Yes | Text/select | As per existing report |
| Model | Yes | Text/select | Cooling tower model |
| Place of Installation | Yes | Text | Site/location |
| Erection Start Date | No | Date picker | Explicitly optional |
| Erection Completion Date | Yes | Date picker | Required |
| Supervisor / Erected By | Yes | Auto-filled | Derived from logged-in user |
| Submission Date | Auto | System | Generated at submission |

### 6.2 Tower Entry Behaviour

If `Multiple Towers = No`, the suffix field is hidden and the report belongs directly to the Cooling Tower Serial No.

If `Multiple Towers = Yes`, suffix becomes mandatory.

Example:

```text
Cooling Tower Serial No.: 26-2-0001
Tower Suffix: B
Display: 26-2-0001 B
Cell: Cell-3
```

### 6.3 Cell Entry

Cell number should preferably use a numeric stepper/select control.

Example:

```text
[-] 3 [+]
```

Display value:

```text
Cell-3
```

No hard maximum should be coded.

---

## 7. Technical Data Sections

The digital form must preserve all technical information represented in the existing three-page E&C report.

Recommended mobile sections:

1. Report Identification
2. Motor
3. Fan
4. Fan Cylinder
5. Drive Shaft
6. Gearbox
7. Fill
8. Eliminator
9. FC Valves
10. Nozzles
11. Bearing Housing
12. Belt & Pulleys
13. Lubricant / Oil Information
14. General / Tower Hardware
15. Fastener Torque Details
16. Mechanical Equipment Alignment
17. DE/NDE Alignment Graphic
18. Other Optionals
19. Detailed Report by Erection Team Leader
20. Customer Comments
21. Attachments
22. Signatures / Confirmation

The interface should use collapsible cards/sections on mobile rather than reproducing the paper layout during data entry.

---

## 8. Important Equipment Serial Numbers

The following serial numbers must be stored in dedicated searchable fields:

- Motor Serial No.
- Fan Serial No.
- Drive Shaft Serial No.
- Gearbox Serial No.

Other equipment serials may also be stored where present, but these four are mandatory search dimensions in the system architecture.

---

## 9. DE / NDE Interactive Alignment Control

The red circle/cross graphic from the existing report should be converted into an interactive digital control.

### 9.1 Display

Two controls/graphics:

- DE
- NDE

`DE` or `NDE` is shown at the center of the corresponding cross/ellipse.

### 9.2 Numeric Range

Each applicable edge/axis should support values from:

```text
-1.0 to +1.0
```

Increment/decrement:

```text
0.1
```

Values must be stored as numeric data, not only as an image.

### 9.3 No Automatic Limit Warning

The application should record values only. Do not display automatic engineering acceptance/limit warnings unless deliberately added later.

---

## 10. Uploads and Attachments

There are two different upload types with different scope and rules.

### 10.1 JCC Letter / JCC Document

Purpose: upload the JCC Letter/document associated with the overall Erection & Commissioning record/package.

**Important scope rule:**

- The JCC is **not one upload per cell**.
- There is **one logical JCC document for the overall E&C package / Cooling Tower Serial No. record**.
- That single JCC document may contain **one page or multiple pages**.
- A multi-page JCC must still be treated by the application as one logical document.

Accepted source forms:

- PDF, including a multi-page PDF.
- Photo/image.
- If a paper JCC has several pages and is captured as separate photographs, those page images should be grouped and presented as **one JCC document**, not as Tower Photos and not as separate JCC records.

Suggested supported image formats:

- JPG/JPEG
- PNG
- WEBP

Lifecycle rules:

- One active logical JCC document per overall E&C package.
- It may be replaced while the related E&C package is still editable/Draft.
- Replacement should supersede the previous active JCC while preserving audit history where implemented.
- After final submission, supervisor replacement/edit is not allowed.
- Admin can view/download the JCC from the overall report context.

Suggested UI:

```text
JCC Letter
[ Upload PDF / Photo(s) ]

Uploaded JCC: 3 pages
[ View ] [ Replace ]    # Replace only while editable
```

For a PDF, page count may be displayed if available. For photo-based JCC pages, the UI should preserve page order.

### 10.2 Tower Photos

Purpose: upload site/tower photographs related to the E&C record.

Rules:

- Multiple uploads allowed.
- Maximum 5 photos.
- Total combined Tower Photos size cap: 5 MB.
- Photo formats only unless later expanded.
- The 5 MB cap applies to the **combined total**, not 5 MB per photo.
- Individual and aggregate validation must be enforced in frontend and backend.

Suggested UI:

```text
Tower Photos
[ + Add Photos ]

Photo 1
Photo 2
Photo 3

3 of 5 photos
Total: 3.7 MB / 5 MB
```

### 10.3 Upload Security

Backend must validate:

- MIME type.
- File extension.
- File size.
- Aggregate Tower Photo size.
- Maximum Tower Photo count.
- Safe generated storage filename.
- Access authorization.
- Correct JCC page/document grouping.

Original user filenames may be kept as metadata but should not be used directly as server filesystem paths.

### 10.4 Storage Strategy

Phase 1 may use filesystem-backed storage if deployed on a single server, but the application should expose attachment storage through a storage service abstraction so that later migration to S3-compatible object storage is straightforward.

Recommended production path:

- Metadata in PostgreSQL.
- Binary files in object storage.
- Database stores file/document ID, scope/group ID, attachment type, original filename, generated key/path, MIME type, byte size, uploader, page/order metadata where required, and timestamps.

---

## 11. Report Status and Workflow

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

`CONFIRM` should normally be treated as a user action/confirmation step rather than necessarily a persisted status.

Practical database statuses:

```text
DRAFT
REVIEWED
SUBMITTED
APPROVED
```

### DRAFT

- Editable by creator.
- Autosave enabled.
- Attachments can be added/replaced within validation rules.

### REVIEWED

- Supervisor reviews all entered information before final submission.
- System shows a report preview/check screen.

### CONFIRM

- Supervisor actively confirms that entered data is correct.
- Confirmation leads to submission.

### SUBMITTED

- Supervisor can view but cannot edit.
- JCC and Tower Photos are locked for supervisor editing/replacement.
- Admin can review.

### APPROVED

- Final approved report.
- Read-only for supervisor.
- Admin-controlled final state.

---

## 12. Autosave

Autosave is required for mobile/site use.

Suggested behaviour:

- Save changed fields after a short debounce interval.
- Save on section change.
- Save before moving to review.
- Display simple state such as `Saved`, `Saving...`, or `Unable to sync - retrying`.

---

## 13. Admin Search and Retrieval

### 13.1 Search Fields

```text
SEARCH
------------------------------------------------
Cooling Tower Serial No. [ 26-2-0001 ]
Fan Serial No.           [             ]
Drive Shaft Serial No.   [             ]
Gearbox Serial No.       [             ]
Motor Serial No.         [             ]
Customer                 [             ]
Model                    [             ]
Date From                [             ]
Date To                  [             ]

                        [ Search ]
```

### 13.2 Cooling Tower Serial No. Search

Admin should search using the Cooling Tower Serial No. without needing the suffix.

If one matching cell report exists, the full report may open directly. If multiple towers exist, show available suffixes/towers. Selecting a tower shows available cells. If a single unsuffixed tower has multiple cells, show the cell selector directly.

The overall result view should also expose the single shared JCC document and Tower Photos associated with the E&C package.

### 13.3 Equipment Serial Search

Searching by Motor, Fan, Drive Shaft, or Gearbox Serial No. must locate the associated report and display context:

- Cooling Tower Serial No.
- Tower suffix, if applicable.
- Cell No.
- Customer.
- Model.
- Completion/submission details.
- Link/button to complete E&C report.

---

## 14. Final Report / Hardcopy Rendering

The supervisor data-entry interface should be mobile optimized and does not need to visually imitate the paper form.

The report output/print view should closely reproduce the existing Erection & Commissioning Completion Report layout.

```text
Mobile Data Entry
      |
      v
Structured Database
      |
      v
Report Renderer
      |
      +-- Browser Print View
      `-- PDF Output
```

The PDF should include the technical data, alignment graphics/readings, detailed report, customer comments, and signature information as applicable.

The JCC document and Tower Photos remain linked supporting records. A future combined-download package may include the ECR PDF + JCC + Tower Photos without altering the original three-page ECR format.

---

## 15. Recommended Technical Architecture

### 15.1 Backend

Recommended: **FastAPI**

Suggested backend components:

- FastAPI
- Python 3.12+
- SQLAlchemy 2.x
- Alembic migrations
- Pydantic
- PostgreSQL driver
- Authentication/session module
- File storage service
- PDF/report renderer

### 15.2 Database

Recommended: **PostgreSQL**

### 15.3 Frontend

Phase 1 should be a mobile-first responsive web frontend.

Recommended options:

- Server-rendered/Jinja + HTMX + lightweight JavaScript for fastest implementation, or
- React/Vue if a richer SPA architecture is desired from the beginning.

Core requirements:

- Mobile-first.
- Touch friendly.
- Large input controls.
- Collapsible sections.
- Autosave feedback.
- File upload progress/validation.
- Report preview.

### 15.4 Future Android App

The Android app should consume the same FastAPI endpoints. Kotlin + Jetpack Compose is a suitable future option.

---

## 16. Proposed Database Architecture

The database should model real business relationships rather than treating the report as one large JSON/text document.

### 16.1 Core Tables

```text
users
user_sessions
ecr_packages / report_groups
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
jcc_documents / jcc_pages
audit_log
```

The exact normalized structure should be finalized after a field-by-field mapping of the current form.

### 16.2 E&C Package / Group

Because the JCC is shared rather than cell-specific, the implementation should have a parent grouping concept for the overall E&C record.

Indicative fields:

```text
id
cooling_tower_serial_no
customer
cooling_tower_series
model
place_of_installation
created_by
status / package status as finalized
created_at
updated_at
```

Individual cell reports link to this parent group.

### 16.3 Cell Report Identity

Suggested report-level fields:

```text
id
ecr_package_id
tower_suffix          nullable
cell_no
erection_start_date   nullable
erection_completion_date
supervisor_user_id
status
created_at
updated_at
submitted_at          nullable
approved_at           nullable
approved_by           nullable
```

Derived display example:

```text
26-2-0001 B / Cell-3
```

### 16.4 Attachment / JCC Model

Suggested logical structure:

```text
attachments
-----------
id
scope_type            PACKAGE | CELL_REPORT
scope_id
attachment_type       JCC_LETTER | TOWER_PHOTO
original_filename
storage_key
mime_type
size_bytes
uploaded_by
created_at

jcc_pages (only if photo-based multi-page JCC needs separate stored image objects)
---------
id
jcc_document_id
attachment_id
page_order
```

Business rules:

- `JCC_LETTER`: one active **logical JCC document per E&C package**, not per cell.
- The JCC may be one-page or multi-page.
- Multi-page PDF remains one file/document.
- Multiple photographed JCC pages, when supported, are grouped as one JCC document with ordered pages.
- `TOWER_PHOTO`: maximum five photos.
- `TOWER_PHOTO` aggregate size: maximum 5 MB.

---

## 17. Search Index Strategy

Database indexes should be created for frequently searched values:

- `cooling_tower_serial_no`
- normalized/lowercase `customer`
- `model`
- `erection_completion_date`
- `submitted_at`
- `motor_serial_no`
- `fan_serial_no`
- `drive_shaft_serial_no`
- `gearbox_serial_no`

---

## 18. Audit and Data Integrity

Recommended audit events:

- User signup.
- Login/logout/security session changes.
- Report creation.
- Important field changes.
- JCC upload/replacement/page changes.
- Tower Photo upload/replacement/removal.
- Review confirmation.
- Submission.
- Approval.
- Admin changes.

Submitted/approved records must not be silently modified.

---

## 19. API Architecture Preview

Indicative endpoints only; final naming may change.

### Authentication

```text
POST /api/auth/signup
POST /api/auth/login
POST /api/auth/refresh
POST /api/auth/logout
POST /api/auth/forgot-password
POST /api/auth/reset-password
```

### Supervisor Reports

```text
POST   /api/reports
GET    /api/reports/mine
GET    /api/reports/{report_id}
PATCH  /api/reports/{report_id}
POST   /api/reports/{report_id}/review
POST   /api/reports/{report_id}/submit
```

### JCC / Shared Package Attachments

```text
POST   /api/ecr-packages/{package_id}/jcc
GET    /api/ecr-packages/{package_id}/jcc
DELETE /api/ecr-packages/{package_id}/jcc
```

If photo-based multi-page JCC capture is supported, the same endpoint may accept an ordered page set, or dedicated page endpoints may be added.

### Tower Photos

```text
POST   /api/ecr-packages/{package_id}/tower-photos
DELETE /api/ecr-packages/{package_id}/tower-photos/{attachment_id}
GET    /api/ecr-packages/{package_id}/tower-photos/{attachment_id}
```

### Admin

```text
GET   /api/admin/reports/search
GET   /api/admin/reports/{report_id}
POST  /api/admin/reports/{report_id}/approve
GET   /api/admin/users
PATCH /api/admin/users/{user_id}
POST  /api/admin/users/{user_id}/reset-password
POST  /api/admin/users/{user_id}/force-logout
```

### Output

```text
GET /api/reports/{report_id}/print
GET /api/reports/{report_id}/pdf
```

---

## 20. Validation Rules Preview

Validation must exist both in the frontend for usability and in the backend as the authoritative enforcement layer.

Examples:

- Cooling Tower Serial No. required.
- Tower suffix required when multiple-tower flag is true.
- Cell No. must be a positive integer; no arbitrary upper cap.
- Erection Start Date optional.
- Erection Completion Date required.
- Submission Date generated automatically.
- Supervisor derived from authenticated user.
- JCC: one active logical document per E&C package, not per cell.
- JCC may be multi-page.
- Tower photos: maximum five.
- Tower photo total payload: maximum 5 MB.
- Alignment values: -1.0 through +1.0 in increments of 0.1.
- Submitted reports cannot be edited by supervisor.
- Submitted JCC/Tower Photos cannot be replaced by supervisor.

---

## 21. Implementation Preview / Roadmap

### Phase 0 - Requirements Freeze and Form Mapping

- Confirm project terminology.
- Map every field from all three pages of the existing form.
- Mark each field as mandatory, optional, conditional, computed, or repeating.
- Define data type and validation.
- Define Yes/No/N/A controls.
- Define DE/NDE control semantics.
- Define final report/PDF layout requirements.
- Confirm one shared JCC document and multi-page behavior.
- Confirm Tower Photo rules.
- Confirm admin approval workflow.

### Phase 1 - Project Skeleton and Infrastructure

- Repository structure.
- FastAPI application skeleton.
- PostgreSQL configuration.
- SQLAlchemy models baseline.
- Alembic migrations.
- Environment configuration.
- Docker/dev environment.
- Basic automated tests.

### Phase 2 - Authentication and User Management

- Signup.
- Login.
- Forgot/reset password.
- Persistent browser/device session.
- Logout.
- User roles.
- Admin user list.
- Disable/enable user.
- Reset password.
- Force logout.

### Phase 3 - Core Report Identity and Draft Lifecycle

- Create new E&C report.
- Cooling Tower Serial No.
- Single/multiple tower logic.
- Dynamic suffix handling.
- Cell number.
- Header fields.
- Auto supervisor.
- Draft state.
- Autosave.
- My Previous Reports.
- E&C package/group relationship.

### Phase 4 - Technical Form Digitization

- Motor.
- Fan and blades.
- Fan cylinder.
- Drive shaft.
- Gearbox.
- Fill.
- Eliminator.
- FC valves.
- Nozzles.
- Bearing housing.
- Belt/pulley.
- Lubrication.
- Hardware.
- Torque table.
- Other optionals.
- Detailed report and comments.

### Phase 5 - DE/NDE and Alignment Interface

- Interactive DE graphic.
- Interactive NDE graphic.
- -1.0 to +1.0 values.
- 0.1 steps.
- Numeric persistence.
- Rendering in final report.

### Phase 6 - Attachments

- One logical JCC document per E&C package.
- Multi-page PDF support.
- Photo-based JCC page grouping/order if used.
- JCC replacement while Draft only.
- JCC locked after submission.
- Tower Photo multi-upload.
- Maximum five photos.
- Total Tower Photo cap 5 MB.
- Frontend counters/progress.
- Backend count/aggregate-size enforcement.
- Secure attachment retrieval.
- Attachment metadata.

### Phase 7 - Review / Confirm / Submit / Approve

- Review screen.
- Confirm action.
- Submit action.
- Supervisor read-only after submission.
- Attachment lock after submission.
- Admin approval.
- Status history.
- Audit trail.

### Phase 8 - Admin Search

- Cooling Tower Serial No. search without suffix.
- Hierarchical tower selection.
- Hierarchical cell selection.
- Fan Serial search.
- Drive Shaft Serial search.
- Gearbox Serial search.
- Motor Serial search.
- Customer/model/date filters.
- Full report display.
- Shared JCC/Tower Photo visibility.

### Phase 9 - Final Report / PDF

- Printable report view close to existing hardcopy.
- Page 1 technical data.
- Page 2 technical/alignment information.
- Page 3 detailed report/customer comments/signature area.
- Consistent PDF generation.

### Phase 10 - Testing and Pilot

Test Android browsers, desktop admin, weak/mobile networks, autosave recovery, duplicate reports, JCC multi-page behavior, photo-size limits, search accuracy, PDF layout, permission boundaries, and submission locking.

### Phase 11 - Production Rollout

- Production server.
- HTTPS.
- PostgreSQL backups.
- Object/file storage backup.
- Monitoring/logging.
- User onboarding.
- Admin documentation.
- Supervisor quick guide.

### Phase 12 - Future Android Application

- Native Kotlin/Jetpack Compose UI.
- Same FastAPI backend.
- Local draft persistence.
- Offline entry and later synchronization.
- Camera-optimized upload.

---

## 22. Suggested Repository Structure

```text
ECR/
|- app/
|  |- api/
|  |- auth/
|  |- core/
|  |- models/
|  |- schemas/
|  |- services/
|  |- storage/
|  |- reports/
|  `- main.py
|- alembic/
|- templates/
|- static/
|- tests/
|- docs/
|- .env.example
|- docker-compose.yml
|- pyproject.toml
|- PROJECT_OVERVIEW.md
`- TODO.md
```

---

## 23. Non-Goals for Initial Release

Unless later requested, the first core release should not attempt to include:

- Native Android app.
- AI-based engineering acceptance/rejection.
- Automatic warnings for alignment limits.
- Full maintenance/service history beyond E&C records.
- Complex job assignment workflow.
- Offline-first browser synchronization.
- Large ERP integration.

---

## 24. Key Architectural Principle

The project must not be implemented as a digital image of three paper pages.

The source of truth is structured engineering data:

```text
Cooling Tower / E&C Package
   -> shared JCC and supporting files
   -> Tower suffix when required
      -> Cell
         -> E&C Report
            -> Equipment / readings / comments / approval
```

The traditional three-page Erection & Commissioning Completion Report is an output representation of this structured data.

This design gives the Service team searchable records today and creates a foundation for future asset history, component tracking, mobile applications, and engineering analytics without forcing a redesign of the core database.
