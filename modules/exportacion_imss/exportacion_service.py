from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from typing import Any

DATA_DIR = os.environ.get("DATA_DIR", "./data")
MOVIMIENTOS_DIR = os.path.join(DATA_DIR, "movimientos_imss")
HEADCOUNT_PATH = os.path.join(DATA_DIR, "headcount.json")


def _normalize_spaces(value: str) -> str:
    return " ".join((value or "").split())


def _normalize_name(value: Any) -> str:
    return _normalize_spaces(str(value or "").upper().strip())


def _ensure_dirs() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(MOVIMIENTOS_DIR, exist_ok=True)
    try:
        os.makedirs("/app/data/movimientos_imss", exist_ok=True)
    except OSError:
        pass


def _read_movimiento(movimiento_id: str) -> dict[str, Any] | None:
    path = os.path.join(MOVIMIENTOS_DIR, f"{movimiento_id}.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return data if isinstance(data, dict) else None


def _all_movimientos() -> list[dict[str, Any]]:
    _ensure_dirs()
    items: list[dict[str, Any]] = []
    for name in os.listdir(MOVIMIENTOS_DIR):
        if not name.lower().endswith(".json"):
            continue
        path = os.path.join(MOVIMIENTOS_DIR, name)
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            items.append(data)
    return items


def _fecha_ddmmyyyy(value: str) -> str:
    s = str(value or "").strip()
    if not s:
        return ""
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s[:10], fmt).strftime("%d%m%Y")
        except ValueError:
            continue
    return s.replace("/", "").replace("-", "")[:8]


def _to_float(value: Any) -> float:
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def _right(text: Any, size: int) -> str:
    return str(text or "").strip()[:size].ljust(size)


