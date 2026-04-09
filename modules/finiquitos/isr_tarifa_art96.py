"""
Cálculo ISR art. 96 LISR con trazabilidad de fila (tarifa mensual o quincenal 2026, RMF Anexo 8).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from modules.finiquitos.config import ISR_TABLA_MENSUAL_2026, ISR_TABLA_QUINCENAL_2026

D2 = Decimal("0.01")
D0 = Decimal("0")


def _q(x: Decimal) -> Decimal:
    return x.quantize(D2, rounding=ROUND_HALF_UP)


PeriodicidadTarifa = Literal["mensual", "quincenal", "15_dias"]


@dataclass(frozen=True)
class ResultadoTarifaISR96:
    """Fila aplicada y desglose art. 96."""

    referencia_tabla: str
    periodicidad: str
    fila_numero: int  # 1..n; 0 si base <= 0
    limite_inferior: Decimal
    limite_superior: Decimal | None
    cuota_fija: Decimal
    porcentaje_marginal: Decimal
    base_gravada: Decimal
    excedente_limite_inferior: Decimal
    isr: Decimal
    formula_pasos: str

    def to_auditoria(self) -> dict[str, Any]:
        return {
            "referencia_tabla": self.referencia_tabla,
            "periodicidad": self.periodicidad,
            "fila_numero": self.fila_numero,
            "limite_inferior": float(self.limite_inferior),
            "limite_superior": float(self.limite_superior) if self.limite_superior is not None else None,
            "limite_superior_en_adelante": self.limite_superior is None and self.fila_numero > 0,
            "cuota_fija": float(self.cuota_fija),
            "porcentaje_marginal": float(self.porcentaje_marginal),
            "base_gravada": float(self.base_gravada),
            "excedente_limite_inferior": float(self.excedente_limite_inferior),
            "isr": float(self.isr),
            "formula_pasos": self.formula_pasos,
        }


def isr_art96_con_detalle(
    base_gravada: Decimal,
    periodicidad: PeriodicidadTarifa,
) -> ResultadoTarifaISR96:
    """
    Selecciona la fila de la tarifa cuya base gravada cae entre límite inferior y superior.
    ISR = cuota_fija + (base - lim_inf) * (pct/100).
    """
    ref_mensual = "RMF 2026 Anexo 8 — Tarifa mensual art. 96 LISR"
    ref_quinc = "RMF 2026 Anexo 8 — Tarifa quincenal art. 96 LISR"

    if base_gravada <= 0:
        return ResultadoTarifaISR96(
            referencia_tabla=ref_mensual if periodicidad == "mensual" else ref_quinc,
            periodicidad=periodicidad,
            fila_numero=0,
            limite_inferior=D0,
            limite_superior=None,
            cuota_fija=D0,
            porcentaje_marginal=D0,
            base_gravada=_q(base_gravada),
            excedente_limite_inferior=D0,
            isr=D0,
            formula_pasos="Base gravada ≤ 0: no aplica tarifa.",
        )

    es_quinc = periodicidad in ("quincenal", "15_dias")
    tab = ISR_TABLA_QUINCENAL_2026 if es_quinc else ISR_TABLA_MENSUAL_2026
    ref = ref_quinc if es_quinc else ref_mensual
    per_label = "quincenal" if es_quinc else "mensual"

    bg = _q(base_gravada)
    for idx, (lim_inf, lim_sup, cuota, pct) in enumerate(tab, start=1):
        if bg < lim_inf:
            continue
        if lim_sup is not None and bg > lim_sup:
            continue
        exc = _q(bg - lim_inf)
        isr = _q(cuota + exc * (pct / Decimal("100")))
        sup_txt = f"{lim_sup}" if lim_sup is not None else "en adelante"
        formula = (
            f"Fila {idx} ({per_label}): límite inf. {lim_inf}, límite sup. {sup_txt}; "
            f"cuota fija {cuota} + ({bg} − {lim_inf}) × {pct}% = {cuota} + {exc} × {pct/Decimal('100')} = {isr}"
        )
        return ResultadoTarifaISR96(
            referencia_tabla=ref,
            periodicidad=per_label,
            fila_numero=idx,
            limite_inferior=lim_inf,
            limite_superior=lim_sup,
            cuota_fija=cuota,
            porcentaje_marginal=pct,
            base_gravada=bg,
            excedente_limite_inferior=exc,
            isr=isr,
            formula_pasos=formula,
        )

    return ResultadoTarifaISR96(
        referencia_tabla=ref,
        periodicidad=per_label,
        fila_numero=0,
        limite_inferior=D0,
        limite_superior=None,
        cuota_fija=D0,
        porcentaje_marginal=D0,
        base_gravada=bg,
        excedente_limite_inferior=D0,
        isr=D0,
        formula_pasos="No se encontró fila aplicable (revisar tablas).",
    )


def isr_art96_importe(base_gravada: Decimal, periodicidad: PeriodicidadTarifa) -> Decimal:
    """Solo el ISR; mismo criterio que siempre."""
    return isr_art96_con_detalle(base_gravada, periodicidad).isr
