# Banorte Exportaciones (Fase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development`. One fresh subagent per task. Parent agent retains full context, integrates results, and performs post-task spec compliance + technical review. Subagents **must not** reinterpret schema or `.pag` layout positions; those are locked in the approved design spec.

**Goal:** Ship Nóminas > Exportaciones > Banorte Fase 1 per `docs/superpowers/specs/2026-07-13-banorte-exportaciones-design.md`.

**Architecture:** Package `modules/nomina/banorte/`; routes on existing `nomina_bp`; Banorte DDL in `schema.py`; canonical `.pag` BLOB + `export_items`; synthetic fixtures only; CSRF on Banorte mutations; backend reloads beneficiaries from SQLite before emit.

**Tech Stack:** Flask, SQLite3 (`PRAGMA foreign_keys=ON`), openpyxl, Decimal, pytest, Jinja2, zoneinfo `America/Monterrey`.

**Branch / worktree:** `feature/banorte-exportaciones` only. Never commit on `main`. No push / merge / deploy without explicit user authorization. One commit per completed task after tests + diff review pass.

## Global Constraints

- Spec file is authority for schema and layout field positions.
- Binding amendments (2026-07-13 execution unlock): unique active emp+account; unique `replaces_id`; unique active alias_normalizado; idempotent SHA reimport with `REIMPORT_NO_CHANGE`; alias→inactive resolves only via clear ACTIVO successor; preview drift blocks export; CSRF hmac.compare_digest + SECRET_KEY; in-memory editor only (no draft table).
- Roles: `admin` | `nomina` server-side.
- `Decimal` + `ROUND_HALF_UP`; never `float`.
- No real PII in Git/fixtures/docs/logs.
- No `calc_nomina` wiring.
- No Excel `original_file_blob`.
- Money authority = integer cents columns.
- `validation_status` ≠ lifecycle; use `record_status` only for ACTIVO/INACTIVO/CONFLICTO.
- `replaces_id` only (no `replaced_by_id`).
- Independent synthetic golden (must not import production builder).
- Commits only when user/process authorizes after gate.

## File map

| Path | Responsibility |
|------|----------------|
| `modules/nomina/banorte/models.py` | Enums/dataclasses/`NormalizedPayment` |
| `modules/nomina/banorte/schema.py` | DDL, CHECKs, FKs, partial uniques, ensure |
| `modules/nomina/banorte/repository.py` | Connections with FK pragma; CRUD; transactions |
| `modules/nomina/banorte/money.py` | Parse/round/cents |
| `modules/nomina/banorte/pag_layout.py` | Field tables + builder + limits |
| `modules/nomina/banorte/import_service.py` | ALTAS/FALLIDOS/Reporte |
| `modules/nomina/banorte/matching_service.py` | Match levels + aliases |
| `modules/nomina/banorte/paste_service.py` | Dual lists |
| `modules/nomina/banorte/export_service.py` | Server-side rebuild + persist |
| `modules/nomina/banorte/validators.py` | Upload + readiness + layout limits |
| `modules/nomina/banorte/csrf.py` | Token issue/validate |
| `modules/nomina/banorte/routes.py` | HTTP; register onto bp |
| `modules/nomina/db.py` | One-line ensure hook |
| `modules/nomina/blueprint.py` | One-line register |
| Templates / CSS | Banorte UI only |
| `docs/banorte_pag_layout.md` | Exhaustive field doc |
| `tests/test_banorte_*.py` | Suite |
| `tests/fixtures/banorte/*` | Synthetic + independent golden |
| `.gitignore` | Real Banorte artifacts |

---

## GATE 0 — Branch (parent agent, before Task 1)

- [ ] Create/worktree branch `feature/banorte-exportaciones` from clean `main`.
- [ ] Confirm no Banorte application code yet except approved docs.
- [ ] Confirm user said **spec aprobado y plan aprobado** before coding.

---

### Task 1: Models, schema, repository skeleton, migration + SQL integrity

**Depends on:** GATE 0  
**Files:**
- Create: `modules/nomina/banorte/__init__.py`, `models.py`, `schema.py`, `repository.py`
- Modify: `modules/nomina/db.py`
- Test: `tests/test_banorte_migration.py`, `tests/test_banorte_schema_integrity.py`

**Interfaces produced:**
- `ValidationStatus`, `RecordStatus`, `SourceKind`, `MatchKind`, `ImportDecision`
- `NormalizedPayment`
- `ensure_banorte_tables(conn) -> None`
- `BANORTE_TABLES: tuple[str, ...]`
- `connect(db_path) -> sqlite3.Connection` with `PRAGMA foreign_keys=ON`

**Steps:**

