---
name: proclean-change-verification
description: Verify every implemented ProClean-App change before Codex claims it is complete, fixed, safe, or ready for commit, merge, push, deployment, or production closure. Use after implementation and again before each integration or deployment action to reclassify the actual diff, run level-specific and domain-specific evidence, protect pre-existing work, and block closure on failures, missing checks, scope drift, secrets, or unexpected files.
---

# Verify ProClean changes before completion

Require fresh evidence. Do not rely on an earlier run, an implementer's claim, or the intended scope.

## Reconstruct scope and reclassify

1. Read the original `PROCLEAN ROUTING` decision, approved design/plan, acceptance criteria, exclusions, and recorded pre-existing changes.
2. Run `git status -sb`; confirm branch and upstream state.
3. Inspect the full task diff and the content of every task-created untracked file. Do not inspect sensitive pre-existing contents merely to complete the review.
4. Separate approved task changes, pre-existing work, and unexpected changes.
5. Re-read `../proclean-task-router/references/protected-surfaces.md` when any path or effect is uncertain.
6. Reclassify from the actual diff. If it triggers a higher level, stop and return to the higher routing gate before claiming success.

## Apply universal checks

- Confirm every changed path is intended and the smallest approved change surface was preserved.
- Confirm unrelated modified/untracked files remain untouched and unstaged.
- Search the task diff for secrets, credentials, private data, debug output, temporary artifacts, unsafe defaults, and accidental generated files.
- Run targeted tests first, then the broader regression required by risk and affected surfaces.
- Treat skipped, unavailable, stale, or flaky checks as missing evidence unless the approved plan explicitly defines another control.
- Do not use passing tests to excuse incorrect behavior, scope drift, or missing visual/domain verification.

## Run the level profile

### L1 SURGICAL

- Render or open the affected screen.
- Verify requested viewport and interaction states.
- Confirm action behavior is unchanged.
- Confirm no backend, route, JavaScript flow, database, document, calculation, permission, shared asset, dependency, or production file changed.

### L2 CONTROLLED

- Run targeted automated tests for every changed function, route, form, or client-side flow.
- Exercise success, empty, validation, unauthorized, and error states as applicable.
- Verify the module visually and inspect logs for new errors.
- Confirm data contracts, persistence semantics, permissions, and unrelated module behavior remain unchanged.

### L3 CRITICAL

Run every applicable domain profile:

- **Payroll and administrative calculations:** reconcile controlled fixtures before/after; totals, counts, days, incidents, amounts, and export columns must match expected controls exactly.
- **DOCX/PDF/PNG/LibreOffice:** generate representative documents through the production-compatible path, convert and render them, inspect every page, and verify placeholders, fonts, tables, wrapping, headers/footers, page count, filenames, and governed hashes.
- **Database and persistence:** use a fixture or verified copy; test migration/initialization, idempotency, duplicates, restart persistence, rollback, and audit/history effects. Never test destructive behavior on production data.
- **Roles and permissions:** test admin, each affected ordinary role, and unauthorized direct access.
- **Imports and exports:** test valid, empty, duplicate, malformed, boundary, and encoding/newline cases as applicable; reconcile row counts, required fields, byte layout, totals, exclusions, and filenames.
- **Infrastructure and deployment:** run local import/build/startup and smoke checks while preserving SQLite volume and LibreOffice assumptions; inspect logs. Do not deploy automatically.

## Enforce integration gates

- Block completion, commit, merge, push, and deployment when a required check fails, evidence is missing, risk is unresolved, or the diff contains unexpected changes.
- Require explicit user authorization separately for commit, push, pull request, deployment, and production mutation.
- Before an authorized commit, confirm only reviewed intended paths are staged; never use `git add .`.
- Before an authorized direct push to `main`, confirm all gates pass, the branch is current, and the exact outgoing diff is reviewed.
- Before an authorized Railway deployment, confirm a current recoverable SQLite backup and documented restore procedure when persistence is affected. Never store a production backup in the repository.
- After an authorized Railway deployment, run the approved non-destructive smoke test, inspect logs, and compare critical invariants. On failure, stop closure and follow the rollback procedure.

## Report evidence

Output:

```text
PROCLEAN VERIFICATION
Final level: <L1 SURGICAL | L2 CONTROLLED | L3 CRITICAL>
Scope match: <yes/no + evidence>
Pre-existing changes preserved: <yes/no + paths>
Commands/checks run: <exact>
Results: <pass/fail/blocked with counts>
Protected surfaces: <unchanged or verified profiles>
Git review: <branch, status, diff, untracked review, staging state>
Residual risks: <none or explicit>
Files changed: <task paths>
Integration gate: <blocked | eligible for specifically authorized next action>
Suggested commit: <message; commands not executed unless authorized>
```

Do not say complete, fixed, safe, or ready when required evidence is missing. State the missing check and blocker plainly.
