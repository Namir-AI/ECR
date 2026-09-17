# ECR Project TODO

This TODO tracks implementation of the digital Erection & Commissioning Report application.

Priority convention:

- **P0** - required for the first usable release.
- **P1** - important for production readiness.
- **P2** - useful enhancement after the core system works.

Status convention:

- [ ] Not started
- [x] Completed

---

## 0. Requirements and Design Freeze

### Business model

- [ ] **P0** Confirm final terminology: `Cooling Tower Serial No.` is the primary serial reference.
- [ ] **P0** Remove the term `Base Paharpur Serial` from product UI/specification.
- [ ] **P0** Confirm single-tower behaviour: no suffix.
- [ ] **P0** Confirm multi-tower behaviour: suffix starts from A, then B, C, D, etc.
- [ ] **P0** Ensure there is no unsuffixed tower entry in a multi-tower case.
- [ ] **P0** Ensure tower suffix is not artificially limited to A-D.
- [ ] **P0** Confirm each tower may have multiple cells.
- [ ] **P0** Confirm typical cell count 1-10 but no software upper limit.
- [ ] **P0** Confirm one E&C report belongs to exactly one cell.
- [ ] **P0** Define duplicate-report policy for the same Cooling Tower Serial + suffix + cell.

### Existing report mapping

- [ ] **P0** Create a full field inventory from page 1 of the present report.
- [ ] **P0** Create a full field inventory from page 2 of the present report.
- [ ] **P0** Create a full field inventory from page 3 of the present report.
- [ ] **P0** Mark every field as mandatory, optional, conditional, repeating, or system-generated.
- [ ] **P0** Define input type for every field.
- [ ] **P0** Define units for numeric engineering fields.
- [ ] **P0** Define whether blank, No, and N/A have different meanings for each Yes/No field.
- [ ] **P0** Identify repeating structures such as blade serial numbers and fastener torque rows.
- [ ] **P0** Confirm signature requirements for supervisor and customer representative.
- [ ] **P1** Decide whether customer seal image capture is needed.

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

- [ ] **P0** Define final application folder structure.
- [ ] **P0** Add `pyproject.toml`.
- [ ] **P0** Pin supported Python version.
- [ ] **P0** Add FastAPI dependency.
- [ ] **P0** Add SQLAlchemy 2.x.
- [ ] **P0** Add Alembic.
- [ ] **P0** Add PostgreSQL driver.
- [ ] **P0** Add Pydantic settings/environment configuration.
- [ ] **P0** Create `.env.example` without secrets.
- [ ] **P0** Add `.gitignore`.
- [ ] **P0** Create local development database configuration.
- [ ] **P1** Add Dockerfile.
- [ ] **P1** Add Docker Compose for app + PostgreSQL.
- [ ] **P1** Add linting/formatting configuration.
- [ ] **P1** Add pytest baseline.
- [ ] **P1** Add pre-commit hooks if useful.
- [ ] **P1** Add CI workflow for tests/linting.

---

## 2. Database Foundation

### Core database

- [ ] **P0** Configure PostgreSQL connection.
- [ ] **P0** Configure SQLAlchemy session management.
- [ ] **P0** Configure Alembic migrations.
- [ ] **P0** Create initial migration.

### Users and sessions

- [ ] **P0** Create `users` table/model.
- [ ] **P0** Add unique Employee ID.
- [ ] **P0** Add unique/validated Mobile Number policy.
- [ ] **P0** Add optional/required email field depending on final password recovery decision.
- [ ] **P0** Add role field.
- [ ] **P0** Add active/disabled status.
- [ ] **P0** Add created/updated timestamps.
- [ ] **P0** Store password hash only; never store plaintext passwords.
- [ ] **P0** Create persistent `user_sessions` / refresh-token session table.
- [ ] **P0** Record session creation, last used time, revocation state, browser/device metadata where practical.

### Reports

