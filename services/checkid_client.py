"""
Cliente HTTP para la API de CheckID (https://www.checkid.mx/api).
Sin login web ni scraping: solo llamadas REST con ApiKey.

Códigos internos (transporte/config): CONFIG_ERROR, TIMEOUT, NETWORK_ERROR, VALIDATION_ERROR.
Códigos de negocio CheckID (E100…E903) se devuelven tal cual vienen en la respuesta.

Prueba manual (con sesión iniciada en la app; sustituir COOKIE y host):

    # Búsqueda por CURP (JSON)
    curl -s -X POST http://127.0.0.1:5000/api/checkid/buscar \\
      -H "Content-Type: application/json" \\
      -H "Cookie: session=..." \\
      -d '{"curp":"CURP18CARACTERES"}'

    # Búsqueda por RFC
    curl -s -X POST http://127.0.0.1:5000/api/checkid/buscar \\
      -H "Content-Type: application/json" \\
      -H "Cookie: session=..." \\
      -d '{"rfc":"RFC12CARACTER"}'

    # Solicitudes restantes (solo admin)
    curl -s http://127.0.0.1:5000/api/checkid/solicitudes-restantes \\
      -H "Cookie: session=..."

Nunca se registra en logs el cuerpo de la petición ni la ApiKey; solo término,
status HTTP y codigoError cuando aplica.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any
from urllib.parse import urljoin

import requests

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://www.checkid.mx/api"

# Textos de apoyo cuando la API no envía mensaje (no sustituyen el código E*).
CHECKID_ERROR_MESSAGES: dict[str, str] = {
    "E100": "Solicitud inválida o parámetros incorrectos.",
    "E101": "ApiKey ausente o no válida.",
    "E200": "No se encontraron resultados para el término de búsqueda.",
    "E201": "Formato de RFC o CURP no válido.",
    "E202": "Límite de solicitudes alcanzado o consulta no permitida en el plan actual.",
    "E900": "Error interno del servicio CheckID.",
    "E901": "Servicio temporalmente no disponible.",
    "E902": "Tiempo de espera agotado al contactar CheckID.",
    "E903": "Error de comunicación con CheckID.",
}


class CheckIDConfigurationError(RuntimeError):
    """Falta CHECKID_API_KEY o configuración mínima."""


class CheckIDClientError(Exception):
    """Error interno de transporte (no confundir con códigos E* de CheckID)."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        http_status: int | None,
        raw: dict[str, Any],
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.raw = raw


def normalize_termino_busqueda(value: str) -> str:
    """
    Normaliza el término enviado a CheckID como `TerminoBusqueda`.

    - Convierte a mayúsculas.
    - Elimina todos los espacios (incluidos internos) para RFC/CURP compactos.
    - Cadena vacía tras normalizar se interpreta como “sin término”.
    """
    s = (value or "").strip()
    if not s:
        return ""
    return "".join(s.split()).upper()


def normalize_base_url(url: str) -> str:
    """
    Normaliza `CHECKID_BASE_URL` para construir URLs con `urljoin` sin `//` duplicados.

    - Trunca barras duplicadas en la parte del host/ruta (después de `://`).
    - Garantiza exactamente una barra final (`/`), coherente con `urljoin` + path relativo.
    """
    u = (url or DEFAULT_BASE_URL).strip()
    if "://" in u:
        scheme, rest = u.split("://", 1)
        rest = re.sub(r"/+", "/", rest).strip("/")
        u = f"{scheme}://{rest}"
    else:
        u = re.sub(r"/+", "/", u).strip("/")
    return u.rstrip("/") + "/"


