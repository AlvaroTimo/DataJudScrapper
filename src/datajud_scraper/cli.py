from __future__ import annotations

import argparse
import json
import sys
from dataclasses import fields
from pathlib import Path

from .batch import BatchService
from .config import ScraperConfig
from .errors import ScraperError

CONFIG_HELP = {
    "bootstrap_url": "consulta publica utilizada para inicializar cada sesion",
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
    "contract_mode": "procesamiento de contratos: both, extract o none",
    "contract_retention": "conservar fuentes privadas (keep) o purgarlas tras controles (purge)",
    "ocr_workers": "procesos OCR (0: automatico, limitado por CPU y RAM)",
    "ocr_memory_mb": "presupuesto de RAM para estimar el numero de procesos OCR, en MiB",
    "local_model": "modelo visual instalado en Ollama (4b para equipos pequenos)",
    "local_context_tokens": "ventana del modelo local en tokens",
    "local_output_tokens": "limite de salida del modelo local en tokens",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="datajud-scraper",
        description="Descarga responsable de expedientes publicos PROJUDI/TJBA.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    scrape = subparsers.add_parser("scrape-dataset", help="importar el dataset y ejecutar un lote")
    scrape.add_argument("dataset", type=Path, help="dataset JSONL")
    scrape.add_argument("--metadata", type=Path, required=True, help="metadata global JSON")
    selection = scrape.add_mutually_exclusive_group()
    selection.add_argument("--limit", type=int, default=15, help="maximo de registros (15)")
    selection.add_argument("--all", action="store_true", help="procesar todo el dataset")
    scrape.add_argument("--sample", choices=("diverse", "first", "stratified"), default="diverse")
    scrape.add_argument("--seed", type=int, default=20260908)
    resume = subparsers.add_parser("resume", help="reanudar exactamente la seleccion de un lote")
    resume.add_argument("batch_id")
    resume.add_argument("--retry-failed", action="store_true", help="reintentar registros fallidos")
    status = subparsers.add_parser("status", help="consultar el estado sin acceder al portal")
    status.add_argument("batch_id")
    for command in (scrape, resume, status):
        _add_config_arguments(command)
    for command in (scrape, resume):
        command.add_argument(
            "--refresh",
            action="store_true",
            help="verificar y descargar de nuevo aunque ya exista un PDF valido",
        )
    return parser


def _add_config_arguments(parser: argparse.ArgumentParser) -> None:
    for item in fields(ScraperConfig):
        parser.add_argument(
            f"--{item.name.replace('_', '-')}",
            type=Path if item.name == "storage_root" else type(item.default),
            default=None,
            choices={
                "contract_mode": ("both", "extract", "none"),
                "contract_retention": ("keep", "purge"),
            }.get(item.name),
            help=(
                f"{CONFIG_HELP[item.name]} "
                f"(DATAJUD_{item.name.upper()}; predeterminado: {item.default})"
            ),
        )


def _progress(event: dict) -> None:
    kind = event["event"]
    if kind == "record_started":
        state = "iniciando"
    elif kind == "pdf_validated":
        state = f"PDF validado: {event['pages']} paginas"
    elif kind == "inventory_progress":
        state = (
            f"lectura/OCR {event['completed']}/{event['page_total']} paginas "
            f"({event['workers']} procesos, {event['elapsed_seconds']} s)"
        )
    elif kind == "model_started":
        state = f"inferencia local: {event['model']} ({event['task']})"
    elif kind == "model_finished":
        state = f"inferencia finalizada: {event['elapsed_seconds']} s"
    elif kind == "contract_phase_started":
        state = f"{event['phase']}: iniciando"
    elif kind == "contract_phase_finished":
        state = f"{event['phase']}: {event['status']} ({event['elapsed_seconds']} s)"
    else:
        state = event["status"]
        if event.get("timings"):
            timings = event["timings"]
            state += (
                f" (scraper {timings['scraper_seconds']} s, "
                f"contratos {timings['contract_seconds']} s)"
            )
    print(
        f"[{event['position']}/{event['total']}] {event['process_number']}: {state}",
        file=sys.stderr,
        flush=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = ScraperConfig.from_env(
            **{item.name: getattr(args, item.name) for item in fields(ScraperConfig)}
        )
        service = BatchService(config, progress=_progress)
        if args.command == "scrape-dataset":
            result = service.start(
                args.dataset,
                args.metadata,
                limit=None if args.all else args.limit,
                sample=args.sample,
                seed=args.seed,
                refresh=args.refresh,
            )
        elif args.command == "resume":
            result = service.resume(
                args.batch_id, retry_failed=args.retry_failed, refresh=args.refresh
            )
        else:
            result = service.status(args.batch_id)
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

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    print(f"Lote {result['batch_id']}: {result['status']}", file=sys.stderr)
    if args.command != "status":
        if result["status"] == "paused":
            return 130 if result["stop_reason"] == "interrupted" else 3
        if result["status"] == "completed_with_errors":
            return 1
    return 0