- [ ] **P0** Create `reports` model/table.
- [ ] **P0** Add Cooling Tower Serial No.
- [ ] **P0** Add multi-tower flag.
- [ ] **P0** Add nullable tower suffix.
- [ ] **P0** Add Cell No.
- [ ] **P0** Add Customer.
- [ ] **P0** Add Customer Order No. if retained from existing report.
- [ ] **P0** Add Cooling Tower Series.
- [ ] **P0** Add Model.
- [ ] **P0** Add Place of Installation.
- [ ] **P0** Add optional Erection Start Date.
- [ ] **P0** Add required Erection Completion Date.
- [ ] **P0** Add supervisor user foreign key.
- [ ] **P0** Add report status.
- [ ] **P0** Add created/updated/submitted/approved timestamps.
- [ ] **P0** Add approved_by admin foreign key.
- [ ] **P0** Add uniqueness/duplicate handling for Cooling Tower Serial + suffix + cell.

### Technical section tables/models

- [ ] **P0** Motor model/table.
- [ ] **P0** Fan model/table.
- [ ] **P0** Fan blade serial/repeating rows model.
- [ ] **P0** Fan cylinder model.
- [ ] **P0** Drive shaft model.
- [ ] **P0** Gearbox model.
- [ ] **P0** Fill model.
- [ ] **P0** Eliminator model.
- [ ] **P0** FC valves model.
- [ ] **P0** Nozzles model.
- [ ] **P0** Bearing housing model.
- [ ] **P0** Belt & pulleys model.
- [ ] **P0** Lubrication/oil model.
- [ ] **P0** General/tower hardware model.
- [ ] **P0** Fastener torque repeating rows model.
- [ ] **P0** Mechanical equipment alignment model.
- [ ] **P0** DE/NDE numeric reading model.
- [ ] **P0** Other optionals model.
- [ ] **P0** Detailed report/comments model.
- [ ] **P0** Signature metadata model if signatures are captured digitally.

### Attachments

- [ ] **P0** Create `attachments` model/table.
- [ ] **P0** Add attachment type enum: `JCC_LETTER`, `TOWER_PHOTO`.
- [ ] **P0** Add report foreign key.
- [ ] **P0** Add original filename.
- [ ] **P0** Add generated storage key/path.
- [ ] **P0** Add MIME type.
- [ ] **P0** Add byte size.
- [ ] **P0** Add uploader.
- [ ] **P0** Add upload timestamp.

### Audit

- [ ] **P1** Create audit log table.
- [ ] **P1** Define auditable actions.
- [ ] **P1** Store actor, target, event, timestamp, and changed values where relevant.

---

## 3. Authentication and User Management

### Signup

- [ ] **P0** Build simple Sign Up page.
- [ ] **P0** Add Full Name.
- [ ] **P0** Add Employee ID.
- [ ] **P0** Add Mobile Number.
- [ ] **P0** Add Email according to final recovery method.
- [ ] **P0** Add Password.
- [ ] **P0** Add Confirm Password.
- [ ] **P0** Validate duplicate Employee ID/mobile/email as applicable.
- [ ] **P0** Hash password using Argon2id or approved alternative.

### Login

- [ ] **P0** Build simple Login page.
- [ ] **P0** Allow approved identifier(s): Employee ID and/or Mobile Number.
- [ ] **P0** Implement password verification.
- [ ] **P0** Implement persistent session for one-time-style login per browser/device.
- [ ] **P0** Use secure HttpOnly/SameSite cookies where applicable.
- [ ] **P0** Implement refresh/session rotation.
- [ ] **P0** Add manual Logout.
- [ ] **P0** Revoke session on logout.

### Forgot password

- [ ] **P0** Finalize recovery method.
- [ ] **P0** Implement Forgot Password page.
- [ ] **P0** Implement recovery token/OTP expiry.
- [ ] **P0** Implement Reset Password page.
- [ ] **P0** Revoke existing sessions after password reset as appropriate.

### Admin user management

- [ ] **P0** Build Admin Users list.
- [ ] **P0** View account/profile details.
- [ ] **P0** Show status and last login/session information.
- [ ] **P0** Disable user.
- [ ] **P0** Enable user.
- [ ] **P0** Force logout user sessions.
- [ ] **P0** Reset password.
- [ ] **P0** Ensure admin cannot retrieve plaintext password because no plaintext password exists.

---

## 4. Supervisor Dashboard

