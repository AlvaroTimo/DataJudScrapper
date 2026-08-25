from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .errors import InvalidInputError
from .models import ValidatedUrl

TRUSTED_HOST = "projudi.tjba.jus.br"
PUBLIC_PATH = "/projudi/AcessoPublico"
TRUSTED_PATH_PREFIX = "/projudi/"
HASH_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _validate_common(url: str) -> tuple[object, str]:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise InvalidInputError("URL mal formada") from exc
    if parsed.scheme.lower() != "https":
        raise InvalidInputError("la URL debe usar HTTPS")
    if parsed.hostname is None or parsed.hostname.lower() != TRUSTED_HOST:
        raise InvalidInputError(f"solo se admite el host {TRUSTED_HOST}")
    if parsed.username or parsed.password or port not in (None, 443):
        raise InvalidInputError("la URL contiene credenciales o un puerto no permitido")
    if parsed.fragment:
        raise InvalidInputError("la URL no debe contener fragmentos")
    return parsed, parsed.hostname.lower()


def validate_input_url(url: str) -> ValidatedUrl:
    parsed, host = _validate_common(url.strip())
    if parsed.path != PUBLIC_PATH:
        raise InvalidInputError(f"la ruta debe ser exactamente {PUBLIC_PATH}")
    params = parse_qsl(parsed.query, keep_blank_values=True)
    if len(params) != 1 or params[0][0] != "codigoHash":
        raise InvalidInputError("la URL debe contener unicamente el parametro codigoHash")
    codigo_hash = params[0][1]
    if not HASH_PATTERN.fullmatch(codigo_hash):
        raise InvalidInputError("codigoHash tiene un formato no permitido")
    canonical = urlunsplit(("https", host, PUBLIC_PATH, urlencode({"codigoHash": codigo_hash}), ""))
    return ValidatedUrl(canonical_url=canonical, codigo_hash=codigo_hash)


def validate_trusted_target(url: str) -> str:
    parsed, host = _validate_common(url)
    if not parsed.path.startswith(TRUSTED_PATH_PREFIX):
        raise InvalidInputError("la redireccion salio del espacio de rutas permitido")
    return urlunsplit(("https", host, parsed.path, parsed.query, ""))