def guardar_movimiento(data_dict: dict[str, Any]) -> dict[str, Any]:
    try:
        _ensure_dirs()
        required = [
            "tipo_movimiento",
            "rp",
            "nss",
            "apellido_paterno",
            "apellido_materno",
            "nombres",
            "fecha_movimiento",
        ]
        missing = [field for field in required if not str(data_dict.get(field, "")).strip()]
        if missing:
            raise ValueError(f"Faltan campos obligatorios: {', '.join(missing)}")

        tipo = str(data_dict.get("tipo_movimiento") or "").strip().upper()
        if tipo not in {"ALTA", "BAJA", "MODIFICACION"}:
            raise ValueError("tipo_movimiento debe ser ALTA, BAJA o MODIFICACION.")

        movimiento = {
            "id": str(uuid.uuid4()),
            "tipo_movimiento": tipo,
            "cliente": _normalize_spaces(str(data_dict.get("cliente") or "").strip()),
            "rp": _normalize_spaces(str(data_dict.get("rp") or "").strip())[:11],
            "nss": _normalize_spaces(str(data_dict.get("nss") or "").strip())[:11],
            "rfc": _normalize_spaces(str(data_dict.get("rfc") or "").strip()),
            "curp": _normalize_spaces(str(data_dict.get("curp") or "").strip())[:18],
            "apellido_paterno": _normalize_spaces(str(data_dict.get("apellido_paterno") or "").strip()),
            "apellido_materno": _normalize_spaces(str(data_dict.get("apellido_materno") or "").strip()),
            "nombres": _normalize_spaces(str(data_dict.get("nombres") or "").strip()),
            "sbc": _to_float(data_dict.get("sbc")),
            "tipo_trabajador": "1",
            "tipo_salario": "0",
            "jornada_reducida": "0",
            "fecha_movimiento": _normalize_spaces(str(data_dict.get("fecha_movimiento") or "").strip()),
            "clave_ubicacion": _normalize_spaces(str(data_dict.get("clave_ubicacion") or "").strip())[:17],
            "num_credito": _normalize_spaces(str(data_dict.get("num_credito") or "").strip())[:10],
            "fecha_inicio_descuento": _normalize_spaces(str(data_dict.get("fecha_inicio_descuento") or "").strip()),
            "tipo_descuento": _normalize_spaces(str(data_dict.get("tipo_descuento") or "").strip())[:1],
            "valor_descuento": _normalize_spaces(str(data_dict.get("valor_descuento") or "").strip()),
            "fecha_captura": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        out_path = os.path.join(MOVIMIENTOS_DIR, f"{movimiento['id']}.json")
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(movimiento, fh, ensure_ascii=False, indent=2)
        return movimiento
    except Exception as exc:
        raise ValueError(f"No se pudo guardar movimiento IMSS: {exc}") from exc


def generar_txt_idse(movimientos_ids: list[str], tipo_movimiento: str) -> str:
    try:
        tipo = str(tipo_movimiento or "").strip().upper()
        lineas: list[str] = []
        consecutivo = 1
        for mov_id in movimientos_ids:
            mov = _read_movimiento(str(mov_id).strip())
            if not mov:
                continue
            if tipo and mov.get("tipo_movimiento", "").upper() != tipo:
                continue
            row = [
                str(consecutivo),
                str(mov.get("rp", "")),
                str(mov.get("nss", "")),
                str(mov.get("apellido_paterno", "")),
                str(mov.get("apellido_materno", "")),
                str(mov.get("nombres", "")),
                f"{_to_float(mov.get('sbc')):.2f}",
                str(mov.get("tipo_trabajador", "1") or "1"),
                str(mov.get("tipo_salario", "0") or "0"),
                str(mov.get("jornada_reducida", "0") or "0"),
                _fecha_ddmmyyyy(str(mov.get("fecha_movimiento", ""))),
                str(mov.get("curp", "")),
            ]
            lineas.append("\t".join(row))
            consecutivo += 1
        return "\n".join(lineas)
    except Exception as exc:
        raise ValueError(f"No se pudo generar TXT IDSE: {exc}") from exc


def generar_txt_sua(movimientos_ids: list[str]) -> str:
    try:
        lineas: list[str] = []
        for mov_id in movimientos_ids:
            mov = _read_movimiento(str(mov_id).strip())
            if not mov:
                continue
            sbc_num = int(round(_to_float(mov.get("sbc")) * 100))
            sbc_field = str(sbc_num).zfill(5)[-5:]
            clave = str(mov.get("clave_ubicacion", "") or "")
            clave2 = _right(clave, 2)
            fecha_mov = _fecha_ddmmyyyy(str(mov.get("fecha_movimiento", ""))).zfill(8)
            fecha_desc = _fecha_ddmmyyyy(str(mov.get("fecha_inicio_descuento", "")))
            fecha_desc = fecha_desc.zfill(8) if fecha_desc else "00000000"
            tipo_desc = str(mov.get("tipo_descuento", "") or "").strip()[:1] or "0"
            valor_desc = str(mov.get("valor_descuento", "") or "").strip()
            valor_desc = (valor_desc or "00000000").replace(".", "").replace(",", "")
            valor_desc = valor_desc.zfill(8)[-8:]
            nombre_compuesto = (
                f"{mov.get('apellido_paterno', '')}${mov.get('apellido_materno', '')}${mov.get('nombres', '')}"
            )
            linea = (
                _right(mov.get("rp"), 11)
                + _right(mov.get("nss"), 11)
                + _right(mov.get("rfc"), 13)
                + _right(mov.get("curp"), 18)
                + _right(nombre_compuesto, 50)
                + "1"
                + "0"
                + fecha_mov[:8]
                + sbc_field
                + clave2
                + _right(clave, 17)
                + _right(mov.get("num_credito"), 10)
                + fecha_desc
                + tipo_desc
                + valor_desc
                + "00000000"
            )
            # El portal SUA suele exigir longitud fija; recortamos/completamos a 164.
            lineas.append(linea[:164].ljust(164))
        return "\n".join(lineas)
    except Exception as exc:
        raise ValueError(f"No se pudo generar TXT SUA: {exc}") from exc


def obtener_movimientos(tipo: str | None = None, cliente: str | None = None, fecha_desde: str | None = None) -> list[dict[str, Any]]:
    try:
        items = _all_movimientos()
        out: list[dict[str, Any]] = []
        for item in items:
            if tipo and str(item.get("tipo_movimiento", "")).strip().upper() != str(tipo).strip().upper():
                continue
            if cliente and str(item.get("cliente", "")).strip().casefold() != str(cliente).strip().casefold():
                continue
            if fecha_desde:
                cap = str(item.get("fecha_captura", "")).strip()
                if cap and cap[:10] < str(fecha_desde).strip()[:10]:
                    continue
            out.append(item)
        out.sort(key=lambda x: str(x.get("fecha_captura", "")), reverse=True)
        return out
    except Exception as exc:
        raise ValueError(f"No se pudo obtener movimientos IMSS: {exc}") from exc


def autocompletar_desde_headcount(nss: str | None = None, nombre_completo: str | None = None) -> dict[str, Any] | None:
    try:
        if not os.path.exists(HEADCOUNT_PATH):
            return None
        with open(HEADCOUNT_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, list):
            return None
        nss_objetivo = _normalize_spaces(str(nss or "").strip())
        nombre_objetivo = _normalize_name(nombre_completo)
        for row in data:
            if not isinstance(row, dict):
                continue
            row_nss = _normalize_spaces(str(row.get("nss", "")).strip())
            row_nombre = _normalize_name(row.get("nombre_completo", ""))
            if nss_objetivo and row_nss == nss_objetivo:
                match = row
            elif nombre_objetivo and row_nombre == nombre_objetivo:
                match = row
            else:
                continue
            return {
                "cliente": row.get("cliente", ""),
                "nss": row.get("nss", ""),
                "rfc": row.get("rfc_homoclave", ""),
                "curp": row.get("curp", ""),
                "apellido_paterno": row.get("apellido_paterno", ""),
                "apellido_materno": row.get("apellido_materno", ""),
                "nombres": row.get("nombre", ""),
                "sbc": row.get("sueldo_diario", ""),
                "rp": row.get("patron", ""),
            }
        return None
    except Exception as exc:
        raise ValueError(f"No se pudo autocompletar desde headcount: {exc}") from exc
