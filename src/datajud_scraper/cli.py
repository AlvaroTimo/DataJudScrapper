from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import ScraperConfig
from .errors import ScraperError
from .scraper import ScraperService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="datajud-scraper",
        description="Descarga responsable de expedientes publicos PROJUDI/TJBA.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    scrape = subparsers.add_parser("scrape", help="procesar un unico enlace publico")
    scrape.add_argument("url", help="URL AcessoPublico de PROJUDI/TJBA")
    scrape.add_argument(
        "--storage-root",
        type=Path,
        default=None,
        help=(
            "raiz para pdfs, logs y estado "
            "(default: DATAJUD_STORAGE_ROOT o /mnt/hdd/datajud-scraper)"
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
        config = ScraperConfig.from_env(args.storage_root)
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
