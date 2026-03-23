from __future__ import annotations

import json
import os
import random
import re
import shutil
import string
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

HEX_LOWER = "0123456789abcdef"
HEX_UPPER = "0123456789ABCDEF"
DIGITS = string.digits
INVALID_FILENAME_CHARS = r'[\\/*?:"<>|]'
TEMPLATE_FILENAMES = {
    1: "formato_movimiento.docx",
    2: "formato_alta (2).docx",
    3: "formato_alta (3).docx",
    4: "formato_alta (4).docx",
}


@dataclass
class Movimiento:
    tipo: str
    nss: str
    nombre: str
    fecha: str
    salario: str
    causa_baja: str

    def to_dict(self) -> dict:
        return asdict(self)


def rnd_digits(n: int) -> str:
    return "".join(random.choices(DIGITS, k=n))


def gen_folio() -> str:
    return rnd_digits(19)


def gen_lote() -> str:
    return "4" + rnd_digits(8)


def gen_huella() -> str:
    return "".join(random.choices(HEX_LOWER, k=40))


def gen_sello() -> tuple[str, str]:
    linea1 = "".join(random.choices(HEX_UPPER, k=111))
    linea2 = "".join(random.choices(HEX_UPPER, k=20)) + " | " + "1" + rnd_digits(9)
    return linea1, linea2


def gen_hex_id() -> str:
    return f"{random.randint(0x10000000, 0xFFFFFFFF):08X}"


def normalize_tipo(value: str) -> str:
    v = (value or "").strip().lower()
    mapping = {
        "1": "alta",
        "2": "baja",
        "8": "reingreso",
        "alta": "alta",
        "baja": "baja",
        "reing": "reingreso",
        "reingreso": "reingreso",
        "reingresó": "reingreso",
    }
    if v not in mapping:
        raise ValueError(f"Tipo de movimiento no válido: {value!r}")
    return mapping[v]


def parse_ymd_date(value: str, *, field_name: str) -> date:
    s = (value or "").strip()
    if not s:
        raise ValueError(f"La {field_name} es obligatoria")
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"{field_name.capitalize()} no válida. Usa formato YYYY-MM-DD") from exc


def normalize_fecha(value: str) -> str:
    s = (value or "").strip()
    if not s:
        raise ValueError("La fecha del movimiento es obligatoria")

    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})(?:[T ].*)?", s)
    if m:
        y, mo, d = m.groups()
        return f"{d}/{mo}/{y}"

    if re.fullmatch(r"\d{2}/\d{2}/\d{4}", s):
        return s

    raise ValueError(f"Fecha no válida: {value!r}. Usa YYYY-MM-DD o DD/MM/YYYY")


def normalize_hora_lote(value: str) -> str:
    s = (value or "").strip()
    if not s:
        raise ValueError("La hora del lote es obligatoria")
    m = re.fullmatch(r"(\d{2}):(\d{2})", s)
    if not m:
        raise ValueError("Hora no válida. Usa formato HH:MM en reloj de 24 horas")
    hh, mm = map(int, m.groups())
    if hh > 23 or mm > 59:
        raise ValueError("Hora no válida. Usa formato HH:MM en reloj de 24 horas")
    return f"{hh:02d}:{mm:02d}"


def normalize_nombre(value: str) -> str:
    s = re.sub(r"\s+", " ", (value or "").strip()).upper()
    if not s:
        raise ValueError("El nombre no puede venir vacío")
    return s


def validate_nss(value: str) -> str:
    s = re.sub(r"\D", "", value or "")
    if not re.fullmatch(r"\d{11}", s):
        raise ValueError(f"NSS no válido: {value!r}. Debe tener 11 dígitos")
    return s


def normalize_salario(raw: object, tipo: str, fronterizo: bool | None = None) -> str:
    if tipo == "baja":
        return "0.00"

    if raw is None or str(raw).strip() == "":
        return "462.61" if fronterizo else "330.57"

    s = str(raw).strip().replace("$", "").replace(",", "")
    if not re.fullmatch(r"\d{1,4}\.\d{2}", s):
        raise ValueError("Salario inválido. Usa formato 0.00 y máximo 4 dígitos antes del punto")
    return s