- [ ] **P0** Create mobile-first supervisor home page.
- [ ] **P0** Add `New Erection & Commissioning Report` button.
- [ ] **P0** Do not require assigned jobs.
- [ ] **P0** Add `My Previous Reports` section.
- [ ] **P0** Show serial no., suffix, cell, customer, date, and status.
- [ ] **P0** Add View action.
- [ ] **P0** Ensure submitted/completed report is read-only for supervisor.
- [ ] **P1** Add pagination/infinite scroll if report history grows.
- [ ] **P1** Add simple search/filter within own reports.

---

## 5. New Report - Identity Section

- [ ] **P0** Create report draft immediately on starting New Report.
- [ ] **P0** Add Customer input.
- [ ] **P0** Add Cooling Tower Serial No. input.
- [ ] **P0** Add Multiple Towers Yes/No selector.
- [ ] **P0** Hide suffix when Multiple Towers = No.
- [ ] **P0** Require suffix when Multiple Towers = Yes.
- [ ] **P0** Support suffixes beyond D.
- [ ] **P0** Add Cell No. numeric control.
- [ ] **P0** Do not enforce upper cell limit.
- [ ] **P0** Add Cooling Tower Series.
- [ ] **P0** Add Model.
- [ ] **P0** Add Place of Installation.
- [ ] **P0** Add optional Erection Start Date.
- [ ] **P0** Add required Erection Completion Date.
- [ ] **P0** Auto-fill Supervisor / Erected By.
- [ ] **P0** Do not allow supervisor identity to be spoofed through normal form editing.
- [ ] **P0** Set Submission Date only at final submission.
- [ ] **P1** Normalize serial input whitespace/casing without damaging displayed value.

---

## 6. Technical Form UI

### General mobile form behaviour

- [ ] **P0** Use mobile-first layout.
- [ ] **P0** Use touch-friendly controls.
- [ ] **P0** Use collapsible technical sections.
- [ ] **P0** Preserve entered values when navigating between sections.
- [ ] **P0** Display mandatory fields clearly.
- [ ] **P0** Provide N/A where technically required instead of forcing false No values.
- [ ] **P1** Add section completion indicators.

### Motor

- [ ] **P0** Implement all motor fields from current report.
- [ ] **P0** Include Motor Serial No. as searchable dedicated field.

### Fan

- [ ] **P0** Implement Fan Serial No.
- [ ] **P0** Implement Diameter & Type.
- [ ] **P0** Implement Fan Hardware.
- [ ] **P0** Implement number of blades.
- [ ] **P0** Implement fan pitch angle.
- [ ] **P0** Implement dynamic Blade Serial No. rows.
- [ ] **P0** Implement Fan Hub Cover Yes/No/N/A behaviour as finalized.

### Fan Cylinder

- [ ] **P0** Implement height.
- [ ] **P0** Implement material of construction.
- [ ] **P0** Implement blade tip clearance.
- [ ] **P0** Implement blade tip track variation.

### Drive Shaft

- [ ] **P0** Implement Series.
- [ ] **P0** Implement Class.
- [ ] **P0** Implement OAL.
- [ ] **P0** Implement searchable Drive Shaft Serial No.

### Gearbox

- [ ] **P0** Implement Series.
- [ ] **P0** Implement Ratio.
- [ ] **P0** Implement searchable Gearbox Serial No.
- [ ] **P0** Implement Model No.

### Fill

- [ ] **P0** Implement Fill Type/Nomenclature.
- [ ] **P0** Implement Material of Construction.

### Page 2 remaining sections

- [ ] **P0** Implement Eliminator.
- [ ] **P0** Implement FC Valves.
- [ ] **P0** Implement Nozzles.
- [ ] **P0** Implement Bearing Housing.
- [ ] **P0** Implement Belt & Pulleys.
- [ ] **P0** Implement Lubricant / GRDR / Bearing Housing fields.
- [ ] **P0** Implement General/Tower/Hardware selections.
- [ ] **P0** Implement Fastener Torque Details as dynamic/repeating rows.
- [ ] **P0** Implement Radial Value / TIR.
- [ ] **P0** Implement Axial Value / TIR.
- [ ] **P0** Implement Vibration Limit Switch.
- [ ] **P0** Implement Oil Level Switch.

