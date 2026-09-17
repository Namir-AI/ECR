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

## 0. Requirements and Architecture Freeze

### Business model

- [ ] **P0** Confirm `Cooling Tower Serial No.` as the primary serial reference.
- [ ] **P0** Remove `Base Paharpur Serial` terminology from UI/specification.
- [ ] **P0** Confirm single-tower behaviour: no suffix.
- [ ] **P0** Confirm multi-tower behaviour: suffix starts A, B, C, D, etc.
- [ ] **P0** Ensure there is no unsuffixed tower entry in a multi-tower case.
- [ ] **P0** Ensure suffix is not artificially limited to A-D.
- [ ] **P0** Confirm each tower may have multiple cells.
- [ ] **P0** Confirm typical cell count 1-10 but no software upper limit.
- [ ] **P0** Confirm one technical E&C report belongs to exactly one cell.
- [ ] **P0** Define duplicate-report policy for the same Cooling Tower Serial + suffix + cell.
- [ ] **P0** Define an overall `ecr_package` / group to contain shared assets plus multiple cell reports.

### Existing report mapping

- [ ] **P0** Create a full field inventory from report page 1.
- [ ] **P0** Create a full field inventory from report page 2.
- [ ] **P0** Create a full field inventory from report page 3.
- [ ] **P0** Mark every field mandatory, optional, conditional, repeating, or system-generated.
- [ ] **P0** Define input type and units for every field.
- [ ] **P0** Define blank / No / N/A semantics.
- [ ] **P0** Identify repeating structures such as blade serials and torque rows.
- [ ] **P0** Confirm final signature requirements.
- [ ] **P0** Confirm final Forgot Password method.

### Infrastructure assumptions

- [x] **P0** Company IT confirms Python applications can be hosted.
- [x] **P0** Company applications such as ERP are hosted in AWS.
- [x] **P0** Company database standard for this environment is MySQL.
- [x] **P0** IT will provide domain/subdomain.
- [ ] **P0** Confirm production MySQL version; target MySQL 8.x unless IT says otherwise.
- [ ] **P0** Confirm MySQL host/database/user/port and network access model before production deployment.
- [ ] **P0** Confirm whether ECR receives a dedicated database/schema and least-privilege user.
- [ ] **P0** Confirm company reverse proxy/load balancer/HTTPS arrangement.
- [ ] **P1** Confirm production file-storage and backup policy.

---

## 1. Phase 1 - Project Foundation + MySQL Infrastructure

- [ ] **P0** Define final application folder structure.
- [ ] **P0** Add `pyproject.toml` and supported Python version.
- [ ] **P0** Decide whether to use `uv`; if used, add `uv.lock`.
- [ ] **P0** Keep `uv` optional as tooling, not an application runtime dependency.
- [ ] **P0** Add FastAPI.
- [ ] **P0** Add SQLAlchemy 2.x.
- [ ] **P0** Add Alembic.
- [ ] **P0** Add Pydantic settings/configuration.
- [ ] **P0** Add a MySQL-compatible Python driver.
- [ ] **P0** Do not add PostgreSQL-specific dependencies/features unless explicitly approved.
- [ ] **P0** Configure MySQL connection via environment variables.
- [ ] **P0** Use `utf8mb4` and suitable collation.
- [ ] **P0** Add `.env.example` without secrets.
- [ ] **P0** Add `.gitignore`.
- [ ] **P0** Create `/health` endpoint.
- [ ] **P0** Configure SQLAlchemy session management.
- [ ] **P0** Configure Alembic migrations.
- [ ] **P0** Create initial migration.
- [ ] **P0** Verify migration upgrade -> downgrade -> upgrade.
- [ ] **P0** Add pytest baseline.
- [ ] **P0** Document local startup procedure.
- [ ] **P1** Add Docker/dev-container support only if useful; do not make it mandatory.
- [ ] **P1** Add lint/format configuration and CI.

---

## 2. Phase 2 - Authentication + User/Admin Foundation

### Users and sessions

- [ ] **P0** Create `users` model/table.
- [ ] **P0** Add unique Employee ID and validated Mobile Number.
- [ ] **P0** Add email according to password recovery decision.
- [ ] **P0** Add Supervisor/Admin roles and active/disabled state.
- [ ] **P0** Store password hash only; never plaintext.
- [ ] **P0** Use Argon2id or approved secure alternative.
- [ ] **P0** Create persistent browser/device session model.
- [ ] **P0** Store session creation, last-use and revocation data.

### Authentication UI/flows

- [ ] **P0** Build simple Sign Up page.
- [ ] **P0** Build simple Login page.
- [ ] **P0** Implement secure persistent login per device/browser.
- [ ] **P0** Implement manual Logout.
- [ ] **P0** Implement Forgot Password / Reset Password foundation.
- [ ] **P0** Revoke sessions appropriately after password reset.

