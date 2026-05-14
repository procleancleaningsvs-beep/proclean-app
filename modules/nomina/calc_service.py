"""Orquestación de cálculo preliminar (lecturas SQLite + motor puro)."""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal
from typing import Any

from modules.nomina.calc_nomina import (
    CalcularNominaConfig,
    EmpleadoCalcInput,
    aplicar_overrides_a_fila,
    calcular_nomina_preliminar,
)
from modules.nomina.config import (
    MSG_HEADCOUNT_UNAVAILABLE_CALCULO,
    WARN_SAME_NSS_MULTIPLE_CLIENTS,
    get_exento_he_for_year,
    get_smg_for_year,
    get_umi_for_year,
)
from modules.nomina.db import (
    get_asistencia_import,
    get_latest_infonavit_import_id,
    get_latest_vacaciones_import_id,
    get_nomina_calculo_run,
    get_nomina_calculo_row,
    list_empleado_parametros,
    list_infonavit_rows,
    list_vacaciones_empleados,
)


def _norm_key(s: str) -> str:
    return " ".join(str(s or "").strip().lower().split())


def _norm_nss(s: str) -> str:
    return re.sub(r"\s+", "", str(s or "").strip())


def _norm_name_param(s: str) -> str:
    t = " ".join(str(s or "").replace("\u00a0", " ").upper().split()).strip()
    t = unicodedata.normalize("NFD", t)
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn")
    t = re.sub(r"[^A-Z0-9 ]+", " ", t)
    return " ".join(t.split()).strip()


def _param_index(param_rows: list[dict[str, Any]]) -> tuple[dict[str, dict], dict[tuple[str, str], dict]]:
    by_nss: dict[str, dict[str, Any]] = {}
    by_name_cliente: dict[tuple[str, str], dict[str, Any]] = {}
    for p in param_rows:
        nss = _norm_nss(str(p.get("nss") or ""))
        if nss:
            by_nss[nss] = p
        nm = _norm_name_param(str(p.get("nombre") or ""))
        cl = _norm_key(str(p.get("cliente") or ""))
        if nm:
            by_name_cliente[(nm, cl)] = p
    return by_nss, by_name_cliente


def _match_parametro(
    row: dict[str, Any],
    by_nss: dict[str, dict],
    by_name_cliente: dict[tuple[str, str], dict],
) -> dict[str, Any] | None:
    nss = _norm_nss(str(row.get("nss") or ""))
    if nss and nss in by_nss:
        return by_nss[nss]
    nm = _norm_name_param(str(row.get("nombre_empleado") or ""))
    cl = _norm_key(str(row.get("cliente") or ""))
    if nm and (nm, cl) in by_name_cliente:
        return by_name_cliente[(nm, cl)]
    if nm:
        for (pn, pc), pv in by_name_cliente.items():
            if pn == nm and (not pc or not cl or pc == cl):
                return pv
    return None


def _headcount_disponible() -> bool:
    try:
        from modules.comparativo.headcount_service import obtener_activos

        obtener_activos()
        return True
    except Exception:
        return False