- [ ] **1.1 Write failing tests first**
  - Migration preserves pre-existing `nomina_asistencia_imports` row.
  - All six Banorte tables exist after ensure; second ensure is idempotent.
  - `PRAGMA foreign_keys` is ON for repository connections.
  - Insert with invalid `validation_status` / `record_status` raises IntegrityError.
  - Two `ACTIVO` rows with same `account_number` violate partial unique index.
  - Two `CONFLICTO_CRITICO` rows may share an account.
  - FK delete parent beneficiary referenced by alias is RESTRICTed.
  - Assert columns: no `is_active` on beneficiaries; no `original_file_blob`; no `total_decimal`/`amount_decimal`; has `record_status`, `replaces_id`, `duplicate_of_export_id`, cents fields.
  - Two `ACTIVO` rows with same `employee_number_effective` violate partial unique (dedicated test, not only account).
  - Two distinct children cannot share the same `replaces_id`.
  - Two active aliases with same `alias_normalizado` violate partial unique; reassignment requires explicit deactivate + audit.

- [ ] **1.2 Run**

```bash
py -3 -m pytest tests/test_banorte_migration.py tests/test_banorte_schema_integrity.py -v
```

Expected: FAIL (modules missing).

- [ ] **1.3 Minimal implementation** matching spec §4–§6 exactly.

- [ ] **1.4 Re-run tests** — PASS.

- [ ] **1.5 Acceptance**
  - Status split implemented.
  - SQL integrity proven without Python business helpers.
  - Diff touches only Banorte package + `db.py` hook + tests.

- [ ] **1.6 Parent review** — spec §4–§5 compliance; no layout reinterpretation.

- [ ] **1.7 Suggested commit** (after approval)

```bash
git add modules/nomina/banorte modules/nomina/db.py tests/test_banorte_migration.py tests/test_banorte_schema_integrity.py
git commit -m "feat(nomina-banorte): add schema with split statuses and SQL integrity"
```

#### GATE A — Schema (mandatory stop)

Parent must confirm: six tables, CHECKs, FKs, partial uniques, cents-only money columns, no Excel blob, `record_status` sole lifecycle. **Do not start Task 2 until GATE A passes.**

---

### Task 2: Money normalization

**Depends on:** GATE A  
**Files:**
- Create: `modules/nomina/banorte/money.py`
- Test: `tests/test_banorte_money.py`

**Interfaces:**
- `MoneyParseResult(ok, amount, ambiguous, error, rounded: bool)`
- `parse_money(raw: str) -> MoneyParseResult`
- `to_cents(amount: Decimal) -> int`
- `sum_amounts(amounts) -> Decimal`
- `format_pesos_from_cents(cents: int) -> str`

**Steps:**

- [ ] **2.1 Failing tests:** formats `2300`, `2,300.50`, `$2,300.00`, `2300,50`, `€2.300,50`, `2 300,50 €`; ROUND_HALF_UP trio; `rounded=True` warning path; zero/negative/empty/ambiguous/formula blocked; euro does not convert FX; `to_cents` after normalize only.

- [ ] **2.2** `py -3 -m pytest tests/test_banorte_money.py -v` → FAIL then implement → PASS.

- [ ] **2.3 Acceptance:** excess decimals accepted+rounded; no block-on->2-decimals; no float.

- [ ] **2.4 Suggested commit:** `feat(nomina-banorte): add Decimal money parser with ROUND_HALF_UP`

---

### Task 3: `.pag` layout builder + independent golden + docs

**Depends on:** Task 2 (for cents helpers; may use ints directly)  
**Files:**
- Create: `pag_layout.py`, `docs/banorte_pag_layout.md`
- Create: `tests/fixtures/banorte/README.md`, `synthetic_golden.pag`, `build_synthetic_golden.py` (must **not** import `pag_layout`)
- Create: `tests/test_banorte_pag_layout.py`
- Modify: `.gitignore`

**Interfaces:**
- `PagField`, `HEADER_FIELDS`, `DETAIL_FIELDS` (positions locked to spec §9.4)
- `build_pag_file(*, layout_date, consecutive, payments: Sequence[NormalizedPayment]) -> bytes`
- `build_filename(consecutive) -> str`
- `sha256_hex(data) -> str`
- `validate_layout_limits(...)` raising on overflow of 6/15/10/18 rules

**Steps:**

- [ ] **3.1** Author independent golden (hand or `build_synthetic_golden.py` copying field tables literally; **no** import of production builder). Commit fixture bytes.

- [ ] **3.2 Failing tests:** byte equality vs fixture; no trailing CRLF; CRLF between; ASCII; field sensitivity (fecha, consecutivo, count, total, emp, account, amount, padding, order); limit overflow; optional real hash skipif.

- [ ] **3.3** Implement `pag_layout.py` + exhaustive markdown doc (header+detail, fictional examples).

- [ ] **3.4** `py -3 -m pytest tests/test_banorte_pag_layout.py -v` → PASS.