### Admin user management

- [ ] **P0** Build Admin Users list.
- [ ] **P0** Show profile, status and session/last-login information.
- [ ] **P0** Enable/disable users.
- [ ] **P0** Force logout/revoke sessions.
- [ ] **P0** Reset password.
- [ ] **P0** Ensure plaintext passwords cannot be retrieved.

---

## 3. Phase 3 - Supervisor Dashboard + ECR Identity + Draft/Autosave

### E&C package/group

- [ ] **P0** Create `ecr_packages` model/table.
- [ ] **P0** Store Cooling Tower Serial No. at package level.
- [ ] **P0** Store common Customer/Series/Model/Installation data at appropriate normalized level.
- [ ] **P0** Link multiple cell reports to a package.

### Cell reports

- [ ] **P0** Create `reports` model/table.
- [ ] **P0** Link report to E&C package.
- [ ] **P0** Add nullable Tower Suffix.
- [ ] **P0** Add positive Cell No. with no artificial upper cap.
- [ ] **P0** Add optional Erection Start Date.
- [ ] **P0** Add required Erection Completion Date.
- [ ] **P0** Add supervisor user foreign key.
- [ ] **P0** Add report status/timestamps.
- [ ] **P0** Add duplicate handling/constraint for package + suffix + cell.

### Supervisor UI

- [ ] **P0** Create mobile-first supervisor home.
- [ ] **P0** Add `New Erection & Commissioning Report`.
- [ ] **P0** Do not require assigned jobs.
- [ ] **P0** Add `My Previous Reports`.
- [ ] **P0** Submitted/approved reports read-only to supervisor.
- [ ] **P0** Hide suffix for single tower.
- [ ] **P0** Require suffix for multi-tower case.
- [ ] **P0** Support suffixes beyond D.
- [ ] **P0** Auto-fill Supervisor / Erected By.
- [ ] **P0** Set Submission Date only at final submission.

### Autosave

- [ ] **P0** Autosave after debounce.
- [ ] **P0** Save on section navigation and before review.
- [ ] **P0** Display Saving/Saved/error state.
- [ ] **P0** Prevent duplicate Draft creation on refresh/double-click.
- [ ] **P0** Restore Draft after reopen.
- [ ] **P1** Add optimistic concurrency/versioning.

---

## 4. Phase 4 - Complete Technical Form

- [ ] **P0** Implement all Motor fields including searchable Motor Serial No.
- [ ] **P0** Implement Fan fields including searchable Fan Serial No.
- [ ] **P0** Implement dynamic Fan Blade serial rows.
- [ ] **P0** Implement Fan Cylinder.
- [ ] **P0** Implement Drive Shaft including searchable serial.
- [ ] **P0** Implement Gearbox including searchable serial.
- [ ] **P0** Implement Fill.
- [ ] **P0** Implement Eliminator.
- [ ] **P0** Implement FC Valves.
- [ ] **P0** Implement Nozzles.
- [ ] **P0** Implement Bearing Housing.
- [ ] **P0** Implement Belt & Pulleys.
- [ ] **P0** Implement Lubrication/Oil.
- [ ] **P0** Implement General/Tower Hardware.
- [ ] **P0** Implement repeating Fastener Torque rows.
- [ ] **P0** Implement Radial and Axial TIR.
- [ ] **P0** Implement Optionals including switches.
- [ ] **P0** Keep searchable equipment serial values in dedicated indexed MySQL columns.
- [ ] **P0** Avoid opaque JSON for core searchable engineering data.

---

## 5. Phase 5 - DE/NDE + Page 3 + Attachments

### DE/NDE

- [ ] **P0** Finalize red ellipse/cross visual.
- [ ] **P0** Put `DE` and `NDE` at centers.
- [ ] **P0** Implement -1.0 to +1.0 range.
- [ ] **P0** Implement 0.1 increment/decrement.
- [ ] **P0** Persist readings numerically.
- [ ] **P0** Do not add automatic acceptance/limit warnings.

### Page 3

- [ ] **P0** Implement Detailed Report by Erection Team Leader.
- [ ] **P0** Implement Customer Comments.
- [ ] **P0** Implement agreed signature/customer-representative strategy.

### JCC

- [ ] **P0** Create one logical JCC document per overall E&C package, not per cell.
- [ ] **P0** Accept one-page and multi-page PDF.
- [ ] **P0** Accept approved images/photos.
- [ ] **P0** Group multiple photographed pages as one ordered logical JCC document.
- [ ] **P0** Exclude JCC page images from Tower Photo count/size pool.
- [ ] **P0** Allow JCC replacement while Draft.
- [ ] **P0** Lock supervisor replacement after submission.
- [ ] **P0** Do not invent a JCC size cap.