### Page 3

- [ ] **P0** Implement Detailed Report by Erection Team-Leader as large multiline field.
- [ ] **P0** Implement Customer Comments.
- [ ] **P0** Implement Erected By display.
- [ ] **P0** Implement customer representative name if required.
- [ ] **P0** Implement signature capture strategy.
- [ ] **P1** Add voice-to-text support later if browser/device support is acceptable.

---

## 7. DE / NDE Alignment Component

- [ ] **P0** Finalize exact red ellipse/cross visual dimensions.
- [ ] **P0** Place `DE` at center of first control.
- [ ] **P0** Place `NDE` at center of second control.
- [ ] **P0** Define exact number of selectable positions/readings required from current engineering practice.
- [ ] **P0** Implement value range -1.0 to +1.0.
- [ ] **P0** Implement increments/decrements of 0.1.
- [ ] **P0** Prevent invalid step values.
- [ ] **P0** Persist all values numerically.
- [ ] **P0** Render selected readings accurately in final printable report.
- [ ] **P0** Do not add automatic acceptance/limit warning.
- [ ] **P1** Unit test numeric stepping and serialization.

---

## 8. Autosave and Draft Recovery

- [ ] **P0** Autosave changed fields after debounce.
- [ ] **P0** Save on section navigation.
- [ ] **P0** Save before review screen.
- [ ] **P0** Display `Saving...` state.
- [ ] **P0** Display `Saved` state.
- [ ] **P0** Handle temporary save failures visibly.
- [ ] **P0** Retry safe failed autosaves.
- [ ] **P0** Prevent duplicate draft creation due to refresh/double-click.
- [ ] **P0** Restore existing draft after browser reopen when user selects it.
- [ ] **P1** Add conflict/version field to prevent accidental overwrites.

---

## 9. Uploads - JCC Letter

- [ ] **P0** Add JCC Letter upload section.
- [ ] **P0** Allow one JCC Letter file only.
- [ ] **P0** Accept PDF.
- [ ] **P0** Accept approved photo formats.
- [ ] **P0** Define final allowed image MIME types: JPG/JPEG, PNG, WEBP unless revised.
- [ ] **P0** Validate extension and MIME type server-side.
- [ ] **P0** Generate safe internal filename/storage key.
- [ ] **P0** Preserve original filename as metadata.
- [ ] **P0** Allow view/download according to access permissions.
- [ ] **P0** Allow replacement only while report is editable/Draft, if replacement behaviour is approved.
- [ ] **P0** Prevent second active JCC attachment.
- [ ] **P1** Decide whether a specific maximum JCC file size is needed; current requirement specifies no JCC size cap.

---

## 10. Uploads - Tower Photos

- [ ] **P0** Add multiple Tower Photos upload control.
- [ ] **P0** Maximum 5 Tower Photos per report.
- [ ] **P0** Maximum total Tower Photos size = 5 MB per report.
- [ ] **P0** Apply total cap to combined photo files, not per-photo 5 MB.
- [ ] **P0** Enforce maximum count in backend.
- [ ] **P0** Enforce aggregate byte-size cap in backend.
- [ ] **P0** Mirror validation in frontend for immediate feedback.
- [ ] **P0** Show `n of 5 photos`.
- [ ] **P0** Show total uploaded size / 5 MB.
- [ ] **P0** Allow photo removal while Draft.
- [ ] **P0** Recalculate total size after removal/replacement.
- [ ] **P0** Prevent removal/replacement by supervisor after submission.
- [ ] **P0** Validate allowed image MIME types.
- [ ] **P1** Consider optional client-side image compression before upload while preserving engineering usefulness.
- [ ] **P1** Preserve EXIF only if needed; otherwise consider stripping sensitive/unnecessary metadata.

---

## 11. File Storage Service

- [ ] **P0** Define storage interface independent of controller/routes.
- [ ] **P0** Implement local filesystem storage for development if used.
- [ ] **P0** Store only metadata/path/key in PostgreSQL, not arbitrary filesystem path from user input.
- [ ] **P0** Protect attachment access with authorization checks.
- [ ] **P0** Prevent path traversal.
- [ ] **P0** Prevent executable content upload.
- [ ] **P1** Add S3-compatible storage backend for production or future migration.
- [ ] **P1** Define backup strategy for uploaded files.
- [ ] **P1** Define retention policy.