- [ ] **3.5 Acceptance:** golden independence verified (test file asserts builder module ≠ golden builder module / or README + review); positions match spec.

- [ ] **3.6 Suggested commit:** `feat(nomina-banorte): deterministic .pag builder and independent golden`

#### GATE B — Layout engine (mandatory stop)

Parent reviews byte fixture vs spec tables position-by-position; confirms no trailing CRLF; confirms independent golden. **Do not start Task 4 until GATE B passes.**

---

### Task 4: Import services (ALTAS, FALLIDOS, Reporte)

**Depends on:** GATE A, Task 2  
**Files:**
- Create: `import_service.py`, `validators.py` (upload/id precision)
- Create: `tests/fixtures/banorte/synthetic_altas.xlsx`, `synthetic_reporte.xlsx`
- Extend: `repository.py`
- Test: `tests/test_banorte_import.py`

**Interfaces:**
- `import_nomina_banorte_xlsx(db_path, file_bytes, filename, user, *, reimport_confirmed=False) -> ImportBatchResult`
- `import_reporte_detallado_xlsx(...) -> ImportBatchResult`
- `is_banorte_employee_substituted_comment(text) -> bool`
- `extract_identifier_cell(...)` safe rules

**Steps:**

- [ ] **4.1** Build synthetic workbooks (fictional PII only) covering EXITOSO, FALLIDOS 21+1 pattern scaled down, manuals, duplicates, special comment, precision-loss account row.

- [ ] **4.2 Failing tests:** cases 1–10, 16; FALLIDOS counter split; idempotent reimport (same SHA no confirm = zero mutations; same SHA with confirm = audit batch + `REIMPORT_NO_CHANGE` for identical rows; material change creates version only); identity matrix (CURP+new account versioning; account conflict; name-only no merge); manual→validated new row+`replaces_id`; fuzzy never inactivates; transaction rollback; EXITOSO preserved on inactivated prior.

- [ ] **4.3** Implement transactional import.

- [ ] **4.4** `py -3 -m pytest tests/test_banorte_import.py -v` → PASS.

- [ ] **4.5 Acceptance:** no Excel BLOB; TEXT identifiers; special comment strict; matrix honored.

- [ ] **4.6 Suggested commit:** `feat(nomina-banorte): import ALTAS and Reporte with row audit`

#### GATE C — Import (mandatory stop)

Parent verifies counters, FALLIDOS exclusion, versioning via `replaces_id`, conflict non-ACTIVO. **Do not start Task 5 until GATE C passes.**

---

### Task 5: Paste, matching, aliases

**Depends on:** GATE C  
**Files:**
- Create: `paste_service.py`, `matching_service.py`
- Test: `tests/test_banorte_matching.py`

**Interfaces:**
- `parse_paste_lists(names_text, amounts_text) -> PasteParseResult`
- `match_name(db_path, raw_name) -> MatchResult` (only `record_status=ACTIVO`)
- `save_alias`, `deactivate_alias`
- Editor helpers: add/remove/reorder row structures (pure functions OK)

**Steps:**

- [ ] **5.1 Failing tests:** exact, alias, fuzzy-no-auto, ambiguous, empty lines, headers, unequal lengths, edit/reorder, fuzzy does not change DB lifecycle; alias to `INACTIVO_REEMPLAZADO` never auto-assigns inactive (recommend unique ACTIVO successor with audit, or block if ambiguous); never export inactive version bank fields.

- [ ] **5.2** Implement → `py -3 -m pytest tests/test_banorte_matching.py -v` PASS.

- [ ] **5.3 Suggested commit:** `feat(nomina-banorte): paste capture and matching`

---

### Task 6: Export orchestration + immutable BLOB history + backend authority

**Depends on:** GATE B, GATE C, Task 5  
**Files:**
- Create: `export_service.py`
- Extend: `repository.py`, `validators.py`
- Test: `tests/test_banorte_export_history.py`

**Interfaces:**
- `generate_export(db_path, user, draft_rows, *, consecutive, layout_date, confirm_duplicate_consecutive=False, confirm_manuals=False, confirm_date_override=False) -> ExportResult`
- `get_export_blob(db_path, export_id) -> tuple[filename, bytes, sha256]`
- Draft row input may include `beneficiary_id` + amount text + decisions; server reloads beneficiary fields from DB.

**Steps:**

- [ ] **6.1 Failing tests:** generate; SHA-256; redownload identity; duplicate consecutive sets flags on **new** row only / prior stays `GENERATED`; forged client account ignored/rejected; beneficiary became inactive/replaced/conflict/bank-data-changed since preview → block and return to editor (no silent successor swap); manual confirm gate; rollback leaves no partial export; layout limit block; cents total authority.

- [ ] **6.2** Implement single transaction: export + items + blob.

- [ ] **6.3** `py -3 -m pytest tests/test_banorte_export_history.py -v` PASS.