### Tower Photos

- [ ] **P0** Maximum 5 photos.
- [ ] **P0** Maximum 5 MB combined total.
- [ ] **P0** Enforce count and aggregate size server-side.
- [ ] **P0** Mirror validation client-side.
- [ ] **P0** Show `n of 5` and current MB/5 MB.
- [ ] **P0** Allow remove/replace while Draft.
- [ ] **P0** Lock supervisor modification after submission.

### Storage service

- [ ] **P0** Create storage interface independent of routes/controllers.
- [ ] **P0** Implement local filesystem backend for development.
- [ ] **P0** Store file metadata/storage keys in MySQL.
- [ ] **P0** Prevent path traversal/executable uploads.
- [ ] **P0** Protect attachment access with authorization.
- [ ] **P1** Add AWS S3/object-storage backend only if selected for production.

---

## 6. Phase 6 - Review / Confirm / Submit + Approval + Audit

- [ ] **P0** Add Review screen.
- [ ] **P0** Highlight missing mandatory fields.
- [ ] **P0** Block confirmation/submission when validation fails.
- [ ] **P0** Record `REVIEWED` as appropriate.
- [ ] **P0** Add explicit Confirm action.
- [ ] **P0** Set status `SUBMITTED` and submission timestamp.
- [ ] **P0** Lock supervisor editing after submission.
- [ ] **P0** Admin can approve submitted reports.
- [ ] **P0** Set `APPROVED`, approving admin, and timestamp.
- [ ] **P0** Create audit log.
- [ ] **P0** Audit important report, attachment, submission, approval, and admin actions.
- [ ] **P2** Do not implement Return for Correction unless separately approved.

---

## 7. Phase 7 - Admin Search + Full Report Retrieval

### Search fields

- [ ] **P0** Cooling Tower Serial No.
- [ ] **P0** Fan Serial No.
- [ ] **P0** Drive Shaft Serial No.
- [ ] **P0** Gearbox Serial No.
- [ ] **P0** Motor Serial No.
- [ ] **P0** Customer.
- [ ] **P0** Model.
- [ ] **P0** Date From/To.

### Behaviour

- [ ] **P0** Search Cooling Tower Serial without requiring suffix.
- [ ] **P0** If multiple towers exist, show suffix/tower choices.
- [ ] **P0** Then show cells.
- [ ] **P0** Single unsuffixed tower with multiple cells shows cells directly.
- [ ] **P0** Selecting a cell opens complete report.
- [ ] **P0** Package view exposes shared JCC and Tower Photos.
- [ ] **P0** Equipment serial search returns associated package/tower/cell/report context.
- [ ] **P1** Add proper MySQL indexes to all primary search columns.
- [ ] **P1** Document exact vs partial and case-insensitive matching rules.

---

## 8. Phase 8 - Official Print/PDF + Full Integration Testing

### Print/PDF

- [ ] **P0** Recreate official page 1 layout.
- [ ] **P0** Recreate official page 2 layout.
- [ ] **P0** Recreate official page 3 layout.
- [ ] **P0** Populate output from structured MySQL data.
- [ ] **P0** Render tower suffix only when applicable.
- [ ] **P0** Render correct cell identity.
- [ ] **P0** Render DE/NDE graphics/readings.
- [ ] **P0** Handle long comments and optional blanks.
- [ ] **P0** Generate browser print view.
- [ ] **P0** Generate PDF.

### Integration tests

- [ ] **P0** Fresh MySQL database can be created entirely by Alembic migrations.
- [ ] **P0** Signup/login/persistent session/logout/reset tested.
- [ ] **P0** Single-tower and multi-tower flows tested.
- [ ] **P0** Cell above 10 accepted; zero/negative rejected.
- [ ] **P0** Draft/autosave/reopen tested.
- [ ] **P0** JCC multi-page flows tested.
- [ ] **P0** Tower Photo count/aggregate size tested.
- [ ] **P0** Review/confirm/submit/approval tested.
- [ ] **P0** Search paths tested.
- [ ] **P0** Authorization boundaries tested.
- [ ] **P0** Mobile browser usability tested.

---

## 9. Phase 9 - Local Release Candidate + AWS Cloud Readiness

### Local release

- [ ] **P0** Add complete developer/user README.
- [ ] **P0** Add `.env.example` documentation.
- [ ] **P0** Test clean/fresh repository checkout and local installation.
- [ ] **P0** Ensure no hard-coded developer-machine paths or secrets.
- [ ] **P0** Document MySQL setup for local development.
- [ ] **P0** Document migrations and startup.