def normalize_causa_baja(value: object, tipo: str) -> str:
    if tipo != "baja":
        return "0"
    s = str(value).strip() if value is not None else ""
    return s or "2"


def parse_movimientos(data: dict) -> list[Movimiento]:
    movimientos_raw = data.get("movimientos")
    if not isinstance(movimientos_raw, list) or not movimientos_raw:
        raise ValueError("Debes capturar al menos un movimiento")
    if len(movimientos_raw) > 4:
        raise ValueError("Máximo 4 movimientos por constancia")

    movimientos: list[Movimiento] = []
    for i, item in enumerate(movimientos_raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"El movimiento #{i} debe ser un objeto JSON")

        tipo = normalize_tipo(str(item.get("tipo", "alta")))
        nombre = normalize_nombre(item.get("nombre", ""))
        nss = validate_nss(item.get("nss", ""))
        fecha = normalize_fecha(str(item.get("fecha", "")))
        salario = normalize_salario(item.get("salario"), tipo, item.get("fronterizo"))
        causa_baja = normalize_causa_baja(item.get("causa_baja") or item.get("causa"), tipo)

        movimientos.append(
            Movimiento(
                tipo=tipo,
                nss=nss,
                nombre=nombre,
                fecha=fecha,
                salario=salario,
                causa_baja=causa_baja,
            )
        )
    return movimientos


def movimientos_from_form(form) -> tuple[list[Movimiento], str, str]:
    movimientos_raw: list[dict] = []
    fecha_lote_input = (form.get("fecha_lote") or "").strip()
    hora_lote = normalize_hora_lote(form.get("hora_lote") or "")
    fecha_lote_date = parse_ymd_date(fecha_lote_input, field_name="fecha de recepción del lote")

    for i in range(1, 5):
        nss = (form.get(f"nss_{i}") or "").strip()
        nombre = (form.get(f"nombre_{i}") or "").strip()
        fecha = (form.get(f"fecha_{i}") or "").strip()
        tipo = (form.get(f"tipo_{i}") or "alta").strip()
        salario_tipo = (form.get(f"salario_tipo_{i}") or "normal").strip().lower()
        salario_otro = (form.get(f"salario_otro_{i}") or "").strip()
        causa_baja = (form.get(f"causa_baja_{i}") or "").strip()

        if not any([nss, nombre, fecha]):
            continue

        fecha_mov_date = parse_ymd_date(fecha, field_name=f"fecha del movimiento #{i}")
        if fecha_mov_date > fecha_lote_date:
            raise ValueError(f"La fecha del movimiento #{i} no puede ser posterior a la fecha de recepción del lote")
        if fecha_mov_date < fecha_lote_date - timedelta(days=8):
            raise ValueError(f"La fecha del movimiento #{i} no puede ser mayor a 8 días anterior a la recepción del lote")

        if salario_tipo == "fronterizo":
            salario = "462.61"
        elif salario_tipo == "otro":
            salario = salario_otro
        else:
            salario = "330.57"

        movimientos_raw.append(
            {
                "tipo": tipo or "alta",
                "nss": nss,
                "nombre": nombre,
                "fecha": fecha,
                "salario": salario,
                "causa_baja": causa_baja,
                "fronterizo": salario == "462.61",
            }
        )

    return parse_movimientos({"movimientos": movimientos_raw}), fecha_lote_input, hora_lote


def unzip_docx(docx_path: Path, dest_dir: Path) -> None:
    with zipfile.ZipFile(docx_path, "r") as zf:
        zf.extractall(dest_dir)