def build_calculo_payload(
    db_path: str,
    *,
    asistencia_import_id: int,
    clientes_filter: list[str],
    config_form: dict[str, Any],
    previous_overrides_by_asistencia_id: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    imp = get_asistencia_import(db_path, asistencia_import_id)
    if imp is None:
        raise ValueError("import_not_found")
    fi = _parse_date(str(imp.get("fecha_inicio") or ""))
    ff = _parse_date(str(imp.get("fecha_fin") or ""))
    if fi is None or ff is None:
        raise ValueError("fechas_invalidas")

    anio = fi.year
    umi = get_umi_for_year(anio)

    domingo = str(config_form.get("domingo_opcion") or "proporcional").strip().lower()
    cfg = CalcularNominaConfig(
        anio_smg=anio,
        dias_tarifa_isr=Decimal(str(config_form.get("dias_tarifa_isr") or 7)),
        dias_tarifa_subs=Decimal(str(config_form.get("dias_tarifa_subs") or 7)),
        domingo_opcion=domingo,
        es_fin_de_mes=bool(config_form.get("es_fin_de_mes")),
        permitir_negativo_isr=bool(config_form.get("permitir_negativo_isr")),
        fecha_inicio=fi,
        fecha_fin=ff,
    )

    param_rows = list_empleado_parametros(db_path, limit=5000)
    by_nss, by_name_cliente = _param_index(param_rows)

    vac_by_nss: dict[str, dict[str, Any]] = {}
    vac_id_map: dict[str, int] = {}
    vac_imp = get_latest_vacaciones_import_id(db_path)
    if vac_imp:
        for vr in list_vacaciones_empleados(db_path, import_id=vac_imp, limit=8000):
            k = _norm_nss(str(vr.get("nss") or ""))
            if k and k not in vac_by_nss:
                vac_by_nss[k] = vr
                vac_id_map[k] = int(vr["id"])

    inf_by_nss: dict[str, dict[str, Any]] = {}
    inf_id_map: dict[str, int] = {}
    inf_imp = get_latest_infonavit_import_id(db_path)
    if inf_imp:
        for ir in list_infonavit_rows(db_path, import_id=inf_imp, limit=8000):
            k = _norm_nss(str(ir.get("nss") or ""))
            if k and k not in inf_by_nss:
                inf_by_nss[k] = ir
                inf_id_map[k] = int(ir["id"])

    clientes_keys = {_norm_key(c) for c in clientes_filter if str(c).strip()}
    if not clientes_keys:
        clientes_keys = {_norm_key(str(imp.get("cliente") or ""))} if imp.get("cliente") else set()

    rows_in = [r for r in imp.get("rows") or [] if _norm_key(str(r.get("cliente") or "")) in clientes_keys]
    if not clientes_keys:
        rows_in = list(imp.get("rows") or [])

    nss_clients: dict[str, set[str]] = {}
    for r in rows_in:
        n = _norm_nss(str(r.get("nss") or ""))
        if n:
            nss_clients.setdefault(n, set()).add(str(r.get("cliente") or "").strip())

    run_warnings: list[str] = []
    if not _headcount_disponible():
        run_warnings.append(MSG_HEADCOUNT_UNAVAILABLE_CALCULO)
    for n, clset in nss_clients.items():
        if len(clset) > 1:
            run_warnings.append(f"{WARN_SAME_NSS_MULTIPLE_CLIENTS}:{n}")

    empleados: list[EmpleadoCalcInput] = []
    for r in rows_in:
        if r.get("errors"):
            continue
        p = _match_parametro(r, by_nss, by_name_cliente)
        nss_k = _norm_nss(str(r.get("nss") or ""))
        vac_row = vac_by_nss.get(nss_k)
        inf_row = inf_by_nss.get(nss_k)

        es_frontera = bool(p and int(p.get("es_frontera") or 0))
        zona = "FRONTERA" if es_frontera else "GENERAL"
        smg = get_smg_for_year(anio, zona) or Decimal("0")
        ex_he = get_exento_he_for_year(anio, zona) or Decimal("0")

        salario_op: Decimal | None = None
        valor_he: Decimal | None = None
        pid = None
        num_emp = None
        if p:
            pid = int(p["id"])
            so = p.get("salario_operativo")
            vx = p.get("valor_x_he")
            try:
                if so is not None and float(so) > 0:
                    salario_op = Decimal(str(so))
            except (TypeError, ValueError):
                pass
            try:
                if vx is not None and float(vx) > 0:
                    valor_he = Decimal(str(vx))
            except (TypeError, ValueError):
                pass
            num_emp = str(p.get("numero_empleado") or "").strip() or None

        daily = [
            r.get("dia_1_value"),
            r.get("dia_2_value"),
            r.get("dia_3_value"),
            r.get("dia_4_value"),
            r.get("dia_5_value"),
            r.get("dia_6_value"),
            r.get("dia_7_value"),
        ]
        emp = EmpleadoCalcInput(
            asistencia_row_id=int(r["id"]),
            nombre_empleado=str(r.get("nombre_empleado") or ""),
            cliente=str(r.get("cliente") or ""),
            planta=str(r.get("planta") or ""),
            puesto=str(r.get("puesto") or ""),
            banco=str(r.get("banco") or ""),
            cuenta=str(r.get("cuenta") or ""),
            nss=str(r.get("nss") or ""),
            daily_values=[str(x or "") for x in daily],
            he_raw=r.get("he"),
            horas_extra_normales_raw=r.get("horas_extra_normales"),
            dias_cubiertos_normales_raw=r.get("dias_cubiertos_normales"),
            vacaciones_laboradas_raw=r.get("vacaciones_laboradas"),
            prima_vacacional_raw=str(r.get("prima_vacacional") or ""),
            bono_raw=r.get("bono"),
            deducciones_raw=r.get("deducciones"),
            headcount_match_status=str(r.get("headcount_match_status") or "") or None,
            parametro_empleado_id=pid,
            numero_empleado=num_emp,
            salario_operativo=salario_op,
            valor_x_he=valor_he,
            es_frontera=es_frontera,
            smg_usado=smg,
            exento_he_usado=ex_he,
            vacaciones_empleado_id=vac_id_map.get(nss_k),
            vacaciones_row=vac_row,
            infonavit_row_id=inf_id_map.get(nss_k),
            infonavit_row=inf_row,
        )
        empleados.append(emp)

    out_rows = calcular_nomina_preliminar(empleados, cfg, umi_diario=umi)
    prev = previous_overrides_by_asistencia_id or {}
    merged: list[dict[str, Any]] = []
    for row in out_rows:
        aid = int(row["asistencia_row_id"])
        if aid in prev and prev[aid]:
            merged.append(aplicar_overrides_a_fila(row, prev[aid], cfg=cfg))
        else:
            merged.append(row)

    wcount_rows = sum(len(r.get("warnings_json") or []) for r in merged)
    bcount = sum(len(r.get("blocks_json") or []) for r in merged)

    raw_json = {
        "run_warnings": run_warnings,
        "clientes_filter": clientes_filter,
        "vacaciones_import_id": vac_imp,
        "infonavit_import_id": inf_imp,
    }
    return {
        "rows": merged,
        "warning_count": wcount_rows,
        "block_count": bcount,
        "total_empleados": len(merged),
        "raw_json": raw_json,
        "fecha_inicio": fi.isoformat(),
        "fecha_fin": ff.isoformat(),
        "config_json": {
            "domingo_opcion": domingo,
            "dias_tarifa_isr": float(cfg.dias_tarifa_isr),
            "dias_tarifa_subs": float(cfg.dias_tarifa_subs),
            "es_fin_de_mes": cfg.es_fin_de_mes,
            "permitir_negativo_isr": cfg.permitir_negativo_isr,
            "anio_smg": anio,
        },
    }


def _parse_date(s: str) -> date | None:
    s = (s or "").strip()[:10]
    if not s:
        return None
    try:
        y, m, d = (int(s[0:4]), int(s[5:7]), int(s[8:10]))
        return date(y, m, d)
    except ValueError:
        return None


def index_overrides_from_calculo_rows(db_rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for r in db_rows:
        aid = int(r.get("asistencia_row_id") or 0)
        if not aid:
            continue
        o = r.get("manual_overrides_json") or {}
        if isinstance(o, str):
            import json

            try:
                o = json.loads(o)
            except json.JSONDecodeError:
                o = {}
        if o:
            out[aid] = dict(o)
    return out


def resync_row_totales(db_path: str, row_id: int) -> None:
    """Recalcula base/isr/totales a partir de columnas actuales + manual_overrides_json."""
    from datetime import datetime

    row = get_nomina_calculo_row(db_path, row_id)
    if not row:
        return
    run = get_nomina_calculo_run(db_path, int(row["calculo_id"]))
    if not run:
        return
    cj = run.get("config_json") or {}
    fi_s = str(run.get("fecha_inicio") or "")[:10]
    ff_s = str(run.get("fecha_fin") or "")[:10]
    fi = datetime.strptime(fi_s, "%Y-%m-%d").date() if fi_s else None
    ff = datetime.strptime(ff_s, "%Y-%m-%d").date() if ff_s else None
    cfg = CalcularNominaConfig(
        dias_tarifa_isr=Decimal(str(cj.get("dias_tarifa_isr", 7))),
        dias_tarifa_subs=Decimal(str(cj.get("dias_tarifa_subs", 7))),
        domingo_opcion=str(cj.get("domingo_opcion") or "proporcional"),
        es_fin_de_mes=bool(cj.get("es_fin_de_mes")),
        permitir_negativo_isr=bool(cj.get("permitir_negativo_isr")),
        fecha_inicio=fi,
        fecha_fin=ff,
    )
    o = row.get("manual_overrides_json") or {}
    refreshed = aplicar_overrides_a_fila(dict(row), o, cfg=cfg)
    from modules.nomina.db import patch_nomina_calculo_row_engine_fields

    patch_nomina_calculo_row_engine_fields(
        db_path,
        row_id,
        {
            "base_gravada": refreshed.get("base_gravada"),
            "isr": refreshed.get("isr"),
            "total_percepciones": refreshed.get("total_percepciones"),
            "total_deducciones": refreshed.get("total_deducciones"),
            "neto_simple": refreshed.get("neto_simple"),
            "neto_a_pagar": refreshed.get("neto_a_pagar"),
            "base_neto_simple": refreshed.get("base_neto_simple"),
            "neto_simple_operativo": refreshed.get("neto_simple_operativo"),
            "neto_redondeado": refreshed.get("neto_redondeado"),
            "ajuste_al_neto": refreshed.get("ajuste_al_neto"),
            "neto_a_pagar_final": refreshed.get("neto_a_pagar_final"),
            "detail_json": refreshed.get("detail_json"),
        },
    )
