# ECR Project TODO

This TODO tracks implementation of the digital Erection & Commissioning Report application.

Priority convention:

- **P0** - required for the first usable release.
- **P1** - important for production readiness.
- **P2** - enhancement after the core system works.

Status convention:

- [ ] Not started
- [x] Completed

---

## 0. Requirements and Design Freeze

### Business model

- [ ] **P0** Confirm `Cooling Tower Serial No.` as the primary serial reference.
- [ ] **P0** Remove `Base Paharpur Serial` terminology from UI/specification.
- [ ] **P0** Confirm single-tower behaviour: no suffix.
- [ ] **P0** Confirm multi-tower behaviour: suffix starts from A, then B, C, D, etc.
- [ ] **P0** Ensure there is no unsuffixed tower entry in a multi-tower case.
- [ ] **P0** Ensure suffix is not artificially limited to A-D.
- [ ] **P0** Confirm each tower may have multiple cells.
- [ ] **P0** Confirm typical cell count 1-10 but no software upper limit.
- [ ] **P0** Confirm one E&C data-entry report belongs to exactly one cell.
- [ ] **P0** Define duplicate-report policy for the same Cooling Tower Serial + suffix + cell.
- [ ] **P0** Define an overall E&C package/group that can contain shared assets plus multiple cell reports.

### Existing report mapping

- [ ] **P0** Create a full field inventory from page 1 of the present report.
- [ ] **P0** Create a full field inventory from page 2 of the present report.
- [ ] **P0** Create a full field inventory from page 3 of the present report.
- [ ] **P0** Mark every field mandatory, optional, conditional, repeating, or system-generated.
- [ ] **P0** Define input type and units for every field.
- [ ] **P0** Define blank / No / N/A semantics.
- [ ] **P0** Identify repeating structures such as blade serial numbers and fastener torque rows.
- [ ] **P0** Confirm signature requirements.

### Mandatory header fields

- [ ] **P0** Customer - mandatory.
- [ ] **P0** Cooling Tower Serial No. - mandatory.
- [ ] **P0** Multiple Towers? - mandatory choice.
- [ ] **P0** Tower suffix - conditional mandatory field for multi-tower reports.
- [ ] **P0** Cell No. - mandatory.
- [ ] **P0** Cooling Tower Series - mandatory.
- [ ] **P0** Model - mandatory.
- [ ] **P0** Place of Installation - mandatory.
- [ ] **P0** Erection Start Date - optional.
- [ ] **P0** Erection Completion Date - mandatory.
- [ ] **P0** Supervisor / Erected By - auto-filled from logged-in user.
- [ ] **P0** Submission Date - auto-generated on submission.

---

## 1. Repository and Development Environment

- [ ] **P0** Define final folder structure.
- [ ] **P0** Add `pyproject.toml` and supported Python version.
- [ ] **P0** Add FastAPI, SQLAlchemy 2.x, Alembic, PostgreSQL driver and Pydantic settings.
- [ ] **P0** Add `.env.example` without secrets.
- [ ] **P0** Add `.gitignore`.
- [ ] **P0** Create local database configuration.
- [ ] **P1** Add Dockerfile and Docker Compose.
- [ ] **P1** Add linting/formatting and pytest baseline.
- [ ] **P1** Add CI workflow.

---

## 2. Database Foundation

### Users and sessions

- [ ] **P0** Create `users` table/model.
- [ ] **P0** Add unique Employee ID and validated Mobile Number.
- [ ] **P0** Add email according to final password recovery decision.
- [ ] **P0** Add role and account status.
- [ ] **P0** Store password hash only; never plaintext.
- [ ] **P0** Create persistent browser/device session model.
- [ ] **P0** Track creation, last-used time, revocation, and device/browser metadata where practical.

### Overall E&C package/group

- [ ] **P0** Create `ecr_packages` / `report_groups` model.
- [ ] **P0** Store Cooling Tower Serial No. at package level.
- [ ] **P0** Store common customer / series / model / installation data at the most appropriate normalized level.
- [ ] **P0** Link all tower/cell reports under the package.
- [ ] **P0** Ensure shared JCC document belongs to this package rather than to an individual cell report.

