# Design Spec: Nóminas > Exportaciones > Banorte (Fase 1)

**Date:** 2026-07-13  
**Status:** Approved for implementation (2026-07-13) + binding amendments below  
**Risk level:** L3 Critical  
**Approach:** A — submódulo dentro de Nóminas (approved)

---

## 1. Outcome and exclusions

### 1.1 Outcome

Deliver a Banorte payroll export workspace under **Nóminas > Exportaciones > Banorte** that:

1. Maintains an audited master of Banorte beneficiaries.
2. Imports `NOMINA BANORTE.xlsx` (sheet `ALTAS`) and Banorte “Reporte Detallado” workbooks.
3. Captures payments via paste of two positional lists (names + amounts).
4. Matches beneficiaries with controlled confirmation (exact / alias / fuzzy recommendation / ambiguous / none).
5. Allows controlled manual beneficiary creation (`MANUAL_PENDIENTE_VALIDACION`).
6. Generates exact Banorte `.pag` bytes from a central layout specification.
7. Stores immutable export history (header + per-payment snapshot + exact `.pag` BLOB).
8. Restricts access to `admin` and `nomina` in UI and backend.
9. Exposes a reusable normalized payment interface for a **future** payroll-calc integration (not wired in this phase).

### 1.2 Explicit exclusions (must remain untouched)

- `calc_nomina` and any direct connection to payroll calculation runs
- Finiquitos, INFONAVIT, Facturación, Headcount, Vacaciones
- DOCX/PDF/PNG templates and LibreOffice
- Other banks, SPEI, interbank layouts, portal upload, auto-dispersion
- Global CSS redesigns / menu duplication
- Deploy, git commit, git push (unless explicitly authorized later)
- Real PII/bank data in Git, fixtures, docs examples, logs, URLs, or error messages

---

## 2. Repository integration

### 2.1 Navigation and permissions

| Item | Decision |
|------|----------|
| Location | `Nóminas` dashboard card **Exportaciones** → Banorte |
| URL prefix | `/nomina/exportaciones/banorte` (and related subpaths) |
| Blueprint | Existing `nomina_bp` only — **no new blueprint** |
| `app.py` | Do not modify unless a real registration gap is proven (e.g. CSRF helper registration that cannot live on the blueprint); prefer Banorte-local wiring |
| Roles | `admin`, `nomina` only (`NOMINA_DASHBOARD_ROLES`) |
| Unauthorized | Same as rest of Nóminas: unauthenticated → login redirect; wrong role → `403` |
| UI hiding | Insufficient alone; every route must enforce the decorator/guard |
| Historical download | Re-checks role on every download request |

### 2.2 Package layout (approved)

```text
modules/nomina/banorte/
├── __init__.py
├── models.py
├── repository.py
├── import_service.py
├── matching_service.py
├── money.py
├── pag_layout.py
├── validators.py
├── paste_service.py
├── export_service.py
├── csrf.py                 # Banorte CSRF helpers (module-scoped)
├── routes.py
└── schema.py

templates/nomina/
├── exportaciones_index.html          # optional hub
├── exportaciones_banorte.html
├── exportaciones_banorte_editor.html
└── exportaciones_banorte_historial.html

static/nomina/
└── exportaciones_banorte.css

docs/
├── banorte_pag_layout.md
└── superpowers/specs/2026-07-13-banorte-exportaciones-design.md

tests/
├── test_banorte_*.py
└── fixtures/banorte/                 # SYNTHETIC only
    ├── synthetic_altas.xlsx
    ├── synthetic_reporte.xlsx
    ├── synthetic_golden.pag          # hand-authored / independent of builder
    ├── build_synthetic_golden.py     # optional independent generator; not imported by pag_layout
    └── README.md
```

### 2.3 Wiring without bloating `blueprint.py` / `db.py`

- `routes.py` → `register_banorte_routes(bp)`.
- `blueprint.py` calls that once (minimal).
- `schema.py` owns Banorte DDL; `db.py` calls `ensure_banorte_tables(conn)` only.
- No Banorte business logic in `blueprint.py` or `db.py`.

### 2.4 Files expected to change

**Create:** Banorte package, templates, CSS, tests, synthetic fixtures, `docs/banorte_pag_layout.md`.