---

## 12. Review, Confirm, Submit, Approve Workflow

### Draft

- [ ] **P0** New report starts as `DRAFT`.
- [ ] **P0** Draft is editable by creating supervisor.

### Review

- [ ] **P0** Add Review action/screen.
- [ ] **P0** Show all report information in a readable summary.
- [ ] **P0** Highlight missing mandatory fields.
- [ ] **P0** Prevent move to confirmation if validation fails.
- [ ] **P0** Set/record `REVIEWED` state when appropriate.

### Confirm

- [ ] **P0** Add explicit confirmation before submission.
- [ ] **P0** Treat Confirm as action unless future requirements require a persistent status.
- [ ] **P0** Show warning that submitted report becomes read-only to supervisor.

### Submit

- [ ] **P0** Set status to `SUBMITTED`.
- [ ] **P0** Auto-set Submission Date/time.
- [ ] **P0** Lock supervisor editing.
- [ ] **P0** Preserve all current report/attachment data.

### Approve

- [ ] **P0** Admin can approve submitted report.
- [ ] **P0** Set `APPROVED` state.
- [ ] **P0** Store approval timestamp.
- [ ] **P0** Store approving admin.
- [ ] **P0** Keep approved report immutable through normal supervisor workflow.

### Future correction flow

- [ ] **P2** Define `RETURNED_FOR_CORRECTION` only if required later.
- [ ] **P2** Define controlled reopening/versioning rather than silently editing submitted data.

---

## 13. Admin Search

### Search form

- [ ] **P0** Cooling Tower Serial No. search field.
- [ ] **P0** Fan Serial No. search field.
- [ ] **P0** Drive Shaft Serial No. search field.
- [ ] **P0** Gearbox Serial No. search field.
- [ ] **P0** Motor Serial No. search field.
- [ ] **P0** Customer search field.
- [ ] **P0** Model search field.
- [ ] **P0** Date From.
- [ ] **P0** Date To.

### Cooling Tower search behaviour

- [ ] **P0** Search by base Cooling Tower Serial No. without suffix requirement.
- [ ] **P0** If one matching report only, allow direct open.
- [ ] **P0** If multiple towers exist, show suffix/tower choices.
- [ ] **P0** If a selected tower has multiple cells, show Cell choices.
- [ ] **P0** If a single unsuffixed tower has multiple cells, show Cell choices directly.
- [ ] **P0** Selecting a cell opens complete E&C report.

### Equipment serial search behaviour

- [ ] **P0** Index Motor Serial No.
- [ ] **P0** Index Fan Serial No.
- [ ] **P0** Index Drive Shaft Serial No.
- [ ] **P0** Index Gearbox Serial No.
- [ ] **P0** Return associated Cooling Tower Serial No.
- [ ] **P0** Return tower suffix if applicable.
- [ ] **P0** Return cell number.
- [ ] **P0** Return customer/model context.
- [ ] **P0** Provide full report View action.

### Search quality

- [ ] **P1** Trim whitespace before serial matching.
- [ ] **P1** Implement case-insensitive serial lookup where appropriate.
- [ ] **P1** Add database indexes for all primary search columns.
- [ ] **P1** Test partial vs exact match policy and document it.

---

## 14. Admin Report View

- [ ] **P0** Create complete report detail page.
- [ ] **P0** Show all header information.
- [ ] **P0** Show all technical sections.
- [ ] **P0** Show DE/NDE data.
- [ ] **P0** Show detailed report and customer comments.
- [ ] **P0** Show JCC attachment.
- [ ] **P0** Show Tower Photos.
- [ ] **P0** Show workflow status.
- [ ] **P0** Show submitting supervisor.
- [ ] **P0** Show submission date.
- [ ] **P0** Show approval data.
- [ ] **P1** Show audit history to authorized admin.

---

## 15. Printable Report / PDF