### Cell reports

- [ ] **P0** Create `reports` model/table.
- [ ] **P0** Link report to E&C package/group.
- [ ] **P0** Add nullable Tower Suffix.
- [ ] **P0** Add Cell No.
- [ ] **P0** Add optional Erection Start Date.
- [ ] **P0** Add required Erection Completion Date.
- [ ] **P0** Add supervisor user foreign key.
- [ ] **P0** Add report status.
- [ ] **P0** Add created/updated/submitted/approved timestamps.
- [ ] **P0** Add approving admin.
- [ ] **P0** Add uniqueness/duplicate handling for Cooling Tower Serial + suffix + cell.

### Technical section models

- [ ] **P0** Motor.
- [ ] **P0** Fan.
- [ ] **P0** Fan blade repeating rows.
- [ ] **P0** Fan cylinder.
- [ ] **P0** Drive shaft.
- [ ] **P0** Gearbox.
- [ ] **P0** Fill.
- [ ] **P0** Eliminator.
- [ ] **P0** FC valves.
- [ ] **P0** Nozzles.
- [ ] **P0** Bearing housing.
- [ ] **P0** Belt & pulleys.
- [ ] **P0** Lubrication/oil.
- [ ] **P0** Hardware.
- [ ] **P0** Fastener torque repeating rows.
- [ ] **P0** Mechanical alignment.
- [ ] **P0** DE/NDE readings.
- [ ] **P0** Other optionals.
- [ ] **P0** Detailed report/comments.
- [ ] **P0** Signature metadata if digital signatures are used.

### Attachments and JCC model

- [ ] **P0** Create attachment/storage metadata model.
- [ ] **P0** Support attachment scope: package-level vs cell-report-level.
- [ ] **P0** Add attachment types `JCC_LETTER` and `TOWER_PHOTO`.
- [ ] **P0** Add original filename, generated storage key, MIME type, byte size, uploader and timestamp.
- [ ] **P0** Create logical JCC document model if needed.
- [ ] **P0** Support ordered JCC pages if photo-based multi-page capture is used.
- [ ] **P0** Enforce one active logical JCC document per overall E&C package, not per cell.

### Audit

- [ ] **P1** Create audit log table.
- [ ] **P1** Audit important field changes, uploads/replacements, review, submission, approval and admin actions.

---

## 3. Authentication and User Management

### Signup / Login / Forgot Password

- [ ] **P0** Build simple Sign Up page.
- [ ] **P0** Full Name, Employee ID, Mobile, Email as required, Password, Confirm Password.
- [ ] **P0** Hash passwords using Argon2id or approved alternative.
- [ ] **P0** Build simple Login page.
- [ ] **P0** Implement persistent session for one-time-style login per browser/device.
- [ ] **P0** Use secure HttpOnly/SameSite cookies where applicable.
- [ ] **P0** Add manual Logout.
- [ ] **P0** Implement Forgot Password and Reset Password flow.
- [ ] **P0** Revoke sessions appropriately after password reset.

### Admin users

- [ ] **P0** Build Admin Users list.
- [ ] **P0** Show profile, status and last-login/session information.
- [ ] **P0** Enable/disable user.
- [ ] **P0** Force logout.
- [ ] **P0** Reset password.
- [ ] **P0** Ensure plaintext password can never be retrieved.

---

## 4. Supervisor Dashboard

- [ ] **P0** Create mobile-first supervisor home page.
- [ ] **P0** Add `New Erection & Commissioning Report` button.
- [ ] **P0** Do not require assigned jobs.
- [ ] **P0** Add `My Previous Reports`.
- [ ] **P0** Show serial no., suffix, cell, customer, date and status.
- [ ] **P0** Add View action.
- [ ] **P0** Submitted/completed reports read-only to supervisor.
- [ ] **P1** Add simple history search/filter.

---