def zip_dir_to_docx(src_dir: Path, out_docx: Path) -> None:
    with zipfile.ZipFile(out_docx, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(src_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(src_dir))


def randomize_ids(fragment: str) -> str:
    return re.sub(r'w14:(paraId|textId)="[A-F0-9]{8}"', lambda m: f'w14:{m.group(1)}="{gen_hex_id()}"', fragment)


def update_text_in_para(xml: str, para_id: str, new_text: str) -> str:
    pattern = re.compile(
        rf'(<w:p\b[^>]*w14:paraId="{re.escape(para_id)}"[^>]*>.*?<w:t(?:\s+xml:space="preserve")?>)(.*?)(</w:t>.*?</w:p>)',
        re.DOTALL,
    )

    def _repl(match: re.Match[str]) -> str:
        return match.group(1) + new_text + match.group(3)

    new_xml, n = pattern.subn(_repl, xml, count=1)
    if n != 1:
        raise ValueError(f"No se encontró el texto del párrafo con paraId={para_id}")
    return new_xml


def replace_texts_in_tc(tc_xml: str, new_text: str, clear_rest: bool = False) -> str:
    counter = {"i": 0}

    def _repl(match: re.Match[str]) -> str:
        counter["i"] += 1
        if counter["i"] == 1:
            repl = new_text
        else:
            repl = "" if clear_rest else match.group(2)
        return match.group(1) + repl + match.group(3)

    new_tc, n = re.subn(
        r'(<w:t(?:\s+xml:space="preserve")?>)(.*?)(</w:t>)',
        _repl,
        tc_xml,
        flags=re.DOTALL,
    )
    if n < 1:
        raise ValueError("No se pudo reemplazar el texto de la celda")
    return new_tc


def replace_cell_text(row_xml: str, cell_index: int, new_text: str, clear_rest: bool = False) -> str:
    cells = list(re.finditer(r"<w:tc\b.*?</w:tc>", row_xml, re.DOTALL))
    if cell_index < 1 or cell_index > len(cells):
        raise ValueError(f"Índice de celda inválido: {cell_index}")
    match = cells[cell_index - 1]
    replaced = replace_texts_in_tc(match.group(0), new_text, clear_rest=clear_rest)
    return row_xml[: match.start()] + replaced + row_xml[match.end() :]


def find_movement_rows(xml: str) -> list[re.Match[str]]:
    rows: list[re.Match[str]] = []
    for match in re.finditer(r"<w:tr\b.*?</w:tr>", xml, re.DOTALL):
        row = match.group(0)
        if "«TI»" in row and "{{NSS}}" in row and "{{SAL_BASE}}" in row:
            rows.append(match)
    if not rows:
        raise ValueError("No se encontraron filas plantilla de movimientos")
    return rows


def make_sal_base_paragraph(original_paragraph: str, salario: str) -> str:
    p_open_match = re.match(r'(<w:p\b[^>]*>)', original_paragraph)
    ppr_match = re.search(r'(<w:pPr>.*?</w:pPr>)', original_paragraph, re.DOTALL)
    if not p_open_match or not ppr_match:
        raise ValueError("No se pudo reconstruir el párrafo SAL_BASE")

    p_open = p_open_match.group(1)
    ppr = re.sub(r'(<w:tab\s+w:val="left"\s+w:pos=")\d+("/>)', r'\g<1>360\g<2>', ppr_match.group(1))
    return (
        f"{p_open}{ppr}"
        '<w:r><w:rPr><w:spacing w:val="-10"/><w:sz w:val="16"/></w:rPr>'
        '<w:t xml:space="preserve">$ </w:t><w:tab/>'
        f'<w:t>{salario}</w:t></w:r>'
        "</w:p>"
    )


def build_movement_row(row_template: str, mov: Movimiento) -> str:
    tipo_num = "2" if mov.tipo == "baja" else "8"
    ext = "0" if mov.tipo == "baja" else "1"
    tipo_columna = "2" if mov.tipo == "baja" else "8"

    sal_idx = row_template.find("{{SAL_BASE}}")
    if sal_idx < 0:
        raise ValueError("No se encontró el marcador SAL_BASE en la fila de movimiento")
    sal_start = row_template.rfind("<w:p ", 0, sal_idx)
    sal_end = row_template.find("</w:p>", sal_idx)
    if sal_start < 0 or sal_end < 0:
        raise ValueError("No se pudo delimitar el párrafo SAL_BASE en la fila de movimiento")
    sal_end += len("</w:p>")
    original_paragraph = row_template[sal_start:sal_end]

    row = row_template[:sal_start] + make_sal_base_paragraph(original_paragraph, mov.salario) + row_template[sal_end:]
    row = replace_cell_text(row, 5, ext)
    row = replace_cell_text(row, 7, tipo_columna)
    row = replace_cell_text(row, 10, mov.causa_baja, clear_rest=True)
    row = randomize_ids(row)

    replacements = {
        "«TI»": tipo_num,
        "{{NSS}}": mov.nss,
        "{{NOMBRE}}": mov.nombre,
        "{{FEC_MOV}}": mov.fecha,
        "{{C_BAJA}": mov.causa_baja,
        "{{C_BAJA}}": mov.causa_baja,
    }
    for old, new in replacements.items():
        row = row.replace(old, new)
    return row


def replace_font_equivalents(unpacked_dir: Path) -> None:
    for rel in [Path("word/styles.xml"), Path("word/fontTable.xml")]:
        path = unpacked_dir / rel
        if path.exists():
            text = path.read_text(encoding="utf-8")
            text = text.replace("Arial MT", "Liberation Sans")
            path.write_text(text, encoding="utf-8")


def filename_for_movimientos(movimientos: Iterable[Movimiento]) -> str:
    movs = list(movimientos)
    tipos = {m.tipo for m in movs}
    if len(tipos) == 1:
        tipo = next(iter(tipos))
        prefix = {"alta": "ALTA", "baja": "BAJA", "reingreso": "REINGRESO"}[tipo]
    else:
        prefix = "AFIL"

    nombres = ", ".join(m.nombre for m in movs)
    filename = f"{prefix}_{nombres}.pdf"
    filename = re.sub(INVALID_FILENAME_CHARS, "", filename)
    filename = re.sub(r"\s+", " ", filename).strip()
    return filename


def _candidate_office_paths() -> list[str]:
    candidates: list[str] = []
    env_candidate = os.environ.get("PROCLEAN_LIBREOFFICE")
    if env_candidate:
        candidates.append(env_candidate)

    for binary in ("soffice", "libreoffice"):
        resolved = shutil.which(binary)
        if resolved:
            candidates.append(resolved)

    if os.name == "nt":
        program_files = [
            os.environ.get("PROGRAMFILES"),
            os.environ.get("PROGRAMFILES(X86)"),
            r"C:\Program Files",
            r"C:\Program Files (x86)",
        ]
        suffixes = [
            r"LibreOffice\program\soffice.exe",
            r"LibreOffice\program\swriter.exe",
        ]
        for root in program_files:
            if not root:
                continue
            for suffix in suffixes:
                candidates.append(str(Path(root) / suffix))

    unique: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if item and item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def resolve_libreoffice_command() -> str:
    for candidate in _candidate_office_paths():
        if Path(candidate).exists() or shutil.which(candidate):
            return candidate
    raise RuntimeError(
        "No se encontró LibreOffice. Instálalo o define la variable PROCLEAN_LIBREOFFICE con la ruta de soffice.exe"
    )


def convert_docx_to_pdf(docx_path: Path, outdir: Path) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    lo_home = outdir / ".lo_profile"
    lo_home.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["HOME"] = str(lo_home)

    office_cmd = resolve_libreoffice_command()
    cmd = [
        office_cmd,
        "--headless",
        "--convert-to",
        "pdf",
        "--outdir",
        str(outdir),
        str(docx_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        raise RuntimeError(f"LibreOffice falló al convertir a PDF: {stderr or stdout or 'Error desconocido'}")

    pdf_path = outdir / f"{docx_path.stem}.pdf"
    if not pdf_path.exists():
        raise RuntimeError("LibreOffice terminó sin error, pero no generó el PDF esperado")
    return pdf_path


def resolve_template_path(template_source: Path, movement_count: int) -> Path:
    if movement_count not in TEMPLATE_FILENAMES:
        raise ValueError("Solo se admiten entre 1 y 4 movimientos por constancia")

    if template_source.is_dir():
        candidate = template_source / TEMPLATE_FILENAMES[movement_count]
        if candidate.exists():
            return candidate
        raise FileNotFoundError(f"No se encontró la plantilla para {movement_count} movimiento(s): {candidate}")

    if template_source.exists() and template_source.is_file():
        if movement_count == 1:
            return template_source
        sibling = template_source.with_name(TEMPLATE_FILENAMES[movement_count])
        if sibling.exists():
            return sibling
        raise FileNotFoundError(f"No se encontró la plantilla hermana para {movement_count} movimiento(s): {sibling}")

    raise FileNotFoundError(f"No existe la ruta de plantillas: {template_source}")


def generate_constancia(
    template_path: Path,
    output_dir: Path,
    movimientos: list[Movimiento],
    keep_docx: bool = False,
    fecha_lote: str | None = None,
    hora_lote: str | None = None,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)

    folio = gen_folio()
    lote = gen_lote()
    fecha_lote_date = parse_ymd_date(fecha_lote or datetime.now().strftime('%Y-%m-%d'), field_name='fecha de recepción del lote')
    fecha_lote_str = fecha_lote_date.strftime('%Y-%m-%d') + f" {normalize_hora_lote(hora_lote or datetime.now().strftime('%H:%M'))}"
    huella = gen_huella()
    sello_l1, sello_l2 = gen_sello()

    n_bajas = sum(1 for m in movimientos if m.tipo == "baja")
    n_altas = sum(1 for m in movimientos if m.tipo == "alta")
    n_reing = sum(1 for m in movimientos if m.tipo == "reingreso")
    total = len(movimientos)
    col_reing = n_altas + n_reing
    template_file = resolve_template_path(template_path, total)

    with tempfile.TemporaryDirectory(prefix="imss_afil_") as td:
        workdir = Path(td)
        unpacked = workdir / "unpacked"
        unzip_docx(template_file, unpacked)
        replace_font_equivalents(unpacked)

        document_xml_path = unpacked / "word/document.xml"
        xml = document_xml_path.read_text(encoding="utf-8")

        row_matches = find_movement_rows(xml)
        if len(row_matches) != total:
            raise ValueError(
                f"La plantilla {template_file.name} contiene {len(row_matches)} fila(s) de movimientos y se recibieron {total}."
            )

        pieces: list[str] = []
        cursor = 0
        for match, mov in zip(row_matches, movimientos):
            pieces.append(xml[cursor:match.start()])
            pieces.append(build_movement_row(match.group(0), mov))
            cursor = match.end()
        pieces.append(xml[cursor:])
        xml = "".join(pieces)

        replacements = {
            "{{FOLIO}}": folio,
            "{{LOTE}}": lote,
            "{{FECHA_LOTE}}": fecha_lote_str,
            "{{HUELLA_DIGITAL}}": huella,
            "{{SELLO_LINEA1}}": sello_l1,
            "{{SELLO_LINEA2}}": sello_l2,
            "«BR»": str(n_bajas),
            "«MR»": "0",
            "«RR»": str(col_reing),
            "«TR»": str(total),
            "«BO»": str(n_bajas),
            "«MO»": "0",
            "«RO»": str(col_reing),
            "«TO»": str(total),
        }
        for old, new in replacements.items():
            xml = xml.replace(old, new)

        xml = update_text_in_para(xml, "58AC4CC2", sello_l1)
        xml = update_text_in_para(xml, "744AECC7", sello_l2)
        xml = update_text_in_para(xml, "67D0FE7D", sello_l1)
        xml = update_text_in_para(xml, "3494ED74", sello_l2)

        document_xml_path.write_text(xml, encoding="utf-8")

        pdf_filename = filename_for_movimientos(movimientos)
        base_name = pdf_filename[:-4]
        docx_path = output_dir / f"{base_name}.docx"
        pdf_path = output_dir / pdf_filename

        zip_dir_to_docx(unpacked, docx_path)
        converted_pdf = convert_docx_to_pdf(docx_path, output_dir)

        if converted_pdf != pdf_path:
            if pdf_path.exists():
                pdf_path.unlink()
            converted_pdf.rename(pdf_path)

        if not keep_docx and docx_path.exists():
            docx_path.unlink()

    return {
        "ok": True,
        "pdf": str(pdf_path),
        "docx": str(docx_path) if keep_docx else None,
        "folio": folio,
        "lote": lote,
        "movimientos": total,
        "movimientos_data": json.dumps([m.to_dict() for m in movimientos], ensure_ascii=False),
        "template_used": template_file.name,
    }


def replace_default_template(source_docx: Path, target_docx: Path) -> None:
    target_docx.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_docx, target_docx)
