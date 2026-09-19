from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .config import ScraperConfig
from .dataset import DatasetRecord, LoadedDataset, load_dataset, select_records
from .errors import (
    AccessChallengeError,
    FetchError,
    InvalidInputError,
    PauseError,
    ScraperError,
    StorageError,
)
from .models import isoformat_utc
from .runtime import Database, StorageLock, StoragePaths, utc_now
from .scraper import ScraperService


class BatchService:
    def __init__(
        self, config: ScraperConfig | None = None, *, service_factory=ScraperService, progress=None
    ):
        self.config = (config or ScraperConfig.from_env()).normalized()
        self.paths = StoragePaths(self.config.storage_root)
        self.service_factory = service_factory
        self.progress = progress or (lambda event: None)

    def start(
        self,
        path: Path,
        metadata: Path,
        *,
        limit: int | None = 15,
        sample: str = "diverse",
        seed: int = 20260908,
        refresh: bool = False,
    ) -> dict:
        if limit is not None and (type(limit) is not int or limit < 1):
            raise InvalidInputError("limit debe ser un entero positivo")
        if sample not in ("first", "diverse"):
            raise InvalidInputError("sample debe ser first o diverse")
        loaded = load_dataset(path, metadata)  # Entire input is validated before creating storage.
        chosen = select_records(loaded.records, limit, sample, seed)
        self.paths.initialize()
        with StorageLock(self.paths.lock, self.config.lock_timeout_seconds):
            db = Database(self.paths.database)
            try:
                self._import(db, loaded)
                batch_id = str(uuid.uuid4())
                now = isoformat_utc(utc_now())
                with db.connection:
                    db.connection.execute(
                        """INSERT INTO batches(
                        batch_id,dataset_id,status,sample,seed,config_json,created_at,updated_at
                        ) VALUES (?,?,'pending',?,?,?,?,?)""",
                        (
                            batch_id,
                            loaded.dataset_id,
                            sample if limit else "all",
                            seed,
                            json.dumps(asdict(self.config), default=str),
                            now,
                            now,
                        ),
                    )
                    for position, record in enumerate(chosen, 1):
                        db.connection.execute(
                            """INSERT INTO batch_items(batch_id,position,record_id)
                            SELECT ?,?,record_id FROM dataset_records
                            WHERE dataset_id=? AND line_number=?""",
                            (batch_id, position, loaded.dataset_id, record.line_number),
                        )
                self._export(db, batch_id)
                return self._run(db, batch_id, refresh=refresh)
            finally:
                db.close()

    @staticmethod
    def _import(db: Database, dataset: LoadedDataset) -> None:
        if db.connection.execute(
            "SELECT 1 FROM datasets WHERE dataset_id=?", (dataset.dataset_id,)
        ).fetchone():
            return
        now = isoformat_utc(utc_now())
        with db.connection:
            db.connection.execute(
                "INSERT INTO datasets VALUES (?,?,?,?,?,?,?,?)",
                (
                    dataset.dataset_id,
                    dataset.path,
                    dataset.metadata_path,
                    dataset.sha256,
                    dataset.metadata_sha256,
                    dataset.metadata_json,
                    len(dataset.records),
                    now,
                ),
            )
            for record in dataset.records:
                case_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "projudi:" + record.cnj))
                db.connection.execute(
                    """INSERT INTO cases(
                    case_id,process_number,process_number_digits,first_seen_at
                    ) VALUES (?,?,?,?) ON CONFLICT(process_number_digits) DO NOTHING""",
                    (case_id, record.cnj, record.data["process_number_digits"], now),
                )
                actual_id = db.connection.execute(
                    "SELECT case_id FROM cases WHERE process_number_digits=?",
                    (record.data["process_number_digits"],),
                ).fetchone()[0]
                db.connection.execute(
                    """INSERT INTO dataset_records(
                    dataset_id,line_number,case_id,raw_json) VALUES (?,?,?,?)""",
                    (dataset.dataset_id, record.line_number, actual_id, record.raw_json),
                )

    def resume(self, batch_id: str, *, retry_failed: bool = False, refresh: bool = False) -> dict:
        self._require_database(batch_id)
        with StorageLock(self.paths.lock, self.config.lock_timeout_seconds):
            db = Database(self.paths.database)
            try:
                self._batch(db.connection, batch_id)
                self._recover(db, batch_id)
                if retry_failed:
                    with db.connection:
                        db.connection.execute(
                            "UPDATE batch_items SET status='pending',result_json=NULL WHERE "
                            "batch_id=? AND status='failed'",
                            (batch_id,),
                        )
                        db.connection.execute(
                            "UPDATE batches SET infrastructure_failures=0 WHERE batch_id=?",
                            (batch_id,),
                        )
                return self._run(db, batch_id, refresh=refresh)
            finally:
                db.close()

    def status(self, batch_id: str) -> dict:
        self._require_database(batch_id)
        connection = sqlite3.connect(f"file:{self.paths.database.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            return self._summary(connection, batch_id)
        finally:
            connection.close()

    def _require_database(self, batch_id):
        try:
            uuid.UUID(batch_id)
        except ValueError as exc:
            raise InvalidInputError("batch-id invalido") from exc
        if not self.paths.database.is_file():
            raise InvalidInputError("no existe un catalogo en este almacenamiento")

    @staticmethod
    def _batch(connection, batch_id):
        row = connection.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
        if row is None:
            raise InvalidInputError("no existe el lote solicitado")
        return row

    def _recover(self, db, batch_id):
        for row in db.connection.execute(
            "SELECT * FROM runs WHERE batch_id=? AND status IN "
            "('running','interrupted','internal_error')",
            (batch_id,),
        ).fetchall():
            self.paths.remove_managed_file(self.paths.temp_file(row["run_id"]))
            if (
                row["published_path"]
                and not db.connection.execute(
                    "SELECT 1 FROM documents WHERE relative_path=?", (row["published_path"],)
                ).fetchone()
            ):
                self.paths.remove_managed_file(self.paths.absolute(row["published_path"]))
            if row["status"] == "running":
                db.finish_run(
                    row["run_id"], "interrupted", finished_at=utc_now(), error_code="interrupted"
                )
        with db.connection:
            db.connection.execute(
                "UPDATE batch_items SET status='pending' WHERE batch_id=? AND status='running'",
                (batch_id,),
            )

    def _pause(self, db, batch_id, code, retry_at=None):
        with db.connection:
            db.connection.execute(
                "UPDATE batches SET status='paused',stop_reason=?,next_retry_at=?,updated_at=? "
                "WHERE batch_id=?",
                (code, retry_at, isoformat_utc(utc_now()), batch_id),
            )
            if retry_at:
                db.connection.execute(
                    """UPDATE access_state SET blocked_until=MAX(COALESCE(blocked_until,0),?),
                    reason=? WHERE singleton=1""",
                    (retry_at, code),
                )

    def _run(self, db, batch_id, *, refresh):
        batch = self._batch(db.connection, batch_id)
        until = max(batch["next_retry_at"] or 0, db.get_access_blocked_until() or 0)
        if until > time.time():
            self._pause(db, batch_id, batch["stop_reason"] or "access_cooldown", until)
            return self._export(db, batch_id)
        if batch["infrastructure_failures"] >= 5:
            self._pause(db, batch_id, "infrastructure_failures")
            return self._export(db, batch_id)
        service = self.service_factory(self.config)
        service.paths.cleanup_stale_temp(self.config.stale_temp_hours)
        with db.connection:
            db.connection.execute(
                "UPDATE batches SET "
                "status='running',stop_reason=NULL,next_retry_at=NULL,updated_at=? WHERE "
                "batch_id=?",
                (isoformat_utc(utc_now()), batch_id),
            )
        items = db.connection.execute(
            """SELECT b.*,r.line_number,r.raw_json,r.case_id FROM batch_items b
            JOIN dataset_records r ON r.record_id=b.record_id WHERE b.batch_id=? ORDER BY
            b.position""",
            (batch_id,),
        ).fetchall()
        failures = batch["infrastructure_failures"]
        current = None
        run_id = None
        last_report_at = time.monotonic()
        try:
            for item in items:
                if item["status"] == "failed" or (
                    item["status"] in ("secret_skipped", "secrecy_unknown") and not refresh
                ):
                    continue
                current = item
                record = DatasetRecord(
                    item["line_number"], item["raw_json"], json.loads(item["raw_json"])
                )
                run_id = str(uuid.uuid4())
                db.start_run(
                    run_id,
                    hashlib.sha256(record.data["url_download"].encode()).hexdigest(),
                    utc_now(),
                    batch_id=batch_id,
                    position=item["position"],
                    case_id=item["case_id"],
                )
                self.progress(
                    {
                        "event": "record_started",
                        "batch_id": batch_id,
                        "position": item["position"],
                        "total": len(items),
                        "process_number": record.cnj,
                    }
                )
                try:
                    result = service.scrape_record(
                        record, db, item["case_id"], run_id, refresh=refresh
                    ).to_dict()
                    failures = 0
                    db.finish_run(
                        run_id,
                        result["status"],
                        finished_at=utc_now(),
                        bytes_received=result["size_bytes"]
                        if result["status"] != "already_exists"
                        else 0,
                    )
                except (PauseError, AccessChallengeError, StorageError) as exc:
                    retry_at = getattr(exc, "retry_at", None)
                    if isinstance(exc, AccessChallengeError):
                        retry_at = time.time() + self.config.challenge_cooldown_seconds
                    db.finish_run(
                        run_id,
                        "paused",
                        finished_at=utc_now(),
                        error_code=exc.code,
                        error_message=exc.message,
                    )
                    with db.connection:
                        db.connection.execute(
                            "UPDATE batch_items SET status='pending',result_json=? WHERE "
                            "batch_id=? AND position=?",
                            (
                                json.dumps(
                                    {
                                        "status": "pending",
                                        "error_code": exc.code,
                                        "message": exc.message,
                                        "run_id": run_id,
                                    }
                                ),
                                batch_id,
                                item["position"],
                            ),
                        )
                    self._pause(db, batch_id, exc.code, retry_at)
                    return self._export(db, batch_id)
                except ScraperError as exc:
                    result = {
                        "status": "failed",
                        "process_number": record.cnj,
                        "error_code": exc.code,
                        "message": exc.message,
                        "run_id": run_id,
                    }
                    failures = failures + 1 if isinstance(exc, FetchError) else 0
                    db.finish_run(
                        run_id,
                        "failed",
                        finished_at=utc_now(),
                        error_code=exc.code,
                        error_message=exc.message,
                    )
                with db.connection:
                    db.connection.execute(
                        "UPDATE batch_items SET status=?,result_json=? WHERE batch_id=? AND "
                        "position=?",
                        (
                            result["status"],
                            json.dumps(result, ensure_ascii=False),
                            batch_id,
                            item["position"],
                        ),
                    )
                    db.connection.execute(
                        "UPDATE batches SET infrastructure_failures=?,updated_at=? WHERE "
                        "batch_id=?",
                        (failures, isoformat_utc(utc_now()), batch_id),
                    )
                self.progress(
                    {
                        "event": "record_finished",
                        "position": item["position"],
                        "total": len(items),
                        **result,
                    }
                )
                current, run_id = None, None
                if len(items) <= 15 or time.monotonic() - last_report_at >= 60:
                    self._export(db, batch_id)
                    last_report_at = time.monotonic()
                if failures >= 5:
                    self._pause(db, batch_id, "infrastructure_failures")
                    return self._export(db, batch_id)
            with db.connection:
                has_errors = db.connection.execute(
                    "SELECT 1 FROM batch_items WHERE batch_id=? AND status='failed'", (batch_id,)
                ).fetchone()
                db.connection.execute(
                    "UPDATE batches SET status=?,updated_at=? WHERE batch_id=?",
                    (
                        "completed_with_errors" if has_errors else "completed",
                        isoformat_utc(utc_now()),
                        batch_id,
                    ),
                )
        except (KeyboardInterrupt, Exception) as exc:
            # Unknown programming/IO failures stop the batch; do not silently skip a row.
            code = "interrupted" if isinstance(exc, KeyboardInterrupt) else "internal_error"
            if run_id:
                db.finish_run(run_id, code, finished_at=utc_now(), error_code=code)
                self.paths.remove_managed_file(self.paths.temp_file(run_id))
            if current:
                with db.connection:
                    db.connection.execute(
                        "UPDATE batch_items SET status='pending' WHERE batch_id=? AND position=?",
                        (batch_id, current["position"]),
                    )
            self._pause(db, batch_id, code)
            self._export(db, batch_id)
            if not isinstance(exc, KeyboardInterrupt):
                raise
        return self._export(db, batch_id)

    def _summary(self, connection, batch_id):
        batch = self._batch(connection, batch_id)
        counts = dict(
            connection.execute(
                "SELECT status,COUNT(*) FROM batch_items WHERE batch_id=? GROUP BY status",
                (batch_id,),
            ).fetchall()
        )
        metrics = connection.execute(
            """SELECT COUNT(*) AS runs,COALESCE(SUM(bootstrap_attempts),0) AS bootstrap_attempts,
            COALESCE(SUM(page_attempts),0) AS page_attempts,COALESCE(SUM(resolve_attempts),0)
            AS resolve_attempts,
            COALESCE(SUM(pdf_attempts),0) AS pdf_attempts,COALESCE(SUM(session_recoveries),0)
            AS session_recoveries,
            COALESCE(SUM(bytes_received),0) AS bytes_received FROM runs WHERE batch_id=?""",
            (batch_id,),
        ).fetchone()
        folder = self.paths.reports / batch_id
        return {
            "batch_id": batch_id,
            "dataset_id": batch["dataset_id"],
            "status": batch["status"],
            "selected": sum(counts.values()),
            "counts": counts,
            "metrics": dict(metrics),
            "sample": batch["sample"],
            "seed": batch["seed"],
            "created_at": batch["created_at"],
            "updated_at": batch["updated_at"],
            "stop_reason": batch["stop_reason"],
            "next_retry_at": datetime.fromtimestamp(
                batch["next_retry_at"], timezone.utc
            ).isoformat()
            if batch["next_retry_at"]
            else None,
            "results_path": str(folder / "results.jsonl"),
            "report_path": str(folder / "report.md"),
        }

    @staticmethod
    def _write(path, text):
        temp = path.with_suffix(path.suffix + ".tmp")
        fd = os.open(temp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o640)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)

    def _export(self, db, batch_id):
        summary = self._summary(db.connection, batch_id)
        folder = self.paths.reports / batch_id
        self.paths._ensure_directory_chain(folder)
        entries = []
        for item in db.connection.execute(
            """SELECT b.*,r.line_number,r.raw_json,c.process_number,c.source_url,
            c.projudi_internal_id,c.last_checked_at,c.is_secret,c.distribution_at,c.subject
            FROM batch_items b JOIN dataset_records r ON r.record_id=b.record_id JOIN cases c
            ON c.case_id=r.case_id
            WHERE b.batch_id=? ORDER BY b.position""",
            (batch_id,),
        ):
            result = json.loads(item["result_json"]) if item["result_json"] else {}
            history = db.connection.execute(
                "SELECT * FROM runs WHERE batch_id=? AND position=? ORDER BY started_at",
                (batch_id, item["position"]),
            ).fetchall()
            attempts = {
                key: sum(run[key] for run in history)
                for key in (
                    "bootstrap_attempts",
                    "page_attempts",
                    "resolve_attempts",
                    "pdf_attempts",
                    "session_recoveries",
                )
            }
            elapsed = sum(
                (
                    datetime.fromisoformat(run["finished_at"].replace("Z", "+00:00"))
                    - datetime.fromisoformat(run["started_at"].replace("Z", "+00:00"))
                ).total_seconds()
                for run in history
                if run["finished_at"]
            )
            entries.append(
                {
                    "position": item["position"],
                    "line_number": item["line_number"],
                    "process_number": item["process_number"],
                    "status": item["status"],
                    "elapsed_seconds": round(elapsed, 3),
                    "attempts": attempts,
                    "runs": [dict(run) for run in history],
                    "effective_download_url": (
                        "https://projudi.tjba.jus.br/projudi/acoes/DownloadProcesso"
                        f"?numeroProcesso={item['projudi_internal_id']}"
                        if item["projudi_internal_id"]
                        else None
                    ),
                    "original": json.loads(item["raw_json"]),
                    "verified": {
                        "source_url": item["source_url"],
                        "projudi_internal_id": item["projudi_internal_id"],
                        "checked_at": item["last_checked_at"],
                        "is_secret": None if item["is_secret"] is None else bool(item["is_secret"]),
                        "distribution_at": item["distribution_at"],
                        "subject": item["subject"],
                    },
                    "result": result,
                }
            )
        self._write(
            folder / "results.jsonl",
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries),
        )
        self._write(
            folder / "summary.json", json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
        )
        if not (folder / "manifest.json").exists():
            self._write(
                folder / "manifest.json",
                json.dumps(
                    {
                        "dataset_id": summary["dataset_id"],
                        "sample": summary["sample"],
                        "seed": summary["seed"],
                        "records": [
                            {
                                "position": e["position"],
                                "line_number": e["line_number"],
                                "process_number": e["process_number"],
                            }
                            for e in entries
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
            )
        lines = [
            f"# Lote {batch_id}",
            "",
            f"Estado: **{summary['status']}**. Seleccionados: {summary['selected']}.",
            "",
            "## Resultados",
            "",
            "| CNJ | Estado | ID verificado | Paginas | Bytes | Segundos | Intentos PDF |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: |",
        ]
        for e in entries:
            r = e["result"]
            cnj = e["process_number"]
            if r.get("pdf_path"):
                cnj = f"[{cnj}]({os.path.relpath(r['pdf_path'], folder)})"
            lines.append(
                f"| {cnj} | {e['status']} | {e['verified']['projudi_internal_id'] or ''} | "
                f"{r.get('page_count') or ''} | {r.get('size_bytes') or ''} | "
                f"{e['elapsed_seconds']} | {e['attempts']['pdf_attempts']} |"
            )
        lines += [
            "",
            "## Diagnostico",
            "",
            f"Recuentos: `{json.dumps(summary['counts'])}`.",
            f"Intentos acumulados y bytes recibidos: `{json.dumps(summary['metrics'])}`.",
            f"Inicio: {summary['created_at']}; ultima actualizacion: {summary['updated_at']}.",
            f"Pausa: {summary['stop_reason'] or 'ninguna'}; "
            f"reanudacion permitida: {summary['next_retry_at'] or 'sin plazo pendiente'}.",
            "",
            "El limite cuenta registros del manifiesto, no PDF exitosos. No se sustituyen casos.",
            "La validacion automatica comprueba identidad CNJ, formato, estructura, "
            "paginas y SHA-256.",
            "No se afirma que una omision sea una descarga, ni que el PDF incluya "
            "anexos externos o multimedia.",
            "",
            "## Errores por registro",
            "",
        ]
        for e in entries:
            if e["result"].get("error_code"):
                lines.append(
                    f"- {e['process_number']}: {e['result']['error_code']} — "
                    f"{e['result'].get('message', '')}"
                )
        self._write(folder / "report.md", "\n".join(lines) + "\n")
        return summary