## 5. New Report - Identity Section

- [ ] **P0** Create draft immediately on New Report.
- [ ] **P0** Add Customer.
- [ ] **P0** Add Cooling Tower Serial No.
- [ ] **P0** Add Multiple Towers Yes/No.
- [ ] **P0** Hide suffix for single tower.
- [ ] **P0** Require suffix for multiple towers.
- [ ] **P0** Support suffixes beyond D.
- [ ] **P0** Add Cell No. numeric control with no artificial upper limit.
- [ ] **P0** Add Cooling Tower Series, Model, Place of Installation.
- [ ] **P0** Add optional Erection Start Date.
- [ ] **P0** Add required Erection Completion Date.
- [ ] **P0** Auto-fill Supervisor / Erected By.
- [ ] **P0** Auto-set Submission Date only on final submission.

---

## 6. Technical Form UI

- [ ] **P0** Mobile-first, touch-friendly, collapsible sections.
- [ ] **P0** Preserve values when navigating sections.
- [ ] **P0** Clearly identify mandatory fields.
- [ ] **P0** Provide N/A where technically appropriate.
- [ ] **P0** Implement all Motor fields including searchable Motor Serial No.
- [ ] **P0** Implement Fan fields including searchable Fan Serial No. and dynamic blade serials.
- [ ] **P0** Implement Fan Cylinder.
- [ ] **P0** Implement Drive Shaft including searchable serial number.
- [ ] **P0** Implement Gearbox including searchable serial number.
- [ ] **P0** Implement Fill, Eliminator, FC Valves, Nozzles, Bearing Housing.
- [ ] **P0** Implement Belt & Pulleys, Lubrication/Oil, Hardware.
- [ ] **P0** Implement dynamic Fastener Torque rows.
- [ ] **P0** Implement Radial and Axial TIR fields.
- [ ] **P0** Implement Vibration Limit Switch and Oil Level Switch.
- [ ] **P0** Implement Detailed Report by Erection Team-Leader.
- [ ] **P0** Implement Customer Comments and final signature strategy.

---

## 7. DE / NDE Alignment Component

- [ ] **P0** Finalize red ellipse/cross visual.
- [ ] **P0** Put `DE` and `NDE` at centers.
- [ ] **P0** Implement -1.0 to +1.0 range.
- [ ] **P0** Implement 0.1 increment/decrement.
- [ ] **P0** Persist readings numerically.
- [ ] **P0** Render readings in printable report.
- [ ] **P0** Do not add automatic acceptance/limit warning.

---

## 8. Autosave and Draft Recovery

- [ ] **P0** Autosave changed fields after debounce.
- [ ] **P0** Save on section navigation and before review.
- [ ] **P0** Display Saving/Saved/failure status.
- [ ] **P0** Retry safe failed autosaves.
- [ ] **P0** Prevent duplicate draft creation on refresh/double-click.
- [ ] **P0** Restore existing draft when reopened.
- [ ] **P1** Add optimistic concurrency/version field.

---

## 9. Uploads - JCC Letter / Document

### Scope and document rules

- [ ] **P0** Add one JCC upload/document section for the **overall E&C package**, not for each cell report.
- [ ] **P0** Enforce one active logical JCC document per E&C package.
- [ ] **P0** Make the JCC visible from all relevant report/admin views without duplicating the file for each cell.
- [ ] **P0** Accept PDF.
- [ ] **P0** Support multi-page PDF as one JCC document.
- [ ] **P0** Accept approved photo/image formats.
- [ ] **P0** If a multi-page paper JCC is photographed page-by-page, group those page images as **one logical JCC document**.
- [ ] **P0** Preserve page order for a photo-based multi-page JCC.
- [ ] **P0** Do not count JCC page photos as Tower Photos.

### Validation and lifecycle