def _normalize_error_code(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in (
        "CodigoError",
        "codigoError",
        "Codigo",
        "codigo",
        "ErrorCode",
        "errorCode",
        "Code",
        "code",
    ):
        val = payload.get(key)
        if val is None:
            continue
        s = str(val).strip().upper()
        if s.startswith("E") and len(s) >= 4:
            return s
    return None


def _message_from_checkid_payload(payload: dict[str, Any], code: str | None) -> str:
    for k in ("Mensaje", "mensaje", "Message", "message", "Descripcion", "descripcion"):
        v = payload.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    if code and code in CHECKID_ERROR_MESSAGES:
        return CHECKID_ERROR_MESSAGES[code]
    return "Respuesta CheckID sin mensaje descriptivo."


def _default_request_timeout() -> float:
    """Timeout HTTP para `requests` (segundos). Configurable con CHECKID_REQUEST_TIMEOUT."""
    return max(5.0, float(os.environ.get("CHECKID_REQUEST_TIMEOUT", "45")))


class CheckIDClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.api_key = (api_key or os.environ.get("CHECKID_API_KEY") or "").strip()
        self.base_url = normalize_base_url(base_url or os.environ.get("CHECKID_BASE_URL") or DEFAULT_BASE_URL)
        self.timeout = float(timeout) if timeout is not None else _default_request_timeout()
        if not self.api_key:
            raise CheckIDConfigurationError("Defina la variable de entorno CHECKID_API_KEY.")

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _post_json(self, path: str, body: dict[str, Any]) -> tuple[int, Any]:
        # No loguear `body`: contiene ApiKey. Los logs van en buscar/solicitudes_restantes.
        # Una sola llamada requests.post (sin adaptador Retry ni sesión con reintentos).
        url = urljoin(self.base_url, path.lstrip("/"))
        connect_timeout = max(2.0, float(os.environ.get("CHECKID_CONNECT_TIMEOUT", "10")))
        read_timeout = self.timeout
        try:
            resp = requests.post(
                url,
                json=body,
                headers=self._headers(),
                timeout=(connect_timeout, read_timeout),
            )
        except requests.exceptions.Timeout as exc:
            raise CheckIDClientError(
                code="TIMEOUT",
                message="Tiempo de espera agotado al contactar CheckID.",
                http_status=None,
                raw={"detail": str(exc)},
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise CheckIDClientError(
                code="NETWORK_ERROR",
                message="Error de red al contactar CheckID.",
                http_status=None,
                raw={"detail": str(exc)},
            ) from exc

        try:
            data = resp.json()
        except ValueError:
            data = {"raw_text": resp.text[:2000] if resp.text else None}

        return resp.status_code, data

    def build_busqueda_body(self, termino_busqueda: str) -> dict[str, Any]:
        termino = normalize_termino_busqueda(termino_busqueda)
        return {
            "ApiKey": self.api_key,
            "TerminoBusqueda": termino,
            "ObtenerRFC": True,
            "ObtenerCURP": True,
            "Obtener69o69B": True,
            "ObtenerNSS": True,
            "ObtenerRegimenFiscal": True,
            "ObtenerCP": True,
        }

    def _result_internal(
        self,
        *,
        error_code: str,
        message: str,
        http_status: int | None,
        data: Any,
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "internal": True,
            "error_code": error_code,
            "message": message,
            "http_status": http_status,
            "data": data if isinstance(data, dict) else data,
        }

    def _result_checkid_error(
        self,
        *,
        code: str,
        message: str,
        http_status: int,
        data: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "internal": False,
            "error_code": code,
            "message": message,
            "http_status": http_status,
            "data": data,
        }

    def _result_ok(self, http_status: int, data: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "internal": False,
            "error_code": None,
            "message": None,
            "http_status": http_status,
            "data": data,
        }

    def buscar(self, termino_busqueda: str) -> dict[str, Any]:
        """
        Llama a `POST …/Busqueda` con flags de obtención (RFC, CURP, NSS, etc.).

        Devuelve siempre un dict con claves: ok, internal, error_code, message,
        http_status, data (mismo formato que el resto de respuestas del cliente).

        Errores de red/timeout usan códigos internos (TIMEOUT, NETWORK_ERROR).
        Respuestas con código de negocio CheckID conservan el E* en `error_code`.
        """
        termino = normalize_termino_busqueda(termino_busqueda)
        if not termino:
            return self._result_internal(
                error_code="VALIDATION_ERROR",
                message="TerminoBusqueda requerido (RFC o CURP).",
                http_status=None,
                data=None,
            )

        try:
            status, data = self._post_json("Busqueda", self.build_busqueda_body(termino))
        except CheckIDClientError as exc:
            logger.warning(
                "checkid Busqueda termino_busqueda=%s status=%s codigoError=%s",
                termino,
                exc.http_status,
                exc.code,
            )
            return self._result_internal(
                error_code=exc.code,
                message=exc.message,
                http_status=exc.http_status,
                data=exc.raw,
            )

        code = _normalize_error_code(data)
        logger.info(
            "checkid Busqueda termino_busqueda=%s status_code=%s codigoError=%s",
            termino,
            status,
            code,
        )

        if isinstance(data, dict):
            if code:
                msg = _message_from_checkid_payload(data, code)
                return self._result_checkid_error(
                    code=code,
                    message=msg,
                    http_status=status,
                    data=data,
                )
            if status >= 400:
                fb = "E900"
                msg = _message_from_checkid_payload(data, fb)
                return self._result_checkid_error(
                    code=fb,
                    message=msg,
                    http_status=status,
                    data=data,
                )
            return self._result_ok(status, data)

        if status >= 400:
            return self._result_checkid_error(
                code="E900",
                message=CHECKID_ERROR_MESSAGES["E900"],
                http_status=status,
                data={"raw": data},
            )
        return self._result_ok(status, data)

    def solicitudes_restantes(self) -> dict[str, Any]:
        """
        Llama a `POST …/SolicitudesRestantes` con `ApiKey` solo en el cuerpo JSON.

        Misma forma de respuesta que `buscar`. No se loguea la ApiKey.
        """
        body = {"ApiKey": self.api_key}
        try:
            status, data = self._post_json("SolicitudesRestantes", body)
        except CheckIDClientError as exc:
            logger.warning(
                "checkid SolicitudesRestantes status=%s codigoError=%s",
                exc.http_status,
                exc.code,
            )
            return self._result_internal(
                error_code=exc.code,
                message=exc.message,
                http_status=exc.http_status,
                data=exc.raw,
            )

        code = _normalize_error_code(data)
        logger.info(
            "checkid SolicitudesRestantes status_code=%s codigoError=%s",
            status,
            code,
        )

        if isinstance(data, dict):
            if code:
                msg = _message_from_checkid_payload(data, code)
                return self._result_checkid_error(
                    code=code,
                    message=msg,
                    http_status=status,
                    data=data,
                )
            if status >= 400:
                fb = "E900"
                msg = _message_from_checkid_payload(data, fb)
                return self._result_checkid_error(
                    code=fb,
                    message=msg,
                    http_status=status,
                    data=data,
                )
            return self._result_ok(status, data)

        if status >= 400:
            return self._result_checkid_error(
                code="E900",
                message=CHECKID_ERROR_MESSAGES["E900"],
                http_status=status,
                data={"raw": data},
            )
        return self._result_ok(status, data)