- [ ] **P0** Recreate page 1 layout from current hardcopy.
- [ ] **P0** Recreate page 2 layout from current hardcopy.
- [ ] **P0** Recreate page 3 layout from current hardcopy.
- [ ] **P0** Populate report from structured database values.
- [ ] **P0** Render tower suffix only when applicable.
- [ ] **P0** Render correct cell identity.
- [ ] **P0** Render DE/NDE graphics/readings.
- [ ] **P0** Render multiline detailed report cleanly.
- [ ] **P0** Render customer comments.
- [ ] **P0** Render signature information according to final decision.
- [ ] **P0** Generate browser-friendly print view.
- [ ] **P0** Generate PDF.
- [ ] **P0** Test long values/text wrapping.
- [ ] **P0** Test blank optional fields.
- [ ] **P0** Test multi-page overflow without corrupting layout.
- [ ] **P1** Consider combined download package containing ECR PDF + JCC Letter + Tower Photos.

---

## 16. Authorization and Security

- [ ] **P0** Define authorization policy by role.
- [ ] **P0** Supervisor can access own draft/private workflow as intended.
- [ ] **P0** Supervisor can view own previous reports.
- [ ] **P0** Supervisor cannot edit submitted/approved reports.
- [ ] **P0** Admin can access all reports.
- [ ] **P0** Protect all attachment downloads.
- [ ] **P0** Protect all admin endpoints.
- [ ] **P0** Use CSRF protection if cookie-based state-changing forms require it.
- [ ] **P0** Validate all API payloads server-side.
- [ ] **P0** Add rate limiting/basic brute-force controls to login/reset flows.
- [ ] **P0** Never log passwords/reset tokens.
- [ ] **P0** Store secrets only in environment/secret store.
- [ ] **P1** Add account lock/rate-control strategy.
- [ ] **P1** Add secure HTTP headers.
- [ ] **P1** Add file-malware scanning if production environment requires it.

---

## 17. Validation Tests

### Identity

- [ ] **P0** Single tower, one cell.
- [ ] **P0** Single tower, multiple cells.
- [ ] **P0** Multiple towers starting A/B/C.
- [ ] **P0** Multi-tower report rejects blank suffix.
- [ ] **P0** Single-tower report does not accidentally store suffix.
- [ ] **P0** Cell number above 10 is accepted.
- [ ] **P0** Zero/negative cell number rejected.

### Dates

- [ ] **P0** Erection Start Date may be blank.
- [ ] **P0** Erection Completion Date required.
- [ ] **P0** Submission Date not manually editable.

### Attachments

- [ ] **P0** One JCC PDF accepted.
- [ ] **P0** One JCC image accepted.
- [ ] **P0** Invalid JCC file type rejected.
- [ ] **P0** Second JCC prevented/replaced according to rule.
- [ ] **P0** 1-5 Tower Photos accepted.
- [ ] **P0** Sixth Tower Photo rejected.
- [ ] **P0** Tower Photos exactly at 5 MB aggregate accepted according to byte-definition decision.
- [ ] **P0** Tower Photos above 5 MB aggregate rejected.
- [ ] **P0** Removing a photo decreases aggregate size correctly.
- [ ] **P0** Frontend and backend enforce same logical rules.

### Serial search

- [ ] **P0** Search Cooling Tower Serial without suffix returns all related tower/cell options.
- [ ] **P0** Fan serial returns correct report.
- [ ] **P0** Drive Shaft serial returns correct report.
- [ ] **P0** Gearbox serial returns correct report.
- [ ] **P0** Motor serial returns correct report.

### Workflow

- [ ] **P0** Draft editable.
- [ ] **P0** Review validates required fields.
- [ ] **P0** Confirmation required before submit.
- [ ] **P0** Submitted report read-only to supervisor.
- [ ] **P0** Approved report displays approval metadata.

### Authentication

- [ ] **P0** Signup succeeds with valid data.
- [ ] **P0** Duplicate Employee ID rejected.
- [ ] **P0** Persistent login survives browser restart according to session policy.
- [ ] **P0** Logout revokes current session.
- [ ] **P0** Admin force logout works.
- [ ] **P0** Forgot password flow works.
- [ ] **P0** Plaintext password never stored.

---

## 18. Mobile UX Testing