- [ ] **P0** Define final allowed image MIME types (JPG/JPEG, PNG, WEBP unless revised).
- [ ] **P0** Validate extension and MIME type server-side.
- [ ] **P0** Generate safe internal filename/storage key.
- [ ] **P0** Preserve original filename as metadata.
- [ ] **P0** Allow view/download according to access permissions.
- [ ] **P0** Allow JCC replacement while the E&C package is editable/Draft.
- [ ] **P0** Replacement supersedes the old active JCC while retaining audit history where implemented.
- [ ] **P0** Lock JCC replacement/edit for supervisor after submission.
- [ ] **P1** Decide a practical JCC size cap if later required; no JCC cap has currently been specified.

### JCC tests

- [ ] **P0** One-page PDF accepted.
- [ ] **P0** Multi-page PDF accepted and treated as one document.
- [ ] **P0** One JCC image accepted.
- [ ] **P0** Multi-page photographed JCC can be grouped in correct page order if image-page workflow is enabled.
- [ ] **P0** Invalid JCC type rejected.
- [ ] **P0** Second active logical JCC prevented/replaced according to rule.
- [ ] **P0** JCC is not duplicated per cell.
- [ ] **P0** JCC is locked for supervisor after submission.

---

## 10. Uploads - Tower Photos

- [ ] **P0** Add multiple Tower Photos upload control.
- [ ] **P0** Maximum 5 Tower Photos.
- [ ] **P0** Maximum total Tower Photos size = 5 MB combined.
- [ ] **P0** Do not treat the limit as 5 MB per photo.
- [ ] **P0** Enforce count and aggregate byte-size in backend.
- [ ] **P0** Mirror validation in frontend.
- [ ] **P0** Show `n of 5 photos`.
- [ ] **P0** Show current total uploaded size / 5 MB.
- [ ] **P0** Allow removal/replacement while Draft.
- [ ] **P0** Recalculate total after removal/replacement.
- [ ] **P0** Lock removal/replacement for supervisor after submission.
- [ ] **P0** Validate allowed image MIME types.
- [ ] **P1** Consider client-side compression while preserving engineering usefulness.

### Tower Photo tests

- [ ] **P0** 1-5 photos accepted.
- [ ] **P0** Sixth photo rejected.
- [ ] **P0** Exactly-at-limit aggregate accepted according to defined byte convention.
- [ ] **P0** Above-limit aggregate rejected.
- [ ] **P0** Removing a photo reduces aggregate size correctly.
- [ ] **P0** JCC page images are excluded from Tower Photo count and 5 MB Tower Photo pool.

---

## 11. File Storage Service

- [ ] **P0** Define storage interface independent of routes/controllers.
- [ ] **P0** Implement local filesystem storage for development if used.
- [ ] **P0** Store metadata/key in PostgreSQL, not unsafe user filesystem paths.
- [ ] **P0** Protect all attachment access with authorization checks.
- [ ] **P0** Prevent path traversal and executable content upload.
- [ ] **P0** Support package-scoped JCC and appropriate Tower Photo scope.
- [ ] **P1** Add S3-compatible storage backend for production/future migration.
- [ ] **P1** Define file backup and retention strategy.

---

## 12. Review, Confirm, Submit, Approve Workflow

### Draft

- [ ] **P0** New report starts `DRAFT`.
- [ ] **P0** Draft editable by creating supervisor.
- [ ] **P0** JCC and Tower Photos may be added/replaced according to Draft rules.

### Review / Confirm

- [ ] **P0** Add Review screen.
- [ ] **P0** Show all entered report information in readable summary.
- [ ] **P0** Highlight missing mandatory fields.
- [ ] **P0** Add explicit Confirm action before submission.
- [ ] **P0** Warn that report and uploads become read-only to supervisor after submission.

### Submit

- [ ] **P0** Set status `SUBMITTED`.
- [ ] **P0** Auto-set Submission Date/time.
- [ ] **P0** Lock supervisor editing.
- [ ] **P0** Lock JCC replacement/edit.
- [ ] **P0** Lock Tower Photo removal/replacement.
- [ ] **P0** Preserve submitted report and attachment state.

### Approve