**Modify (minimal):** `modules/nomina/blueprint.py`, `modules/nomina/db.py`, `templates/nomina/dashboard.html`, `.gitignore`.

**Do not modify:** calc, finiquitos, INFONAVIT, facturación, headcount, vacaciones, document generators, LibreOffice, Railway deploy config (escalate before any exception).

---

## 3. Storage decision (canonical)

### 3.1 `.pag` bytes: SQLite BLOB is the source of truth

| Field | Purpose |
|-------|---------|
| `filename` | Exact name e.g. `NI6705903.pag` |
| `file_sha256` | Hex SHA-256 of exact bytes |
| `file_size` | Byte length |
| `file_blob` | Exact immutable bytes |

**Fase 1:** no `DATA_DIR` write; no Excel original BLOB.

### 3.2 What is stored on import (no Excel BLOB)

For each import batch store only:

- file name, size, SHA-256;
- detected type, counters, `summary_json`;
- per-row audit in `nomina_banorte_import_rows` (fields needed for decisions — not card/CLABE/RFC dumps beyond what audit requires).

Do **not** persist `original_file_blob`. Do not keep tarjeta, CLABE, or RFC in the beneficiary master. CURP is optional and allowed when present because it supports identity matching; never log it.

### 3.3 Secrets / PII hygiene

- Never log full account numbers, CLABE, card numbers, CURP, or RFC.
- Never put bank identifiers in URLs or GET query strings.
- Authorized UI may show full account numbers.
- Error messages: opaque ids / positions only.
- No accounts, CURP, amounts, or drafts in `localStorage` / `sessionStorage`.
- Responses that expose bank data: `Cache-Control: private, no-store`.

### 3.4 Real reference files

Outside repo / gitignored:

```text
private_fixtures/banorte/
NOMINA BANORTE.xlsx
Reporte_Detallado_*.xlsx
NI67059*.pag
```

---

## 4. Domain model

### 4.1 Separated statuses (mandatory)

**Banking validation** (`validation_status`) — does not change when a record is inactivated:

| Code | Meaning |
|------|---------|
| `IMPORTADO_EXITOSO` | Validated by Banorte EXITOSO |
| `MANUAL_PENDIENTE_VALIDACION` | Manual create or ALTAS complete row without status |

**Lifecycle** (`record_status`) — **sole** authority for active use:

| Code | Meaning |
|------|---------|
| `ACTIVO` | Eligible for matching/export (if otherwise valid) |
| `INACTIVO_REEMPLAZADO` | Superseded; retained forever |
| `CONFLICTO_CRITICO` | Stored for audit; **not** usable as a normal active beneficiary |

Rules:

- There is **no** separate `is_active` column. `record_status` is the only lifecycle field.
- `IMPORTADO_EXITOSO` + `INACTIVO_REEMPLAZADO` is valid and expected after replacement.
- `CONFLICTO_CRITICO` rows are never selected automatically for payment.
- Only `record_status = ACTIVO` participates in auto matching and export eligibility.

### 4.2 Source kinds

- `ALTAS_NOMINA_BANORTE`
- `REPORTE_DETALLADO`
- `ALTA_MANUAL`

Conversion manual→validated is expressed by creating a **new** beneficiary row (`IMPORTADO_EXITOSO`, `ACTIVO`) that `replaces_id` points at the prior manual row (see §4.4 / §7.6). No separate source kind required.

### 4.3 Identity and replacement matrix (deterministic)

| Situation | Action |
|-----------|--------|
| Same CURP + new account number | Treat as possible update of same person: create new version `ACTIVO`; prior → `INACTIVO_REEMPLAZADO` via `replaces_id` |
| Same effective employee number + same CURP (or other confirmed identity) | New version; prior inactivated as above |
| Same account + clearly different person (name/CURP conflict without enough evidence of update) | Insert/keep as `CONFLICTO_CRITICO`; do **not** auto-activate; do **not** inactivate the existing good record automatically |
| Same name only | Never merge; never auto-inactivate |
| Duplicate requested employee number **with** official Banorte substitution comment | Apply §7.4 (effective = account); not a conflict |
| Insufficient identifiers | Manual review; do not auto-activate conflicting data |
| Fuzzy/approximate name match | May recommend in paste UI only; **never** inactivates or replaces bank master records |

