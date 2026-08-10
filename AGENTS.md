# ProClean-App governance for Codex

These instructions apply only when repository evidence confirms this is ProClean-App. They govern every Codex task in this repository and coexist with the existing Cursor + Superpowers setup. Do not modify `.cursor/` as part of Codex governance work.

## Mandatory routing

- Before any edit, generated file, mutating command, branch, worktree, commit, push, deployment, or data operation, invoke `proclean-task-router` and complete its read-only preflight.
- Begin the implementation handoff with the exact `PROCLEAN ROUTING` block required by that skill.
- Classify from repository evidence and downstream effects, never from apparent line count. A one-line calculation, permission, document, persistence, or deployment change can be L3 CRITICAL.
- Reclassify after diagnosis and from the final diff. If a higher level appears, stop before further mutation and satisfy the higher gate.
- The user may raise a level. Do not lower an L3 classification until evidence resolves every L3 trigger and the user explicitly accepts the downgrade.

## Risk gates

- **L1 SURGICAL:** presentation-only, local, trivially reversible, and outside protected/shared surfaces. After routing, the request itself authorizes the minimal change unless the user imposed another gate.
- **L2 CONTROLLED:** localized behavior in one non-protected module. Diagnose first, define acceptance criteria, present a compact design, obtain design approval, then write and follow a concise plan.
- **L3 CRITICAL:** any protected surface, shared architecture, multi-module effect, material production/reversibility risk, or unresolved ambiguity. Perform a read-only impact audit, obtain explicit design approval, then explicit plan approval before implementation.
- Branches and worktrees require explicit user authorization at every level. Never use isolation mechanics to bypass a gate.

## Protected surfaces

Treat a change as L3 when it modifies or plausibly affects any of these categories:

- payroll, attendance, Headcount, IMSS, SUA, INFONAVIT, finiquitos, vacations, invoices, financial calculations, bank files, reconciliation, or regulatory exports;
- SQLite schemas, migrations, initialization, persistent-volume paths, imports, destructive updates, identifiers, deduplication, history, or audit data;
- authentication, sessions, roles, permissions, credentials, secrets, environment variables, or personally identifying information;
- DOCX templates, placeholders, table geometry, PDF/PNG generation, LibreOffice conversion, filenames, hashes, or medical documents/calculations;
- `app.py`, shared models/helpers, base templates, global CSS/JavaScript, dependencies, Docker, Railway, startup commands, external integrations, or changes spanning modules.

Use the complete and current list in `.agents/skills/proclean-task-router/references/protected-surfaces.md` during routing and escalation.

## Engineering invariants

- Diagnose the existing flow before proposing or implementing a fix. Trace relevant routes, call sites, templates, scripts, tests, persistence, permissions, and recent related changes.
- Prefer the smallest module-scoped change. A visual request does not authorize backend, database, route, JavaScript-flow, permission, document, export, deployment, or global-file changes.
- Do not add dependencies unless the approved design requires them and the user authorizes them.
- Use test-driven development where viable. Preserve established data contracts, calculations, document layouts, export formats, permissions, and LibreOffice compatibility.
- Never operate on production data. For SQLite or imports, use controlled fixtures or verified copies and define rollback before mutation.
- Never read, print, log, expose, commit, or overwrite credentials or private production data. Do not inspect a sensitive file merely because it appears in `git status`.
- Preserve all pre-existing modified and untracked files unless the user explicitly places one in scope. Never clean, reset, move, stage, or overwrite unrelated work.
- Cursor and Codex must not edit the same task simultaneously. Stop if another implementer appears active on the same files or approved scope.
- Treat an approved specification or plan as locked scope. Do not reinterpret governed formulas, layouts, offsets, schemas, or business rules without returning to the approval gate.

## Verification and integration

- Invoke `proclean-change-verification` before saying a change is complete, fixed, safe, or ready for commit, merge, push, or deployment.
- Run fresh tests appropriate to the final level and every affected domain. Earlier runs and implementer claims are not evidence.
- Review `git status`, the full diff, and every task-created untracked file. Separate approved task changes from pre-existing work and unexpected changes.
- Do not commit, merge, push, or deploy with failing tests, missing required evidence, unresolved residual risk, scope drift, secrets, or unexpected files.
- Do not stage with `git add .`. Stage only reviewed, intended paths and only after explicit user authorization.
- Never commit, push, deploy, create a pull request, or modify production automatically. Each action requires explicit user authorization after all applicable gates pass.
- If the user authorizes direct integration to `main`, do it only after final verification passes and the branch is confirmed current. After an authorized Railway deployment, run the approved non-destructive production smoke test, inspect logs, and report the result. A failed smoke test blocks closure and triggers the rollback procedure.

Every completion handoff must include the exact `PROCLEAN VERIFICATION` block, files changed, evidence, residual risks, and a suggested commit message. Provide commands without executing them unless the user authorizes the corresponding action.
