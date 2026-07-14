"""Read-only Banorte queries over saved nómina calculation runs."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from modules.nomina.banorte.repository import connect
from modules.nomina.banorte.schema import ensure_banorte_tables


Q2 = Decimal("0.01")


def neto_final_to_decimal(value: Any) -> Decimal:
    """Deterministic REAL/storage → Decimal(2). Does not apply operative payroll rounding."""
    if value is None:
        raise ValueError("neto_missing")
    if isinstance(value, bool):
        raise ValueError("neto_invalid")
    if isinstance(value, Decimal):
        d = value
    else:
        d = Decimal(str(value))
    return d.quantize(Q2, rounding=ROUND_HALF_UP)


def neto_to_cents(value: Any) -> int:
    d = neto_final_to_decimal(value)
    return int((d * 100).to_integral_value(rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class RunSummary:
    id: int
    fecha_inicio: str
    fecha_fin: str
    cliente: str
    status: str
    updated_at: str
    total_empleados: int
    worker_count: int
    total_exportable_cents: int
    positive_neto_count: int
    prior_export_count: int
    warning_count: int
    block_count: int


def list_exportable_calculo_runs(
    db_path: str,
    *,
    limit: int = 20,
    offset: int = 0,
) -> list[RunSummary]:
    """Runs with at least one row and at least one neto_a_pagar_final > 0 (E5+E3)."""
    lim = max(1, min(int(limit), 100))
    off = max(0, int(offset))
    conn = connect(db_path)
    try:
        ensure_banorte_tables(conn)
        rows = conn.execute(
            """
            SELECT r.id, r.fecha_inicio, r.fecha_fin, r.cliente, r.status, r.updated_at,
                   r.total_empleados, r.warning_count, r.block_count
            FROM nomina_calculo_runs r
            WHERE EXISTS (
                SELECT 1 FROM nomina_calculo_rows c
                WHERE c.calculo_id = r.id AND c.neto_a_pagar_final IS NOT NULL
                  AND CAST(c.neto_a_pagar_final AS REAL) > 0
            )
            ORDER BY datetime(r.updated_at) DESC, r.id DESC
            LIMIT ? OFFSET ?
            """,
            (lim, off),
        ).fetchall()
        out: list[RunSummary] = []
        for r in rows:
            cid = int(r["id"])
            crow = conn.execute(
                """
                SELECT id, neto_a_pagar_final FROM nomina_calculo_rows
                WHERE calculo_id = ?
                """,
                (cid,),
            ).fetchall()
            total_cents = 0
            positive = 0
            for cr in crow:
                try:
                    cents = neto_to_cents(cr["neto_a_pagar_final"])
                except ValueError:
                    continue
                if cents > 0:
                    total_cents += cents
                    positive += 1
            if positive < 1:
                continue
            prior = conn.execute(
                "SELECT COUNT(*) AS n FROM nomina_banorte_exports WHERE calculo_id = ?",
                (cid,),
            ).fetchone()
            out.append(
                RunSummary(
                    id=cid,
                    fecha_inicio=str(r["fecha_inicio"] or ""),
                    fecha_fin=str(r["fecha_fin"] or ""),
                    cliente=str(r["cliente"] or ""),
                    status=str(r["status"] or ""),
                    updated_at=str(r["updated_at"] or ""),
                    total_empleados=int(r["total_empleados"] or 0),
                    worker_count=len(crow),
                    total_exportable_cents=total_cents,
                    positive_neto_count=positive,
                    prior_export_count=int(prior["n"] if prior else 0),
                    warning_count=int(r["warning_count"] or 0),
                    block_count=int(r["block_count"] or 0),
                )
            )
        return out
    finally:
        conn.close()


def get_calculo_run_readonly(db_path: str, calculo_id: int) -> dict[str, Any] | None:
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM nomina_calculo_runs WHERE id = ?",
            (int(calculo_id),),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def list_calculo_rows_readonly(db_path: str, calculo_id: int) -> list[dict[str, Any]]:
    conn = connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT id, asistencia_row_id, nss, numero_empleado, nombre_empleado,
                   cliente, planta, banco, cuenta, neto_a_pagar_final, row_status
            FROM nomina_calculo_rows
            WHERE calculo_id = ?
            ORDER BY id ASC
            """,
            (int(calculo_id),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
