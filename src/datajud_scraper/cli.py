from __future__ import annotations

import argparse
import json
import sys
from dataclasses import fields
from pathlib import Path

from .config import ScraperConfig
from .errors import ScraperError
from .scraper import ScraperService

CONFIG_HELP = {
    "storage_root": "raiz para PDF, logs y estado (relativa al directorio actual)",
    "user_agent": "identificacion HTTP del cliente",
    "connect_timeout_seconds": "timeout de conexion en segundos",
    "page_timeout_seconds": "timeout entre lecturas de la ficha en segundos",
    "pdf_timeout_seconds": "timeout entre lecturas del PDF en segundos",
    "min_request_interval_seconds": "intervalo minimo entre peticiones en segundos",
    "max_request_jitter_seconds": "espera aleatoria adicional maxima en segundos",
    "max_html_bytes": "tamano maximo de la ficha en bytes",
    "max_pdf_bytes": "tamano maximo del PDF en bytes",
    "min_free_bytes": "espacio libre minimo antes de descargar en bytes",
    "page_attempts": "intentos maximos para obtener la ficha",
    "pdf_attempts": "intentos maximos para descargar el PDF",
    "lock_timeout_seconds": "espera maxima por el bloqueo en segundos",
    "log_max_bytes": "tamano maximo de cada archivo de log en bytes",
    "log_retention_days": "retencion de logs en dias",
    "stale_temp_hours": "edad minima para limpiar temporales en horas",
    "challenge_cooldown_seconds": "pausa tras CAPTCHA o HTTP 403 en segundos",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="datajud-scraper",
        description="Descarga responsable de expedientes publicos PROJUDI/TJBA.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    scrape = subparsers.add_parser("scrape", help="procesar un unico enlace publico")
    scrape.add_argument("url", help="URL AcessoPublico de PROJUDI/TJBA")
    for item in fields(ScraperConfig):
        scrape.add_argument(
            f"--{item.name.replace('_', '-')}",
            type=Path if item.name == "storage_root" else type(item.default),
            default=None,
            help=(
                f"{CONFIG_HELP[item.name]} "
                f"(DATAJUD_{item.name.upper()}; predeterminado: {item.default})"
            ),
        )
    scrape.add_argument(
        "--refresh",
        action="store_true",
        help="descargar y comparar una nueva captura aunque ya exista una valida",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = ScraperConfig.from_env(
            **{item.name: getattr(args, item.name) for item in fields(ScraperConfig)}
        )
        result = ScraperService(config).scrape_url(args.url, refresh=args.refresh)
    except ScraperError as exc:
        payload = {
            "status": "error",
            "error_code": exc.code,
            "message": exc.message,
            "run_id": exc.run_id,
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        print(f"ERROR [{exc.code}]: {exc.message}", file=sys.stderr)
        return exc.exit_code
    except ValueError as exc:
        payload = {
            "status": "error",
            "error_code": "invalid_configuration",
            "message": str(exc),
            "run_id": None,
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        print(f"ERROR [invalid_configuration]: {exc}", file=sys.stderr)
        return 2

    print(result.to_json())
    print(
        f"OK [{result.status}]: {result.process_number}",
        file=sys.stderr,
    )
    return 0