- [ ] **6.4 Suggested commit:** `feat(nomina-banorte): export with immutable BLOB and server-side authority`

#### GATE D — Export BLOB (mandatory stop)

Parent verifies byte immutability, redownload, backend authority, duplicate consecutive semantics. **Do not start Task 7 until GATE D passes.**

---

### Task 7: CSRF, routes, permissions, UI, cache headers

**Depends on:** GATE D  
**Files:**
- Create: `csrf.py`, `routes.py`, templates, `static/nomina/exportaciones_banorte.css`
- Modify: `blueprint.py`, `dashboard.html`
- Test: `tests/test_banorte_permissions.py`, `tests/test_banorte_csrf.py`

**Routes (minimum):**
- GET workspace / historial / beneficiarios / download
- POST import/altas, import/reporte, paste, match, manual beneficiary, aliases, export/preview, export/generate
- No state-changing GET

**Steps:**

- [ ] **7.1 Failing tests:** admin allow; nomina allow; coordinador/usuario 403; download re-checks role; POST without CSRF → 400/403; GET cannot create export; `Cache-Control: private, no-store` on bank pages/download; response is `.pag` bytes unchanged.

- [ ] **7.2** Implement routes/UI; CSRF: session-bound crypto-random token using `SECRET_KEY`, `hmac.compare_digest`, forms+JSON, never log token; in-memory editor with unload warning; AJAX for manual/match without full reload losing paste list; no localStorage/sessionStorage of bank data; no draft table.

- [ ] **7.3** `py -3 -m pytest tests/test_banorte_permissions.py tests/test_banorte_csrf.py -v` PASS.

- [ ] **7.4 Suggested commit:** `feat(nomina-banorte): role-gated UI with CSRF and no-store cache`

#### GATE E — Permissions / CSRF (mandatory stop)

Parent verifies decorator coverage on all Banorte routes and CSRF on mutations. **Do not start Task 8 until GATE E passes.**

---

### Task 8: Full suite, docs polish, delivery evidence

**Depends on:** GATE E  
**Files:** fill any matrix gaps; polish `docs/banorte_pag_layout.md`; no real PII.

**Steps:**

- [ ] **8.1**

```bash
py -3 -m pytest tests/test_banorte_*.py -v
```

All required PASS; optional real-hash skipped if absent.

- [ ] **8.2** Diff review: no protected modules; no real fixtures; branch is `feature/banorte-exportaciones`.

- [ ] **8.3** Delivery report (diagnosis, files, migration, layout, import rules, special comment, tests, golden, untouched evidence, risks, manual checklist, `git diff --stat`, `git status --short`).

- [ ] **8.4 Suggested commit if docs-only leftovers:** `docs(nomina-banorte): finalize layout notes and QA evidence`

#### GATE F — Integral suite (mandatory stop before claiming done)

Parent runs full Banorte suite + protected-surface diff check + `proclean-change-verification` when claiming completion. No push/merge/deploy.

---

## Suggested commit sequence (on feature branch only)

1. Schema/integrity  
2. Money  
3. Pag layout + independent golden + gitignore + layout doc  
4. Import + audit  
5. Paste/matching  
6. Export BLOB + server authority  
7. UI/permissions/CSRF  
8. Docs/QA wrap-up  

Do not combine schema + pag + import + UI in one commit.

---

## Subagent / parent operating rules

1. One subagent per Task 1–8.  
2. Parent supplies locked schema/layout excerpts; subagent may not change field positions or status enums.  
3. After each task: parent spec compliance review + technical/test review before next task.  
4. Gates A–F are hard stops.  
5. Commits only after parent (and user process) accepts that task’s tests and diff.

---

## Plan self-review vs corrected spec

| Spec requirement | Task / Gate |
|------------------|-------------|
| Split validation/record status | T1 / A |
| Identity matrix | T4 / C |
| Manual→validated versioning | T4 / C |
| FK/CHECK/partial unique + pragma | T1 / A |
| Money ROUND_HALF_UP accept extras | T2 |
| Backend authority | T6 / D |
| CSRF, no-store, no browser storage | T7 / E |
| Excel precision rules | T4 |
| No Excel BLOB | T1/T4 |
| Cents canonical | T1/T6 |
| Duplicate consecutive metadata | T6 / D |
| No snapshot hash column | T1 |
| Independent golden | T3 / B |
| Layout limits | T3/T6 |
| Permissions | T7 / E |
| Full matrix | T8 / F |
| No calc_nomina | Global |

---

## Execution handoff (only after “spec aprobado y plan aprobado”)

1. Parent creates `feature/banorte-exportaciones`.  
2. Subagent-driven Tasks 1→8 with Gates A–F.  
3. Commits per task on that branch only.  
4. No push/merge/deploy without explicit authorization.