- [ ] **P0** Admin can approve submitted report.
- [ ] **P0** Set `APPROVED`.
- [ ] **P0** Store approval timestamp and admin.
- [ ] **P0** Keep approved record immutable through normal supervisor workflow.

---

## 13. Admin Search and Report View

- [ ] **P0** Search by Cooling Tower Serial No. without suffix requirement.
- [ ] **P0** Search by Fan Serial No.
- [ ] **P0** Search by Drive Shaft Serial No.
- [ ] **P0** Search by Gearbox Serial No.
- [ ] **P0** Search by Motor Serial No.
- [ ] **P0** Search/filter by Customer, Model, Date From and Date To.
- [ ] **P0** If multiple towers exist, show suffix/tower choices.
- [ ] **P0** If a selected tower has multiple cells, show cell choices.
- [ ] **P0** Selecting a cell opens the complete E&C report.
- [ ] **P0** Show shared JCC document at the overall E&C package level.
- [ ] **P0** Show Tower Photos.
- [ ] **P0** Show workflow status, submitting supervisor, submission and approval data.
- [ ] **P1** Show audit history to authorized admin.

---

## 14. Printable Report / PDF

- [ ] **P0** Recreate page 1 layout from current hardcopy.
- [ ] **P0** Recreate page 2 layout from current hardcopy.
- [ ] **P0** Recreate page 3 layout from current hardcopy.
- [ ] **P0** Populate from structured database values.
- [ ] **P0** Render suffix only when applicable.
- [ ] **P0** Render correct cell identity.
- [ ] **P0** Render DE/NDE graphics/readings.
- [ ] **P0** Render detailed report, customer comments and signature information.
- [ ] **P0** Generate browser print view and PDF.
- [ ] **P0** Test long values, blank optional fields and page overflow.
- [ ] **P1** Consider combined download package: ECR PDF + single JCC document + Tower Photos.

---

## 15. Authorization and Security

- [ ] **P0** Define role authorization policy.
- [ ] **P0** Supervisor can access own drafts/history as intended.
- [ ] **P0** Supervisor cannot edit submitted/approved reports.
- [ ] **P0** Admin can access all reports.
- [ ] **P0** Protect all attachments and admin endpoints.
- [ ] **P0** Validate all API payloads server-side.
- [ ] **P0** Add login/reset rate limiting or brute-force controls.
- [ ] **P0** Never log passwords/reset tokens.
- [ ] **P0** Store secrets only in environment/secret store.
- [ ] **P1** Add secure HTTP headers and file-malware scanning if required.

---

## 16. Validation Tests

### Identity and dates

- [ ] **P0** Single tower, one cell.
- [ ] **P0** Single tower, multiple cells.
- [ ] **P0** Multiple towers starting A/B/C.
- [ ] **P0** Multi-tower report rejects blank suffix.
- [ ] **P0** Single-tower report does not store suffix.
- [ ] **P0** Cell number above 10 accepted.
- [ ] **P0** Zero/negative cell rejected.
- [ ] **P0** Erection Start Date may be blank.
- [ ] **P0** Erection Completion Date required.
- [ ] **P0** Submission Date not manually editable.

### Search

- [ ] **P0** Cooling Tower Serial search returns all related tower/cell options.
- [ ] **P0** Fan, Drive Shaft, Gearbox and Motor serial searches return correct report.

### Workflow

- [ ] **P0** Draft editable.
- [ ] **P0** Review validates mandatory fields.
- [ ] **P0** Confirmation required before submit.
- [ ] **P0** Submitted report read-only to supervisor.
- [ ] **P0** Submitted JCC and Tower Photos locked to supervisor.
- [ ] **P0** Approved report shows approval metadata.

### Authentication

- [ ] **P0** Signup succeeds with valid data.
- [ ] **P0** Duplicate Employee ID rejected.
- [ ] **P0** Persistent login survives browser restart according to session policy.
- [ ] **P0** Logout revokes current session.
- [ ] **P0** Admin force logout works.
- [ ] **P0** Forgot Password flow works.
- [ ] **P0** Plaintext password never stored.