Name alone is never a unique key. Fuzzy matching never drives master inactivation.

### 4.4 Versioning direction (canonical)

- Canonical FK on the **newer** row: `replaces_id` → previous beneficiary `id`.
- Do **not** store `replaced_by_id` (avoids bidirectional contradiction).
- Inverse query: `SELECT * FROM ... WHERE replaces_id = :old_id`.
- Physical deletes of history rows are forbidden.

### 4.5 Manual → validated

When a later EXITOSO import links to a manual beneficiary:

1. Create a **new** validated row (`validation_status=IMPORTADO_EXITOSO`, `record_status=ACTIVO`).
2. Set `replaces_id` to the manual row’s id.
3. Set the manual row to `record_status=INACTIVO_REEMPLAZADO` (keep `validation_status=MANUAL_PENDIENTE_VALIDACION` historically true for that version).
4. Audit import batch + import row that produced the validated version.
5. Never destructively overwrite the manual row’s identifying fields in place.

Link order for finding the manual predecessor: employee number → CURP → account → name as **suggestion only** (name never auto-links).

### 4.6 Future-facing payment DTO (not wired to calc)

```python
@dataclass(frozen=True)
class NormalizedPayment:
    beneficiary_id: int | None
    employee_number: str          # effective, TEXT, preserve leading zeros
    account_number: str           # Banorte account (not card/CLABE)
    amount: Decimal               # exactly 2 decimal places after ROUND_HALF_UP
    source_reference: str | None
```

Payroll calc must not call this in Fase 1.

---

## 5. Exact SQLite schema

### 5.0 Integrity prerequisites

- Every Banorte connection sets `PRAGMA foreign_keys = ON` (asserted in repository and in tests).
- Explicit `FOREIGN KEY (... ) REFERENCES ... ON DELETE RESTRICT`.
- `CHECK` constraints for enums, boolean-like 0/1 flags, consecutive pattern, and non-negative money fields.
- Partial unique indexes (SQLite) so that among **non-conflict active** rows there cannot be two with the same effective employee number or the same account:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS uq_banorte_active_emp
  ON nomina_banorte_beneficiaries(employee_number_effective)
  WHERE record_status = 'ACTIVO';

CREATE UNIQUE INDEX IF NOT EXISTS uq_banorte_active_account
  ON nomina_banorte_beneficiaries(account_number)
  WHERE record_status = 'ACTIVO';

-- At most one active replacement child per predecessor version
CREATE UNIQUE INDEX IF NOT EXISTS uq_banorte_replaces_id
  ON nomina_banorte_beneficiaries(replaces_id)
  WHERE replaces_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_banorte_active_alias_norm
  ON nomina_banorte_aliases(alias_normalizado)
  WHERE is_active = 1;
