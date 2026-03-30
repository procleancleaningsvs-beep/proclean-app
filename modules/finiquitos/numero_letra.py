"""Convierte importe MXN a texto (hasta millones, centavos 00/100)."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


_UNIDADES = (
    "cero",
    "uno",
    "dos",
    "tres",
    "cuatro",
    "cinco",
    "seis",
    "siete",
    "ocho",
    "nueve",
    "diez",
    "once",
    "doce",
    "trece",
    "catorce",
    "quince",
    "dieciséis",
    "diecisiete",
    "dieciocho",
    "diecinueve",
)
_DECENAS = (
    "",
    "",
    "veinte",
    "treinta",
    "cuarenta",
    "cincuenta",
    "sesenta",
    "setenta",
    "ochenta",
    "noventa",
)
_CIENTOS = (
    "",
    "ciento",
    "doscientos",
    "trescientos",
    "cuatrocientos",
    "quinientos",
    "seiscientos",
    "setecientos",
    "ochocientos",
    "novecientos",
)


def _menor_1000(n: int) -> str:
    if n < 20:
        return _UNIDADES[n]
    if n < 100:
        d, u = divmod(n, 10)
        if u == 0:
            return _DECENAS[d]
        if d == 2:
            return f"veinti{_UNIDADES[u]}"
        return f"{_DECENAS[d]} y {_UNIDADES[u]}"
    if n == 100:
        return "cien"
    c, r = divmod(n, 100)
    base = _CIENTOS[c]
    if r == 0:
        return base
    return f"{base} {_menor_1000(r)}"


def _miles(n: int) -> str:
    if n < 1000:
        return _menor_1000(n)
    miles, resto = divmod(n, 1000)
    if miles == 1:
        pref = "mil"
    else:
        pref = f"{_menor_1000(miles)} mil"
    if resto == 0:
        return pref
    return f"{pref} {_menor_1000(resto)}"


def importe_mxn_a_letra(monto: Decimal) -> str:
    """Ej.: 5755.00 -> 'cinco mil setecientos cincuenta y cinco pesos 00/100 M.N.'"""
    monto = monto.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    entero = int(monto)
    centavos = int((monto - entero) * 100)
    if entero == 0:
        letras = "cero"
    elif entero < 1000000:
        letras = _miles(entero)
    else:
        millones, resto = divmod(entero, 1000000)
        if millones == 1:
            p = "un millón"
        else:
            p = f"{_miles(millones)} millones"
        if resto:
            letras = f"{p} {_miles(resto)}"
        else:
            letras = p
    return f"{letras} pesos {centavos:02d}/100 M.N."