- [ ] **P0** Test common Android Chrome sizes.
- [ ] **P0** Ensure form usable one-handed where practical.
- [ ] **P0** Ensure buttons are touch friendly.
- [ ] **P0** Ensure numeric fields invoke suitable keyboard.
- [ ] **P0** Ensure date controls are mobile friendly.
- [ ] **P0** Test camera/gallery upload workflow for Tower Photos.
- [ ] **P0** Test PDF picker for JCC Letter.
- [ ] **P0** Test interrupted/slow upload handling.
- [ ] **P0** Test autosave on unstable connection.
- [ ] **P1** Test iOS Safari if supervisors may use iPhones.
- [ ] **P1** Test low-memory device behaviour.

---

## 19. Production Infrastructure

- [ ] **P1** Select production hosting.
- [ ] **P1** Configure HTTPS.
- [ ] **P1** Configure reverse proxy if required.
- [ ] **P1** Configure production PostgreSQL.
- [ ] **P1** Configure automated database backup.
- [ ] **P1** Configure attachment/file backup.
- [ ] **P1** Test restore procedure.
- [ ] **P1** Configure application logs.
- [ ] **P1** Configure error monitoring.
- [ ] **P1** Configure server health checks.
- [ ] **P1** Configure storage capacity monitoring.
- [ ] **P1** Define retention and backup policy.

---

## 20. Pilot Rollout

- [ ] **P1** Select a small pilot group of supervisors.
- [ ] **P1** Run real E&C reports in parallel with current manual process if needed.
- [ ] **P1** Record user friction/issues.
- [ ] **P1** Verify terminology understood by supervisors.
- [ ] **P1** Verify tower suffix behaviour with actual multi-tower jobs.
- [ ] **P1** Verify multi-cell behaviour.
- [ ] **P1** Verify file-size/photo limits are practical on real site photos.
- [ ] **P1** Verify final PDF is accepted internally.
- [ ] **P1** Fix pilot issues before broad deployment.

---

## 21. Documentation

- [x] **P0** Create project overview/architecture/roadmap document.
- [x] **P0** Create implementation TODO document.
- [ ] **P1** Add developer setup README.
- [ ] **P1** Add environment/configuration guide.
- [ ] **P1** Add database migration guide.
- [ ] **P1** Add production deployment guide.
- [ ] **P1** Add backup/restore guide.
- [ ] **P1** Add Admin user guide.
- [ ] **P1** Add short Supervisor mobile usage guide.

---

## 22. Future Enhancements

These are deliberately outside the first core release unless priorities change.

- [ ] **P2** Native Android app using same FastAPI API.
- [ ] **P2** Offline Android draft storage.
- [ ] **P2** Background synchronization when network returns.
- [ ] **P2** Camera compression/optimization workflow.
- [ ] **P2** Push notifications.
- [ ] **P2** Component installation/replacement history.
- [ ] **P2** Maintenance history linked to Motor/Fan/Drive Shaft/Gearbox serials.
- [ ] **P2** Dashboard analytics.
- [ ] **P2** Export to Excel/CSV.
- [ ] **P2** Integration with ERP/job master if required.
- [ ] **P2** QR code linking a tower/cell to its E&C record.
- [ ] **P2** AI-assisted text cleanup of team-leader report while preserving original text.
- [ ] **P2** Engineering rule engine only if formally defined and approved.

---

## Immediate Next Development Sequence

The recommended immediate sequence is:

1. [ ] Freeze the complete field-by-field specification of the existing report.
2. [ ] Decide exact authentication recovery method.
3. [ ] Decide exact signature capture requirement.
4. [ ] Finalize DE/NDE input semantics.
5. [ ] Scaffold FastAPI + PostgreSQL + Alembic project.
6. [ ] Implement User/Auth/Session models and login flow.
7. [ ] Implement Report identity + Draft creation.
8. [ ] Implement mobile autosave.
9. [ ] Implement technical sections.
10. [ ] Implement JCC and Tower Photo uploads.
11. [ ] Implement Review -> Confirm -> Submit -> Approve.
12. [ ] Implement Admin search.
13. [ ] Implement hardcopy-style PDF.
14. [ ] Pilot with real supervisors and real reports.
