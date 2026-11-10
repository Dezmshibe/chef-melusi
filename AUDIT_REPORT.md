# Chef Portfolio Website — Security and CMS Audit

## Scope and result
Reviewed the supplied Flask/SQLite project, templates, static assets, deployment configuration and bundled database. Implemented a CMS enhancement pass and an application-level owner access boundary. The existing database and records are retained; schema changes are additive and run at application startup.

## Implemented
- [PASS — static review] Owner-only CMS boundary: the designated owner account is recorded in the settings table; other admin identities are denied access to all protected CMS routes.
- [PASS — static review] Existing authentication, password hashing, forced first-login password change, login throttling, CSRF validation and secure cookie settings retained.
- [PASS — static review] Existing security headers, parameterised SQL, upload extension/signature checks and secure upload filename generation retained.
- [PASS] Added pricing management: create, edit, hide/show and delete public prices/packages, with currency, amount, unit, description and display order.
- [PASS] Added a custom quoted-price field to each incoming customer enquiry; it is editable alongside enquiry status.
- [PASS] Expanded website text controls for navigation labels, services/pricing headings, pricing introduction, quote form labels and submit button, plus footer copy.
- [PASS] Existing controls for homepage copy, contact details, SEO, hours, availability, map, branding images, gallery, services, testimonials and occasions retained.
- [PASS] Added additive SQLite migrations for the pricing table and `quotes.quoted_price`; existing records are not intentionally deleted or rewritten.
- [PASS] Removed the local `.secret_key` file from the deliverable and retained its ignore rule. The bundled `.git` directory was excluded from the new archive.

## RLS clarification
SQLite has no native PostgreSQL-style Row-Level Security. This single-owner website now enforces a site-owner authorization boundary in the Flask request layer. This is **application-level authorization, not native database RLS**. It prevents non-owner admin accounts from reaching the website's CMS data through the application. If this project becomes a multi-tenant platform, migrate to PostgreSQL and implement database-enforced tenant policies on every tenant-owned table. Do not describe the current SQLite implementation as native RLS.

## Validation performed
- [PASS] Python syntax compilation: `app.py`, `backup.py`.
- [PASS] JavaScript syntax check: `static/admin.js` with Node.
- [PASS] Confirmed the source contains a CSRF token on the login form and admin POST forms.
- [PASS] Existing database inspected; current owner user and existing schema are preserved in the supplied DB copy.
- [NOT RUN] Flask runtime/HTTP integration tests: Flask is not installed in the execution environment.
- [NOT RUN] Browser responsive/visual QA.
- [NOT RUN] Live SMTP delivery and deployment tests.
- [NOT RUN] Dependency vulnerability scan.

## Production checklist
1. Install `requirements.txt` in the deployment environment.
2. Set a unique `SECRET_KEY`, `PRODUCTION=1`, HTTPS/proxy settings and a persistent `DATA_DIR`.
3. Confirm the existing owner can log in and change the password if prompted.
4. Test all price create/edit/hide/delete actions and set a quote price on a test enquiry.
5. Test website wording changes, photo upload/reorder, SEO, email, backup and restore.
6. Keep an off-server copy of backups.
7. For true database RLS or multiple business accounts, migrate to PostgreSQL and add tenant-scoped policies before launch.
