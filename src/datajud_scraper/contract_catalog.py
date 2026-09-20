"""Additive contract-analysis schema in the existing scraper catalog.

Unknown is deliberately distinct from a manually verified negative. Inventory and
candidate generation never set has_contract / contract_count or approve redactions.
"""

from __future__ import annotations

import json
import sqlite3
from functools import wraps
from pathlib import Path

from .runtime import utc_now

CONTRACT_SCHEMA_VERSION = "5"


def catalog_write(action):
    """Serialize approval checks and writes against release/purge operations."""

    @wraps(action)
    def wrapped(connection, *args, **kwargs):
        connection.execute("BEGIN IMMEDIATE")
        try:
            result = action(connection, *args, **kwargs)
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise

    return wrapped


def connect_catalog(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise ValueError("no existe el catalogo del scraper")
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


def initialize_contract_schema(connection: sqlite3.Connection) -> None:
    version = connection.execute(
        "SELECT value FROM schema_meta WHERE key='contracts_schema_version'"
    ).fetchone()
    if version and version[0] not in ("1", "2", "3", "4", CONTRACT_SCHEMA_VERSION):
        raise ValueError("version de esquema de contratos no soportada")
    connection.execute("BEGIN IMMEDIATE")
    try:
        for table in ("cases", "documents"):
            existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            columns = {
                "has_contract": "INTEGER CHECK (has_contract IN (0,1))",
                "contract_count": "INTEGER CHECK (contract_count >= 0)",
                "contracts_had_sensitive_data": (
                    "INTEGER CHECK (contracts_had_sensitive_data IN (0,1))"
                ),
                "contract_review_status": "TEXT NOT NULL DEFAULT 'unreviewed'",
            }
            if table == "documents":
                columns["source_disposition"] = "TEXT NOT NULL DEFAULT 'retained'"
            for name, declaration in columns.items():
                if name not in existing:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
        statements = [
            """CREATE TABLE IF NOT EXISTS contract_sources (
                document_id TEXT PRIMARY KEY REFERENCES documents(document_id),
                inventory_version TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                inventory_path TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                page_count INTEGER NOT NULL,
                ocr_pages INTEGER NOT NULL DEFAULT 0,
                ocr_failures INTEGER NOT NULL DEFAULT 0,
                attachment_count INTEGER NOT NULL DEFAULT 0,
                candidate_attachment_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                error_message TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS contract_attachments (
                attachment_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL REFERENCES documents(document_id),
                position INTEGER NOT NULL,
                start_page INTEGER NOT NULL CHECK (start_page > 0),
                end_page INTEGER NOT NULL CHECK (end_page >= start_page),
                source_attachment_id TEXT,
                title TEXT,
                candidate INTEGER NOT NULL CHECK (candidate IN (0,1)),
                evidence_json TEXT NOT NULL,
                UNIQUE(document_id, position)
            )""",
            """CREATE TABLE IF NOT EXISTS contract_pages (
                document_id TEXT NOT NULL REFERENCES documents(document_id),
                page_number INTEGER NOT NULL,
                native_chars INTEGER NOT NULL,
                text_chars INTEGER NOT NULL,
                image_fraction REAL NOT NULL,
                text_method TEXT NOT NULL,
                ocr_error TEXT,
                evidence_json TEXT NOT NULL,
                PRIMARY KEY(document_id, page_number)
            )""",
            """CREATE TABLE IF NOT EXISTS contracts (
                contract_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL REFERENCES documents(document_id),
                occurrence INTEGER NOT NULL,
                source_pages_json TEXT NOT NULL,
                kind TEXT NOT NULL,
                extraction_status TEXT NOT NULL DEFAULT 'candidate',
                extraction_path TEXT,
                extraction_sha256 TEXT,
                had_sensitive_data INTEGER CHECK (had_sensitive_data IN (0,1)),
                redaction_status TEXT NOT NULL DEFAULT 'unreviewed',
                cleaned_path TEXT,
                cleaned_sha256 TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(document_id, occurrence)
            )""",
            """CREATE TABLE IF NOT EXISTS contract_page_reviews (
                document_id TEXT NOT NULL REFERENCES documents(document_id),
                page_number INTEGER NOT NULL,
                decision TEXT NOT NULL CHECK (decision IN (
                    'contract','noncontract','mixed','uncertain')),
                reviewer TEXT NOT NULL,
                reviewed_at TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                rendered_sha256 TEXT NOT NULL,
                rationale TEXT NOT NULL,
                PRIMARY KEY(document_id, page_number),
                FOREIGN KEY(document_id,page_number)
                    REFERENCES contract_pages(document_id,page_number)
            )""",
            """CREATE TABLE IF NOT EXISTS contract_redactions (
                redaction_id TEXT PRIMARY KEY,
                contract_id TEXT NOT NULL REFERENCES contracts(contract_id),
                page_number INTEGER NOT NULL,
                rect_json TEXT NOT NULL,
                category TEXT NOT NULL,
                confirmed INTEGER NOT NULL DEFAULT 0 CHECK (confirmed IN (0,1)),
                reviewer TEXT,
                reviewed_at TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS contract_output_reviews (
                contract_id TEXT NOT NULL REFERENCES contracts(contract_id),
                page_number INTEGER NOT NULL,
                original_render_sha256 TEXT NOT NULL,
                cleaned_render_sha256 TEXT NOT NULL,
                masks_sha256 TEXT NOT NULL,
                personal_data_removed INTEGER NOT NULL CHECK (personal_data_removed IN (0,1)),
                contract_content_preserved INTEGER NOT NULL
                    CHECK (contract_content_preserved IN (0,1)),
                reviewer TEXT NOT NULL,
                reviewed_at TEXT NOT NULL,
                rationale TEXT NOT NULL,
                PRIMARY KEY(contract_id,page_number)
            )""",
            """CREATE TABLE IF NOT EXISTS contract_audit (
                event_id INTEGER PRIMARY KEY,
                event TEXT NOT NULL,
                document_id TEXT REFERENCES documents(document_id),
                contract_id TEXT REFERENCES contracts(contract_id),
                created_at TEXT NOT NULL,
                details_json TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS contract_view_artifacts (
                view_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL REFERENCES documents(document_id),
                contract_id TEXT REFERENCES contracts(contract_id),
                scope TEXT NOT NULL,
                page_number INTEGER NOT NULL,
                source_sha256 TEXT NOT NULL,
                image_path TEXT NOT NULL,
                image_sha256 TEXT NOT NULL,
                pixels_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS contract_releases (
                document_id TEXT PRIMARY KEY REFERENCES documents(document_id),
                source_sha256 TEXT NOT NULL,
                manifest_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('purging','complete')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS contract_reference_bodies (
                reference_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL REFERENCES documents(document_id),
                page_number INTEGER NOT NULL,
                source_sha256 TEXT NOT NULL,
                rendered_sha256 TEXT NOT NULL,
                reviewed_at TEXT NOT NULL,
                width INTEGER NOT NULL,
                height INTEGER NOT NULL,
                split_row INTEGER NOT NULL,
                body_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(document_id,page_number,rendered_sha256,reviewed_at,split_row)
            )""",
            """CREATE TABLE IF NOT EXISTS contract_page_review_evidence (
                document_id TEXT NOT NULL,
                page_number INTEGER NOT NULL,
                basis TEXT NOT NULL CHECK (basis='exact_body_match'),
                reference_id TEXT NOT NULL REFERENCES contract_reference_bodies(reference_id),
                source_view_id TEXT NOT NULL REFERENCES contract_view_artifacts(view_id),
                remainder_view_id TEXT NOT NULL REFERENCES contract_view_artifacts(view_id),
                body_sha256 TEXT NOT NULL,
                remainder_pixels_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(document_id,page_number),
                FOREIGN KEY(document_id,page_number)
                    REFERENCES contract_page_reviews(document_id,page_number)
            )""",
            "CREATE INDEX IF NOT EXISTS contract_reference_bodies_hash "
            "ON contract_reference_bodies(body_sha256,width,height,split_row)",
            "CREATE INDEX IF NOT EXISTS contract_attachments_candidate "
            "ON contract_attachments(document_id,candidate)",
            "CREATE INDEX IF NOT EXISTS contracts_document ON contracts(document_id)",
        ]
        for statement in statements:
            connection.execute(statement)
        contract_columns = {row[1] for row in connection.execute("PRAGMA table_info(contracts)")}
        for name, declaration in {
            "source_regions_json": "TEXT NOT NULL DEFAULT '[]'",
            "render_evidence_json": "TEXT",
        }.items():
            if name not in contract_columns:
                connection.execute(f"ALTER TABLE contracts ADD COLUMN {name} {declaration}")
        source_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(contract_sources)")
        }
        for name, declaration in {"owner_pid": "INTEGER", "owner_start_time": "TEXT"}.items():
            if name not in source_columns:
                connection.execute(f"ALTER TABLE contract_sources ADD COLUMN {name} {declaration}")
        connection.execute(
            "INSERT OR REPLACE INTO schema_meta VALUES ('contracts_schema_version',?)",
            (CONTRACT_SCHEMA_VERSION,),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def audit(connection, event, *, document_id=None, contract_id=None, **details):
    connection.execute(
        "INSERT INTO contract_audit(event,document_id,contract_id,created_at,details_json) "
        "VALUES (?,?,?,?,?)",
        (event, document_id, contract_id, utc_now().isoformat(), json.dumps(details)),
    )


def refresh_case_contract_state(connection, case_id: str) -> None:
    """Case flags describe the latest valid source, including verified disposal."""
    latest = connection.execute(
        "SELECT has_contract,contract_count,contracts_had_sensitive_data,contract_review_status "
        "FROM documents WHERE case_id=? AND validation_status='valid' "
        "ORDER BY retrieved_at DESC,document_id DESC LIMIT 1",
        (case_id,),
    ).fetchone()
    values = tuple(latest) if latest else (None, None, None, "unreviewed")
    connection.execute(
        "UPDATE cases SET has_contract=?,contract_count=?,contracts_had_sensitive_data=?,"
        "contract_review_status=? WHERE case_id=?",
        (*values, case_id),
    )


def require_source_review(connection: sqlite3.Connection, document_id: str) -> None:
    source = connection.execute(
        "SELECT * FROM contract_sources WHERE document_id=?", (document_id,)
    ).fetchone()
    if not source or source["status"] != "inventoried" or source["ocr_failures"]:
        raise ValueError("inventario incompleto o con errores de OCR")
    counts = dict(
        connection.execute(
            "SELECT decision,COUNT(*) FROM contract_page_reviews WHERE document_id=? "
            "AND source_sha256=? GROUP BY decision",
            (document_id, source["source_sha256"]),
        )
    )
    if sum(counts.values()) != source["page_count"] or counts.get("uncertain"):
        raise ValueError("faltan paginas por revisar manualmente o resolver")
    # References remain auditable after source disposal, but changing the original
    # manual decision must invalidate every dependent approval.
    from .contract_equivalence import require_current_references

    require_current_references(connection, document_id)


def require_release_reviews(connection: sqlite3.Connection, document_id: str) -> None:
    require_source_review(connection, document_id)
    source = connection.execute(
        "SELECT has_contract,contract_count,contract_review_status FROM documents "
        "WHERE document_id=?",
        (document_id,),
    ).fetchone()
    if source["has_contract"] is None or source["contract_review_status"] != "approved":
        raise ValueError("la extraccion del expediente no esta aprobada")
    contracts = connection.execute(
        "SELECT * FROM contracts WHERE document_id=?", (document_id,)
    ).fetchall()
    if source["contract_count"] != len(contracts) or source["has_contract"] != bool(contracts):
        raise ValueError("recuento de contratos incoherente")
    for contract in contracts:
        if (
            contract["extraction_status"] != "approved"
            or contract["had_sensitive_data"] is None
            or contract["redaction_status"] != "approved"
            or not contract["cleaned_path"]
            or not contract["cleaned_sha256"]
        ):
            raise ValueError("hay contratos sin extraccion o limpieza aprobadas")
        expected = set(range(1, len(json.loads(contract["source_pages_json"])) + 1))
        reviews = connection.execute(
            "SELECT * FROM contract_output_reviews WHERE contract_id=?",
            (contract["contract_id"],),
        ).fetchall()
        if {row["page_number"] for row in reviews} != expected or any(
            not r["personal_data_removed"] or not r["contract_content_preserved"] for r in reviews
        ):
            raise ValueError("faltan paginas limpias por revisar manualmente")
        evidence = json.loads(contract["render_evidence_json"] or "{}")
        current_pages = {page["page_number"]: page for page in evidence.get("pages", [])}
        if set(current_pages) != expected:
            raise ValueError("faltan huellas de la revision actual")
        for review in reviews:
            page = current_pages[review["page_number"]]
            if (
                review["original_render_sha256"] != page["original_pixels_sha256"]
                or review["cleaned_render_sha256"] != page["cleaned_pixels_sha256"]
                or review["masks_sha256"] != page["masks_sha256"]
            ):
                raise ValueError("la revision manual corresponde a otra version del contrato")