---

## 17. Mobile UX Testing

- [ ] **P0** Test common Android Chrome sizes.
- [ ] **P0** Ensure buttons/fields are touch friendly.
- [ ] **P0** Ensure numeric/date controls are mobile appropriate.
- [ ] **P0** Test camera/gallery workflow for Tower Photos.
- [ ] **P0** Test PDF picker for JCC.
- [ ] **P0** Test multi-page JCC photo capture/grouping if implemented.
- [ ] **P0** Test interrupted/slow uploads.
- [ ] **P0** Test autosave on unstable connection.
- [ ] **P1** Test iOS Safari if required.

---

## 18. Production Infrastructure

- [ ] **P1** Select hosting.
- [ ] **P1** Configure HTTPS and reverse proxy if required.
- [ ] **P1** Configure production PostgreSQL.
- [ ] **P1** Configure automated database and attachment backups.
- [ ] **P1** Test restore procedure.
- [ ] **P1** Configure logs, error monitoring and health checks.
- [ ] **P1** Monitor storage capacity.
- [ ] **P1** Define retention/backup policy.

---

## 19. Pilot Rollout

- [ ] **P1** Select small pilot group of supervisors.
- [ ] **P1** Run real E&C reports in parallel with manual process if needed.
- [ ] **P1** Verify terminology and mobile usability.
- [ ] **P1** Verify tower suffix and multi-cell behaviour.
- [ ] **P1** Verify one shared JCC behaves correctly across multi-cell records.
- [ ] **P1** Verify multi-page JCC upload is practical.
- [ ] **P1** Verify 5-photo / 5-MB limit is practical with real site photos.
- [ ] **P1** Verify final PDF is accepted internally.
- [ ] **P1** Fix pilot issues before broad deployment.

---

## 20. Documentation

- [x] **P0** Create project overview/architecture/roadmap document.
- [x] **P0** Create implementation TODO document.
- [x] **P0** Update documentation to clarify one shared, potentially multi-page JCC document.
- [ ] **P1** Add developer setup README.
- [ ] **P1** Add environment/configuration guide.
- [ ] **P1** Add database migration guide.
- [ ] **P1** Add production deployment and backup/restore guides.
- [ ] **P1** Add Admin guide.
- [ ] **P1** Add short Supervisor mobile guide.

---

## 21. Future Enhancements

- [ ] **P2** Native Android app using same FastAPI API.
- [ ] **P2** Offline Android draft storage and synchronization.
- [ ] **P2** Camera compression/optimization.
- [ ] **P2** Push notifications.
- [ ] **P2** Component installation/replacement history.
- [ ] **P2** Maintenance history linked to equipment serials.
- [ ] **P2** Dashboard analytics.
- [ ] **P2** Excel/CSV export.
- [ ] **P2** ERP/job-master integration.
- [ ] **P2** QR code linking tower/cell to E&C record.
- [ ] **P2** AI-assisted cleanup of team-leader text while preserving original.
- [ ] **P2** Engineering rule engine only if formally defined and approved.

---

## Immediate Next Development Sequence

1. [ ] Freeze complete field-by-field specification from the existing report.
2. [ ] Confirm exact scope/lifecycle of the shared E&C package/group.
3. [ ] Finalize exact JCC UI for multi-page PDF and photo-page capture.
4. [ ] Decide authentication recovery method.
5. [ ] Decide signature capture requirement.
6. [ ] Finalize DE/NDE input semantics.
7. [ ] Scaffold FastAPI + PostgreSQL + Alembic.
8. [ ] Implement User/Auth/Session models and login flow.
9. [ ] Implement E&C package + Cell Report identity and Draft creation.
10. [ ] Implement autosave.
11. [ ] Implement technical sections.
12. [ ] Implement shared JCC and Tower Photo uploads.
13. [ ] Implement Review -> Confirm -> Submit -> Approve.
14. [ ] Implement Admin search.
15. [ ] Implement hardcopy-style PDF.
16. [ ] Pilot with real supervisors and real reports.
