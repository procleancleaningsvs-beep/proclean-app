# Banorte `.pag` layout (Fase 1)

Derived from programmatic analysis of a real Banorte reference file and the
approved design spec. **Do not invent fields.** Fictional examples only below.

## Global invariants

| Rule | Value |
|------|-------|
| Encoding | ASCII printable |
| Line width | exactly 165 characters |
| Separators | CRLF (`\r\n`) **between** lines |
| Trailing CRLF after last line | **absent** |
| Emisora | `67059` |
| Clave servicio | `NE` |
| Banco receptor | `072` |
| Tipo cuenta | `01` |
| Tipo movimiento | `0` |
| IVA | `00000000` |
| Detail acción | single space |

### Structural limits

| Field | Max |
|-------|-----|
| Detail count | 6 digits |
| Total cents | 15 digits |
| Employee number | 10 digits (zero-padded) |
| Account | 18 digits (zero-padded) |
| Consecutive | `01`–`99` |

Filename: `NI67059` + consecutive + `.pag`.

## Header record (`H`) — positions 0-based

| Field | Start | End | Len | Pad | Content | Fictional example |
|-------|-------|-----|-----|-----|---------|-------------------|
| tipo_registro | 0 | 1 | 1 | — | `H` | `H` |
| clave_servicio | 1 | 3 | 2 | — | `NE` | `NE` |
| emisora | 3 | 8 | 5 | `0` left | constant | `67059` |
| fecha | 8 | 16 | 8 | `0` | `YYYYMMDD` | `20260115` |
| consecutivo | 16 | 18 | 2 | `0` | `01`–`99` | `07` |
| num_registros | 18 | 24 | 6 | `0` | detail count | `000003` |
| importe_total | 24 | 39 | 15 | `0` | total cents | `000000000650050` |
| num_registros_alt | 39 | 45 | 6 | `0` | constant zeros | `000000` |
| importe_alt | 45 | 60 | 15 | `0` | constant zeros | `000000000000000` |
| num_bajas | 60 | 66 | 6 | `0` | constant zeros | `000000` |
| importe_bajas | 66 | 81 | 15 | `0` | constant zeros | `000000000000000` |
| num_verificacion | 81 | 87 | 6 | `0` | constant zeros | `000000` |
| accion | 87 | 88 | 1 | — | `0` | `0` |
| filler_spaces | 88 | 165 | 77 | space | spaces | 77 spaces |

All zero ranges and space ranges above were verified against the real reference
layout analysis; they are not a catch-all unknown filler.

## Detail record (`D`) — positions 0-based

| Field | Start | End | Len | Pad | Content | Fictional example |
|-------|-------|-----|-----|-----|---------|-------------------|
| tipo_registro | 0 | 1 | 1 | — | `D` | `D` |
| fecha | 1 | 9 | 8 | `0` | `YYYYMMDD` | `20260115` |
| num_empleado | 9 | 19 | 10 | `0` | effective employee | `0000000011` |
| referencia_servicio | 19 | 59 | 40 | space | blank in samples | 40 spaces |
| campo_secundario | 59 | 99 | 40 | space | blank in samples | 40 spaces |
| importe | 99 | 114 | 15 | `0` | cents | `000000000270000` |
| banco_receptor | 114 | 117 | 3 | — | `072` | `072` |
| tipo_cuenta | 117 | 119 | 2 | — | `01` | `01` |
| numero_cuenta | 119 | 137 | 18 | `0` | Banorte account | `000000001321431243` |
| tipo_movimiento | 137 | 138 | 1 | — | `0` | `0` |
| accion | 138 | 139 | 1 | — | space | ` ` |
| iva | 139 | 147 | 8 | `0` | `00000000` | `00000000` |
| filler_spaces | 147 | 165 | 18 | space | spaces | 18 spaces |

Employee/account values in examples are **fictional**.

## Production builder

`modules/nomina/banorte/pag_layout.py` emits deterministic ASCII bytes matching
these tables. The versioned golden fixture
`tests/fixtures/banorte/synthetic_golden.pag` is authored by
`tests/fixtures/banorte/build_synthetic_golden.py`, which must not import the
production builder.
