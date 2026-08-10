# ProClean protected surfaces

Any item below triggers L3 CRITICAL when modified or plausibly affected.

## Money, employment, and compliance

- Payroll, Master de Asistencia, worked days, incidents, overtime, bonuses, deductions, taxes, INFONAVIT, net pay, salary bases, and CONTPAQi or bank exports.
- IMSS, SUA, IDSE, AFIL, movements, legally meaningful dates, SDI/SBC, finiquitos, vacations, and social-security reports.
- Invoice amounts, payment status, credit notes, financial exports, and bulk reconciliation.
- Likely names include `nomina`, `asistencia`, `headcount`, `infonavit`, `imss`, `sua`, `idse`, `finiquitos`, `vacaciones`, `facturacion`, `exportacion`, `contpaqi`, `calculo`, `banco`, and `conciliacion`.

## Persistence, identity, and access

- SQLite schemas, migrations, initialization, persistent-volume paths, bulk imports, destructive updates, identifiers, deduplication, and history or audit records.
- Authentication, roles, permissions, admin/user separation, sessions, credentials, API keys, environment variables, and personally identifying information.
- `instance/`, database files, migrations, shared database helpers, and global models.

## Documents and generated files

- DOCX templates, placeholders, table geometry, headers/footers, fonts, page breaks, filenames, hashes, timestamps, and Word/PDF/PNG generation.
- LibreOffice conversion, generated-document helpers, shared export utilities, and document packaging.
- Medical calculations, reference ranges, classification, patient identity, folios, and persisted registration or taking times.
- `docx_templates/`, `examenes_medicos_templates/`, `vitroflex_templates/`, and any production-compatible conversion path.

## Shared architecture and production

- `app.py`, application factories, shared configuration, common utilities, global error handling, dependency files, Docker, Railway, startup commands, production paths, and persistent volumes.
- Shared base templates, navigation, global CSS or JavaScript, context processors, and changes spanning modules.
- External API contracts, secrets, network calls, provider integrations, and rate-limit behavior.

## Escalation examples

- A date-label change whose value is persisted or printed in a medical PDF.
- A modal change that requires a new route, permission path, or shared API response.
- A one-line payroll correction that changes a formula or export field.
- Styling one page through a global stylesheet or shared base template.
- A deploy fix that changes business logic or removes LibreOffice support.

Treat samples and fixtures as non-production only when repository evidence and the approved plan establish that status. Never infer safety from a folder name.