```

`CONFLICTO_CRITICO` and `INACTIVO_REEMPLAZADO` are excluded from active uniques so conflicts and history can be stored (including duplicate emp/account among non-ACTIVO rows). Integrity must be demonstrated by tests that violate constraints at SQL level (not only via Python helpers), including **both** duplicate active employee number and duplicate active account.

`uq_banorte_replaces_id` prevents two different replacement branches from claiming the same predecessor (`replaces_id`). Historical FKs use `ON DELETE RESTRICT`.

Active alias uniqueness: two active aliases may not share the same `alias_normalizado` pointing at different beneficiaries (enforced by partial unique on `alias_normalizado` where `is_active=1`). To reassign an alias: expressly deactivate the prior row (audit who/when), then insert/activate the new mapping.

### 5.1 `nomina_banorte_beneficiaries`

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PRIMARY KEY | |
| `nombre_original` | TEXT NOT NULL | |
| `nombre_normalizado` | TEXT NOT NULL | indexed |
| `curp` | TEXT NULL | TEXT; uppercase when present; indexed |
| `employee_number_requested` | TEXT NULL | TEXT identifiers |
| `employee_number_effective` | TEXT NOT NULL | TEXT; used in `.pag` |
| `account_number` | TEXT NOT NULL | TEXT; Banorte account only |
| `source_kind` | TEXT NOT NULL | CHECK against §4.2 |
| `validation_status` | TEXT NOT NULL | CHECK: `IMPORTADO_EXITOSO` \| `MANUAL_PENDIENTE_VALIDACION` |
| `record_status` | TEXT NOT NULL | CHECK: `ACTIVO` \| `INACTIVO_REEMPLAZADO` \| `CONFLICTO_CRITICO` |
| `banorte_employee_substituted` | INTEGER NOT NULL DEFAULT 0 | CHECK IN (0,1) |
| `banorte_comment` | TEXT NULL | |
| `source_filename` | TEXT NULL | |
| `source_sheet` | TEXT NULL | |
| `source_row` | INTEGER NULL | |
| `report_date` | TEXT NULL | ISO date if known |
| `imported_at` | TEXT NOT NULL | America/Monterrey ISO |
| `imported_by` | TEXT NOT NULL | |
| `replaces_id` | INTEGER NULL | FK → `nomina_banorte_beneficiaries(id)` ON DELETE RESTRICT |
| `created_at` | TEXT NOT NULL | |
| `updated_at` | TEXT NOT NULL | |

Additional indexes: `(nombre_normalizado)`, `(employee_number_effective)`, `(account_number)`, `(curp)`, `(record_status)`, `(validation_status)`.

### 5.2 `nomina_banorte_aliases`

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PRIMARY KEY | |
| `alias_original` | TEXT NOT NULL | |
| `alias_normalizado` | TEXT NOT NULL | indexed |
| `beneficiary_id` | INTEGER NOT NULL | FK ON DELETE RESTRICT |
| `is_active` | INTEGER NOT NULL DEFAULT 1 | CHECK IN (0,1); alias lifecycle only |
| `created_by` | TEXT NOT NULL | |
| `created_at` | TEXT NOT NULL | |
| `deactivated_by` | TEXT NULL | |
| `deactivated_at` | TEXT NULL | |

Deactivate by flag; never delete.

### 5.3 `nomina_banorte_import_batches`

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PRIMARY KEY | |
| `file_name` | TEXT NOT NULL | |
| `file_sha256` | TEXT NOT NULL | indexed; duplicate → warn + require confirm |
| `file_size` | INTEGER NOT NULL | CHECK >= 0 |
| `detected_type` | TEXT NOT NULL | CHECK type enum |
| `imported_by` | TEXT NOT NULL | |
| `imported_at` | TEXT NOT NULL | |
| `rows_processed` | INTEGER NOT NULL | |
| `count_exitosos` | INTEGER NOT NULL | |
| `count_manuales` | INTEGER NOT NULL | |
| `count_fallidos_estatus` | INTEGER NOT NULL | |
| `count_fallidos_hoja_sin_estatus` | INTEGER NOT NULL | |
| `count_excluidos_hoja_fallidos_total` | INTEGER NOT NULL | |
| `count_duplicados_reemplazados` | INTEGER NOT NULL | |
| `count_conflictos` | INTEGER NOT NULL | |
| `count_omitidos` | INTEGER NOT NULL | |
| `summary_json` | TEXT NOT NULL | |
| `reimport_confirmed` | INTEGER NOT NULL DEFAULT 0 | CHECK IN (0,1) |

**No `original_file_blob` column.**

### 5.4 `nomina_banorte_import_rows`

Immutable per-row audit (including exclusions).

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PRIMARY KEY | |
| `batch_id` | INTEGER NOT NULL | FK ON DELETE RESTRICT |
| `sheet_name` | TEXT NOT NULL | |
| `row_number` | INTEGER NOT NULL | |
| `decision` | TEXT NOT NULL | enumerated decisions |
| `reason` | TEXT NOT NULL | |
| `nombre` | TEXT NULL | |
| `curp` | TEXT NULL | |
| `employee_number_requested` | TEXT NULL | |
| `employee_number_effective` | TEXT NULL | |
| `account_number` | TEXT NULL | |
| `estatus_raw` | TEXT NULL | |
| `comentarios_raw` | TEXT NULL | |
| `beneficiary_id` | INTEGER NULL | FK ON DELETE RESTRICT |
| `payload_json` | TEXT NULL | extra non-PII-heavy metadata; do not dump card/CLABE/RFC |

Fase 1 uses this normalized table (not a sole JSON blob alternative).

### 5.5 `nomina_banorte_exports`

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PRIMARY KEY | |
| `created_by` | TEXT NOT NULL | |
| `created_at` | TEXT NOT NULL | |
| `timezone` | TEXT NOT NULL | `America/Monterrey` |
| `layout_date` | TEXT NOT NULL | `YYYYMMDD` |
| `layout_date_auto` | TEXT NOT NULL | |
| `date_override_confirmed` | INTEGER NOT NULL DEFAULT 0 | CHECK IN (0,1) |
| `consecutive` | TEXT NOT NULL | CHECK `length=2` and digits `01`–`99` |
| `filename` | TEXT NOT NULL | `NI67059{consecutive}.pag` |
| `payment_count` | INTEGER NOT NULL | CHECK >= 0; must fit 6-digit layout field |
| `total_cents` | INTEGER NOT NULL | **canonical money**; CHECK >= 0; must fit 15 digits |
| `capture_origin` | TEXT NOT NULL | |
| `incidents_json` | TEXT NOT NULL | |
| `manual_row_count` | INTEGER NOT NULL | |
| `aliases_used_json` | TEXT NOT NULL | |
| `recommendations_accepted_json` | TEXT NOT NULL | |
| `warnings_ignored_json` | TEXT NOT NULL | |
| `duplicate_consecutive_confirmed` | INTEGER NOT NULL DEFAULT 0 | CHECK IN (0,1) |
| `duplicate_of_export_id` | INTEGER NULL | FK → exports(id) ON DELETE RESTRICT; set when user confirms reuse of same date+consecutive |
| `file_sha256` | TEXT NOT NULL | |
| `file_size` | INTEGER NOT NULL | |
| `file_blob` | BLOB NOT NULL | canonical `.pag` |
| `status` | TEXT NOT NULL | CHECK: `GENERATED` only for Fase 1 normal rows |

Notes:

- Prior exports always remain `GENERATED` and immutable.
- Reusing consecutive does **not** rewrite prior `status`.
- No `total_decimal` column; UI formats pesos from `total_cents`.
- No `beneficiary_snapshot_sha256`; `export_items` is the complete snapshot.

Indexes: `(layout_date, consecutive)`, `(created_at)`, `(filename)`, `(file_sha256)`.

### 5.6 `nomina_banorte_export_items`

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PRIMARY KEY | |
| `export_id` | INTEGER NOT NULL | FK ON DELETE RESTRICT |
| `position` | INTEGER NOT NULL | |
| `nombre_recibido` | TEXT NOT NULL | |
| `beneficiary_id` | INTEGER NULL | FK ON DELETE RESTRICT |
| `employee_number_effective` | TEXT NOT NULL | snapshot TEXT |
| `account_number` | TEXT NOT NULL | snapshot TEXT |
| `curp` | TEXT NULL | snapshot |
| `amount_cents` | INTEGER NOT NULL | **canonical**; CHECK > 0 |
| `match_kind` | TEXT NOT NULL | |
| `alias_id` | INTEGER NULL | FK ON DELETE RESTRICT |
| `validation_status` | TEXT NOT NULL | snapshot |
| `record_status` | TEXT NOT NULL | snapshot |
| `is_manual_beneficiary` | INTEGER NOT NULL | CHECK IN (0,1) |
| `warnings_json` | TEXT NOT NULL | |
| `user_decision_json` | TEXT NOT NULL | |

Unique `(export_id, position)`. Display amounts derive from `amount_cents` only.

---

## 6. Migration strategy

1. `ensure_nomina_tables` → `ensure_banorte_tables`.
2. `CREATE TABLE IF NOT EXISTS` + CHECKs + FKs + partial unique indexes.
3. Additive `_migrate_banorte_schema` via `PRAGMA table_info` only.
4. Repository always enables `PRAGMA foreign_keys = ON`.
5. Test on DB preloaded with unrelated Nóminas data; prove preservation + Banorte tables + FK enforcement.

No DROP of existing app tables. No empty-DB assumption.

---

## 7. Import rules

### 7.1 Shared Excel safety and identifier precision

- Validate `.xlsx`, size, openability (openpyxl; no macros).
- Header detection by normalized names.
- Escape for HTML; no raw HTML from cells; reject path traversal.
- SHA-256 reimport behavior (idempotent):
  - Same SHA-256 **without** confirmation: **no mutation** (reject/warn only).
  - Same SHA-256 **with** confirmation: may create a new audit batch recording the decision, but must **not** create duplicate beneficiary versions when row content is identical; unchanged rows use decision `REIMPORT_NO_CHANGE` (or equivalent); new versions only on material change per identity matrix; never mass-inactivate/recreate the whole master unnecessarily.

**Identifier extraction (safe):**

| Cell situation | Rule |
|----------------|------|
| Text cell | Keep digit string exactly |
| Numeric integer within safe precision | Convert without scientific notation; if `number_format` has a zero mask, apply that width **only when** it does not invent digits beyond the numeric value’s exact integer representation |
| Possible precision loss (esp. >15 significant digits) | Do **not** guess; mark row for manual review; do not import as trusted account/employee |
| Altered/uncertain account | Never silently accept |

### 7.2 `NOMINA BANORTE.xlsx`

Sheets: `ALTAS` (import), `FALLIDOS` (exclude entirely), `LAYOUT` (not imported).

#### ALTAS

| Condition | Action |
|-----------|--------|
| EXITOSO | Insert `validation_status=IMPORTADO_EXITOSO`, `record_status=ACTIVO` (unless conflict matrix says otherwise) |
| FALLIDO | Exclude; `count_fallidos_estatus` |
| Empty status + complete | `MANUAL_PENDIENTE_VALIDACION` + `ACTIVO` |
| Empty incomplete / empty / headers / subtotals | Omit |
| Account for `.pag` | Número de cuenta only |
| Duplicates same file | Lower row wins per matrix; prior → `INACTIVO_REEMPLAZADO` |

Sample expectations: 621 EXITOSO; 113 manual complete; 3 incomplete; 0 FALLIDO in ALTAS.

#### FALLIDOS sheet

| Bucket | Sample count |
|--------|----------------|
| Explicit FALLIDO | 21 |
| Empty status on FALLIDOS | 1 |
| Total excluded | 22 |

Never collapse audit to “22 FALLIDO”.

### 7.3 Reporte Detallado

Only EXITOSO into master; other statuses counted in audit only. Persist comment, requested employee, account, CURP, name, alta date, row, filename (as TEXT fields / audit).

### 7.4 Special Banorte comment

Normalize: uppercase, strip accents, collapse spaces, strip punctuation. Match official phrase with high confidence only (not broad fuzzy).

When matched (ALTAS or Reporte):

1. Keep `employee_number_requested` as read from cell (safe extraction).
2. `employee_number_effective` = `account_number`.
3. `banorte_employee_substituted = 1`.
4. Not a conflict.
5. Never on manual altas without the comment.

### 7.5 Currency / conflicts

Apply matrix §4.3. Never hard-delete. Fuzzy name never inactivates master rows.

### 7.6 Manual create

Duplicate **active** effective employee number: block; show existing; allow select/correct. No Banorte substitution without official comment.

---

## 8. Paste capture, matching, editor

### 8.1 Paste lists

Position-only pairing; preserve empties; warn on length mismatch; allow editor; block `.pag` until complete. Detect headers; show stripped headers; don’t silently drop ambiguous person-like lines.

### 8.2 Money

- `Decimal` only; never `float`.
- Accept excess fractional digits; apply `ROUND_HALF_UP` to 2 places:

  - `2300.41123210` → `2300.41`
  - `2300.66231130` → `2300.66`
  - `2300.66631130` → `2300.67`

- Informational warning when rounding occurred; **do not block** solely because input had >2 decimals.
- Currency symbols (`$`, `€`, etc.): strip for parsing only; **no FX conversion**; result is the nominal Banorte layout amount.
- Block: zero, negative, empty, unparseable/ambiguous, NaN/inf, formulas/executable text.
- Totals = sum of final 2-decimal amounts; cents only after normalize.
- External Excel totals are never authority.

### 8.3 Matching levels

1. Exact → auto  
2. Alias → auto (labeled) only when target beneficiary is `ACTIVO`  
3. Unique fuzzy → recommend; explicit accept  
4. Ambiguous → alternatives; no auto  
5. None → manual create  

Fuzzy never updates beneficiary master lifecycle.

**Alias pointing at inactive beneficiary:** never auto-assign the inactive row. If `replaces_id` chain leads unequivocally to exactly one `ACTIVO` successor, recommend that successor and audit that the alias originally targeted a prior version. If the chain is ambiguous, incomplete, or ends in conflict, block auto-assignment and require user choice. Never export account/employee numbers from an inactive version.

### 8.4 Editor

Editable table as previously specified. Manual beneficiaries: visible warning + confirm before generate.

### 8.5 Backend authority on export (mandatory)

Client may send: row positions, `beneficiary_id` choices, alias decisions, amounts as edited text, consecutive, date override flags.

Server **must** before building bytes:

1. Re-load each selected beneficiary from SQLite by id.
2. Verify `record_status == ACTIVO`.
3. Take `employee_number_effective`, `account_number`, `validation_status`, `record_status` from DB — not from client.
4. Re-parse/validate amounts with `money.py`.
5. Recompute `total_cents` and `payment_count` server-side.
6. Reject if client-supplied account/employee/status disagrees with DB (do not “prefer” client).
7. If since preview the beneficiary is no longer `ACTIVO`, was replaced, became `CONFLICTO_CRITICO`, or its bank fields changed: **block generation**, return the row to the editor for review. Do **not** silently switch to a successor or auto-substitute account; user must reconfirm the current selection.

### 8.6 Editor session state (Fase 1)

- No draft table and no bank data in `localStorage` / `sessionStorage`.
- Keep the editor in page memory for the browser session.
- Manual creates and match resolutions should work via controlled requests without a full reload that drops the paste list.
- Warn on unload/reload when there are unsaved/unexported changes.
- Backend remains authority and rebuilds all bank fields before generate.
- Do not add a seventh draft table unless a technical necessity is discovered; escalate before adding it.

---

## 9. `.pag` generation

### 9.1 Filename / consecutive / date

- `NI67059` + `01`–`99` + `.pag`.
- Duplicate `(layout_date, consecutive)`: warn; require confirm; set `duplicate_consecutive_confirmed=1` and `duplicate_of_export_id` on the **new** row; prior row stays `GENERATED` untouched.
- Default date: `America/Monterrey`; override needs confirm; store auto + used.

### 9.2 Layout engine

Central spec in `pag_layout.py` + `docs/banorte_pag_layout.md`. Deterministic bytes; CRLF between lines; **no trailing CRLF**.

### 9.3 Structural layout limits (hard)

| Field | Max |
|-------|-----|
| Detail count | 6 digits (`000000`–`999999`) |
| Total cents | 15 digits |
| Employee number (layout) | 10 chars zero-padded |
| Account (layout) | 18 chars zero-padded |
| Line | exactly 165 ASCII |
| Consecutive | 2 digits `01`–`99` |

Exceeding these blocks generation with a clear error. “No artificial Excel-row cap” ≠ unbounded layout.

### 9.4 Verified layout (reference `NI6705903.pag`)

| Fact | Value |
|------|-------|
| Size | 19203 |
| Lines | 115 (1H+114D) |
| Width | 165 ASCII |
| Separators | CRLF between; **no** final CRLF |
| Date / consec / count / total | `20260710` / `03` / `000114` / `000000029863880` |
| Constants | `NE`, `67059`, `072`, `01`, mov `0`, IVA `00000000`, detail acción space |

#### Header (0-based)

| Field | Start | End | Len | Content |
|-------|-------|-----|-----|---------|
| tipo_registro | 0 | 1 | 1 | `H` |
| clave_servicio | 1 | 3 | 2 | `NE` |
| emisora | 3 | 8 | 5 | `67059` |
| fecha | 8 | 16 | 8 | `YYYYMMDD` |
| consecutivo | 16 | 18 | 2 | `01`–`99` |
| num_registros | 18 | 24 | 6 | zero-pad |
| importe_total | 24 | 39 | 15 | cents |
| num_registros_alt | 39 | 45 | 6 | `000000` |
| importe_alt | 45 | 60 | 15 | zeros |
| num_bajas | 60 | 66 | 6 | zeros |
| importe_bajas | 66 | 81 | 15 | zeros |
| num_verificacion | 81 | 87 | 6 | zeros |
| accion | 87 | 88 | 1 | `0` |
| filler_spaces | 88 | 165 | 77 | spaces |

#### Detail (0-based)

| Field | Start | End | Len | Content |
|-------|-------|-----|-----|---------|
| tipo_registro | 0 | 1 | 1 | `D` |
| fecha | 1 | 9 | 8 | `YYYYMMDD` |
| num_empleado | 9 | 19 | 10 | effective, zero-pad |
| referencia_servicio | 19 | 59 | 40 | spaces |
| campo_secundario | 59 | 99 | 40 | spaces |
| importe | 99 | 114 | 15 | cents |
| banco_receptor | 114 | 117 | 3 | `072` |
| tipo_cuenta | 117 | 119 | 2 | `01` |
| numero_cuenta | 119 | 137 | 18 | zero-pad |
| tipo_movimiento | 137 | 138 | 1 | `0` |
| accion | 138 | 139 | 1 | space |
| iva | 139 | 147 | 8 | `00000000` |
| filler_spaces | 147 | 165 | 18 | spaces |

Validate each field width/charset before emit.

### 9.5 HTTP download

Attachment `.pag`; exact bytes; re-authorize role; `Cache-Control: private, no-store`; no CRLF mutation.

### 9.6 Golden testing

1. **`synthetic_golden.pag`**: authored **independently** of `pag_layout.build_pag_file` (manual bytes or separate `build_synthetic_golden.py` that must not import the production builder). Reviewed byte-by-byte against the field tables. Production builder is then compared to that fixture.
2. Field-sensitivity unit tests with synthetic inputs.
3. Optional local real-file hash check (`8472dcb4…`) when private fixture exists; never commit real PII.

---

## 10. UI

ProClean visual system; local CSS; status via text+icon (not color alone). Sections: Beneficiarios / Importar / Captura y editor / Historial. No browser storage of sensitive drafts.

---

## 11. Security

- Role checks on every Banorte route including historical download.
- **CSRF required** on all POST/PATCH/DELETE (and any JSON mutating endpoint). Module-scoped (`banorte/csrf.py`): cryptographically random token bound to the Flask session, derived/signed using app `SECRET_KEY`, compared with `hmac.compare_digest` (no custom crypto), reject missing/invalid/other-session tokens, cover forms and JSON, never log the token, never mutate via GET. Do **not** add global CSRF to all of ProClean via `app.py`.
- Upload validation; no macros; escape templates; mask logs.
- Transactional import/export with full rollback.
- Backend authority §8.5.
- Cache-Control on bank-data responses.

Note: ProClean today largely lacks global CSRF; Banorte Fase 1 **must** implement it for this module even if other modules do not yet.

---

## 12. Test matrix

Cases 1–40 from prior matrix remain, plus:

- Split `validation_status` / `record_status` after replace (EXITOSO remains on inactive row).
- Identity matrix cases (CURP+new account; account conflict; name-only no merge).
- Manual→validated creates new row + `replaces_id` (no destructive overwrite).
- SQL FK / CHECK / partial unique enforcement with `PRAGMA foreign_keys=ON`.
- Money rounding accepted with warning; euro symbol no FX.
- Backend ignores client-supplied forged account.
- CSRF rejection without token; GET cannot mutate.
- No Excel BLOB column.
- Cents-only money fields.
- Duplicate consecutive metadata on new row only.
- Independent synthetic golden.
- Layout limit overflows blocked.
- Excel precision-loss row marked for review.

---

## 13. Rollback strategy

Additive feature; transaction rollback on failure; export blobs immutable; consecutive reuse adds a new row only.

---

## 14. Locked decisions

| Topic | Decision |
|-------|----------|
| Module shape | `modules/nomina/banorte/` on existing blueprint |
| `.pag` storage | SQLite BLOB only |
| Excel original | Not stored as BLOB |
| Status model | `validation_status` ⊕ `record_status` (no `is_active`) |
| Version FK | `replaces_id` only |
| Money columns | cents canonical |
| Snapshot | `export_items` (no separate snapshot hash column) |
| Golden | Independent of production builder |
| CSRF | Mandatory for Banorte mutating routes |
| `calc_nomina` | Disconnected |

---

## 15. Spec self-review

- Corrections 1–14 incorporated.
- No open “optional blob” decision.
- Monetary rule consistent with ROUND_HALF_UP acceptance.
- Layout limits explicit.
- Future `NormalizedPayment` still unwired.