### AWS/company production readiness

- [ ] **P1** Document required IT-provided values: domain/subdomain, DB host, DB name, DB user/password, port, TLS/reverse proxy/storage details.
- [ ] **P1** Keep DB credentials and AWS secrets in environment/secret store only.
- [ ] **P1** Support external/company-managed MySQL; installer must not assume ownership of the DB server.
- [ ] **P1** Prepare Linux service deployment (systemd or company-approved platform method).
- [ ] **P1** Prepare reverse-proxy/HTTPS integration guidance.
- [ ] **P1** Prepare logs/health checks.
- [ ] **P1** Define database and attachment backup/restore guidance consistent with IT policy.
- [ ] **P1** Support company-selected file backend; add S3 only if required.

### Optional one-command installer

- [ ] **P1** Create `install/install.sh` for a standard Ubuntu/Linux VM if the company AWS access model permits SSH/sudo installation.
- [ ] **P1** Model the experience on the simple OpenAlgo installer flow.
- [ ] **P1** Installer may install app prerequisites, Python/uv, application files and service configuration.
- [ ] **P1** Installer should interactively request domain/configuration where appropriate.
- [ ] **P1** Installer should generate `.env` safely.
- [ ] **P1** Installer should accept externally supplied company MySQL connection information.
- [ ] **P1** Installer must not assume DNS, MySQL server, TLS termination, firewall, or AWS networking are under application control.
- [ ] **P1** Add health verification at installation end.
- [ ] **P2** Add `update.sh`, `backup.sh`, and `restore.sh` after production deployment approach is finalized.

---

## 10. Security and Data Integrity Checklist

- [ ] **P0** Passwords never stored/logged in plaintext.
- [ ] **P0** Secrets never committed.
- [ ] **P0** Validate all backend payloads.
- [ ] **P0** Protect all admin routes.
- [ ] **P0** Protect attachment access.
- [ ] **P0** Add brute-force/rate protections to authentication where appropriate.
- [ ] **P0** Use secure cookies/session settings in production.
- [ ] **P0** Submitted records cannot be silently modified.
- [ ] **P0** Use MySQL foreign keys and indexes correctly.
- [ ] **P0** Avoid PostgreSQL-only SQL/features.
- [ ] **P1** Add security headers/CSRF controls as required by the chosen web/session model.
- [ ] **P1** Coordinate any corporate vulnerability scanning/WAF/security requirements with IT.

---

## 11. Git / AI-Coder Working Rules

For every implementation phase:

1. [ ] Read relevant `PROJECT_OVERVIEW.md`, `TODO.md`, and `AI_CODER_PROMPT.md` sections.
2. [ ] Run `git status` before coding.
3. [ ] Stop on blockers instead of guessing business rules.
4. [ ] Implement only the current approved phase.
5. [ ] Run relevant automated verification.
6. [ ] Provide explicit manual localhost verification steps.
7. [ ] Wait for user `PASS` / approval.
8. [ ] Only then commit and push.
9. [ ] Verify push and clean working tree before next phase.
10. [ ] Never force-push or rewrite history without explicit authorization.

---

## 12. Documentation

- [x] **P0** Project overview / architecture document.
- [x] **P0** Implementation TODO document.
- [x] **P0** Update architecture from PostgreSQL to company MySQL target.
- [x] **P0** Record AWS as primary company production environment.
- [ ] **P0** Add/update master AI-coder prompt.
- [ ] **P1** Developer setup guide.
- [ ] **P1** Admin guide.
- [ ] **P1** Supervisor quick-start guide.
- [ ] **P1** Production deployment guide.
- [ ] **P1** Backup/restore guide.

---

## 13. Future Enhancements

- [ ] **P2** Native Android app using the same FastAPI API.
- [ ] **P2** Offline Android Draft storage and synchronization.
- [ ] **P2** Component installation/replacement history.
- [ ] **P2** Maintenance history linked to equipment serials.
- [ ] **P2** Analytics/dashboard.
- [ ] **P2** Excel/CSV export.
- [ ] **P2** ERP integration if later approved.
- [ ] **P2** QR code linking a tower/cell to its record.
- [ ] **P2** AI-assisted text cleanup only if original text is retained and functionality is explicitly approved.

---

## Immediate Next Sequence

1. [ ] Run Phase 0 with the AI coder: requirements/architecture review only, no coding.
2. [ ] Confirm MySQL version and local-development approach.
3. [ ] Resolve password-recovery and signature questions.
4. [ ] Approve Phase 0.
5. [ ] Start Phase 1 only.
6. [ ] Verify locally.
7. [ ] User approves.
8. [ ] Commit and push.
9. [ ] Continue phase-by-phase through Phase 9.