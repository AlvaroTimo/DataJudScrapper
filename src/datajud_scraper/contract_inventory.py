"""Read every source page and build a resumable, explicitly unreviewed inventory."""

from __future__ import annotations

import argparse
import gzip
import json
import multiprocessing
import os
import re
import time
import unicodedata
import uuid
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path

from .contract_catalog import audit, connect_catalog, initialize_contract_schema
from .pdf_validation import hash_file
from .runtime import utc_now

INVENTORY_VERSION = "2"
TEXT_ENGINE_VERSION = "1"
DEFAULT_TESSDATA = Path.home() / ".local/share/datajud-scraper/tessdata/fast"


def process_start_time(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def recover_inventory_jobs(connection) -> None:
    for row in connection.execute(
        "SELECT document_id,owner_pid,owner_start_time FROM contract_sources "
        "WHERE status='processing' AND owner_pid IS NOT NULL"
    ).fetchall():
        try:
            os.kill(row["owner_pid"], 0)
            alive = True
        except ProcessLookupError:
            alive = False
        except PermissionError:
            alive = True
        if alive and row["owner_start_time"]:
            observed_start = process_start_time(row["owner_pid"])
            if observed_start is not None:
                alive = observed_start == row["owner_start_time"]
        if not alive:
            with connection:
                connection.execute(
                    "UPDATE contract_sources SET status='pending',owner_pid=NULL,"
                    "owner_start_time=NULL WHERE document_id=?",
                    (row["document_id"],),
                )


def reserve_inventory_job(connection, row) -> bool:
    connection.execute("BEGIN IMMEDIATE")
    try:
        current = connection.execute(
            "SELECT d.source_disposition,d.validation_status,s.status,s.inventory_version "
            "FROM documents d LEFT JOIN contract_sources s USING(document_id) "
            "WHERE d.document_id=?",
            (row["document_id"],),
        ).fetchone()
        if (
            current is None
            or current["source_disposition"] != "retained"
            or (current["validation_status"] != "valid")
            or current["status"] == "processing"
            or (
                current["inventory_version"] == INVENTORY_VERSION
                and current["status"] not in (None, "failed", "pending")
            )
        ):
            connection.rollback()
            return False
        connection.execute(
            """INSERT INTO contract_sources(document_id,inventory_version,source_sha256,
            status,page_count,updated_at,owner_pid,owner_start_time)
            VALUES (?,?,?,'processing',?,?,?,?) ON CONFLICT(document_id) DO UPDATE SET
            status='processing',updated_at=excluded.updated_at,owner_pid=excluded.owner_pid,
            owner_start_time=excluded.owner_start_time""",
            (
                row["document_id"],
                INVENTORY_VERSION,
                row["sha256"],
                row["page_count"],
                utc_now().isoformat(),
                os.getpid(),
                process_start_time(os.getpid()),
            ),
        )
        connection.commit()
        return True
    except Exception:
        connection.rollback()
        raise


def normalize(text: str) -> str:
    for _ in range(2):
        if "Ã" not in text and "Â" not in text:
            break
        try:
            text = text.encode("latin1").decode("utf8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            break
    return " ".join(
        "".join(
            char
            for char in unicodedata.normalize("NFKD", text.casefold())
            if not unicodedata.combining(char)
        ).split()
    )


CONTRACT_PATTERNS = {
    "adhesion": r"\b(?:termo|proposta|solicitacao)\s+(?:de\s+)?adesao\b",
    "credit_note": r"\bcedula\s+de\s+credito\s+bancario\b|\bccb\b",
    "loan_agreement": r"\bcontrato\b.{0,100}\b(?:emprestimo|financiamento|credito)\b",
    "credit_instrument": r"\binstrumento\s+particular\b.{0,140}\b(?:credito|emprestimo)\b",
    "card_application": r"\b(?:solicitacao|proposta)\b.{0,80}\bcartao\b",
    "credit_terms": r"\b(?:condicoes\s+gerais|regulamento)\b.{0,160}"
    r"\b(?:credito|cartao|emprestimo|financiamento)\b",
    "credit_form": r"\b(?:emitente|mutuario|tomador)\b.{0,200}\b(?:cpf|credito)\b",
}


def contract_evidence(text: str, *, title: bool = False) -> list[str]:
    normalized = normalize(text)
    found = [name for name, pattern in CONTRACT_PATTERNS.items() if re.search(pattern, normalized)]
    if title and re.search(r"contrat|ades|cedula|\bccb\b|regulamento|condicoes", normalized):
        found.append("title_hint")
    return found


def describe_attachments(document) -> list[dict]:
    """Combine outline boundaries with index-link text; include uncovered prefixes."""
    import pymupdf

    outline = document.get_toc()
    starts: dict[int, dict] = {}
    for _, title, page_number in outline:
        if not 1 <= page_number <= document.page_count:
            continue
        item = starts.setdefault(page_number, {"ids": [], "outline_titles": []})
        item["outline_titles"].append(title)
        match = re.search(r"\bId\.\s*(\d+)", title)
        if match and match[1] not in item["ids"]:
            item["ids"].append(match[1])
    first_content = min(starts, default=min(document.page_count + 1, 21))
    titles: dict[int, list[str]] = defaultdict(list)
    for page_number in range(min(first_content - 1, 20)):
        page = document[page_number]
        words = page.get_text("words", sort=True)
        for link in page.get_links():
            target = link.get("page", -1) + 1
            if link["kind"] != pymupdf.LINK_GOTO or not 1 <= target <= document.page_count:
                continue
            rect = link["from"]
            if page.rect.width * 0.25 <= rect.x0 < page.rect.width * 0.75:
                text = " ".join(
                    word[4]
                    for word in words
                    if rect.x0 - 1 <= (word[0] + word[2]) / 2 <= rect.x1 + 1
                    and rect.y0 <= (word[1] + word[3]) / 2 <= rect.y1
                ).strip()
                if text and text not in titles[target]:
                    titles[target].append(text)
            starts.setdefault(target, {"ids": [], "outline_titles": []})
    starts.setdefault(1, {"ids": [], "outline_titles": []})
    boundaries = sorted(starts)
    attachments = []
    for position, start in enumerate(boundaries, 1):
        end = boundaries[position] - 1 if position < len(boundaries) else document.page_count
        item = starts[start]
        title = " ".join(titles[start]) or " | ".join(item["outline_titles"])
        attachments.append(
            {
                "position": position,
                "start_page": start,
                "end_page": end,
                "source_attachment_id": ",".join(item["ids"]) or None,
                "title": title or ("Indice y portada" if start < first_content else "Sin indice"),
                "index_prefix": start == 1 and first_content > 1 and bool(outline),
                "boundary_ambiguity": len(item["ids"]) > 1,
                "title_evidence": contract_evidence(title, title=True),
                "page_evidence": {},
            }
        )
    return attachments


def page_inventory(page, tessdata: Path) -> dict:
    import pymupdf

    native_words = page.get_text("words", sort=True)
    native_text = page.get_text(sort=True)
    body = [word for word in native_words if word[1] < page.rect.height * 0.94]
    body_chars = sum(len(word[4]) for word in body)
    images = page.get_image_info()
    image_rects = [pymupdf.Rect(image["bbox"]) & page.rect for image in images]
    image_fraction = min(1.0, sum(rect.get_area() for rect in image_rects) / page.rect.get_area())
    unrecognized_image = False
    for rect in image_rects:
        if rect.get_area() < page.rect.get_area() * 0.05:
            continue
        overlapping_chars = sum(
            len(word[4]) for word in body if rect.contains(pymupdf.Rect(word[:4]))
        )
        if overlapping_chars < 80:
            unrecognized_image = True
    needs_ocr = (
        unrecognized_image
        or (image_fraction > 0.1 and body_chars < 300)
        or native_text.count("\ufffd") > max(10, len(native_text) * 0.05)
        or (body_chars < 80 and len(page.get_drawings()) > 80)
    )
    text, words = native_text, native_words
    method, error = "native", None
    if needs_ocr:
        try:
            textpage = page.get_textpage_ocr(
                language="por+eng", dpi=200, full=True, tessdata=str(tessdata)
            )
            text = page.get_text(textpage=textpage, sort=True)
            words = page.get_text("words", textpage=textpage, sort=True)
            method = "ocr"
        except RuntimeError as exc:
            method, error = "ocr_failed", str(exc)[:250]
    return {
        "page_number": page.number + 1,
        "width": page.rect.width,
        "height": page.rect.height,
        "rotation": page.rotation,
        "native_chars": len(native_text),
        "text_chars": len(text),
        "image_fraction": round(image_fraction, 4),
        "image_rects": [list(rect) for rect in image_rects],
        "text_method": method,
        "ocr_error": error,
        "text": text,
        "native_text": native_text if needs_ocr else None,
        "words": [list(word) for word in words],
        "evidence": contract_evidence(text),
        "heading_evidence": contract_evidence(text[:1600]),
    }


def inventory_document(task: dict) -> dict:
    os.environ["OMP_THREAD_LIMIT"] = "1"
    import pymupdf

    path, folder = Path(task["source_path"]), Path(task["folder"])
    if hash_file(path) != task["source_sha256"]:
        raise ValueError("la huella del PDF fuente no coincide con SQLite")
    folder.mkdir(mode=0o750, parents=True, exist_ok=True)
    folder.chmod(0o750)
    pages_file = folder / "pages.jsonl.gz"
    temporary = pages_file.with_suffix(".part")
    cached_pages = {}
    metadata_file = folder / "inventory.json"
    if pages_file.exists() and metadata_file.exists():
        try:
            previous = json.loads(metadata_file.read_text())
            if (
                previous["source_sha256"] == task["source_sha256"]
                and previous["page_count"] == task["page_count"]
                and previous["tessdata"] == task["tessdata"]
                and previous.get("text_engine_version", "1") == TEXT_ENGINE_VERSION
            ):
                with gzip.open(pages_file, "rt", encoding="utf8") as stream:
                    cached_pages = {r["page_number"]: r for r in map(json.loads, stream)}
                if set(cached_pages) != set(range(1, task["page_count"] + 1)):
                    cached_pages = {}
        except (OSError, ValueError, KeyError):
            cached_pages = {}
    try:
        with pymupdf.open(path) as document:
            if document.page_count != task["page_count"]:
                raise ValueError("el numero de paginas no coincide con SQLite")
            attachments = describe_attachments(document)
            attachment_position = 0
            pages = []
            with gzip.open(temporary, "wt", encoding="utf8") as stream:
                temporary.chmod(0o640)
                for page in document:
                    record = cached_pages.get(page.number + 1)
                    if not record or record["ocr_error"]:
                        record = page_inventory(page, Path(task["tessdata"]))
                    else:
                        record["evidence"] = contract_evidence(record["text"])
                        record["heading_evidence"] = contract_evidence(record["text"][:1600])
                    while page.number + 1 > attachments[attachment_position]["end_page"]:
                        attachment_position += 1
                    attachment = attachments[attachment_position]
                    if record["evidence"]:
                        attachment["page_evidence"][record["page_number"]] = record["evidence"]
                    record["attachment_position"] = attachment["position"]
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                    pages.append(
                        {
                            key: record[key]
                            for key in (
                                "page_number",
                                "native_chars",
                                "text_chars",
                                "image_fraction",
                                "text_method",
                                "ocr_error",
                                "evidence",
                            )
                        }
                    )
            os.replace(temporary, pages_file)
            for attachment in attachments:
                attachment["candidate"] = bool(
                    not attachment["index_prefix"]
                    and (attachment["title_evidence"] or attachment["page_evidence"])
                )
            result = {
                "document_id": task["document_id"],
                "source_sha256": task["source_sha256"],
                "inventory_version": INVENTORY_VERSION,
                "text_engine_version": TEXT_ENGINE_VERSION,
                "page_count": document.page_count,
                "pages_file": str(pages_file.resolve()),
                "ocr_pages": sum(p["text_method"] == "ocr" for p in pages),
                "ocr_failures": sum(p["text_method"] == "ocr_failed" for p in pages),
                "attachments": attachments,
                "pages": pages,
                "tessdata": task["tessdata"],
                "review_status": "unreviewed",
            }
            metadata_file.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            metadata_file.chmod(0o640)
            return result
    finally:
        temporary.unlink(missing_ok=True)


def save_inventory(connection, inventory: dict) -> None:
    document_id = inventory["document_id"]
    now = utc_now().isoformat()
    with connection:
        connection.execute(
            """INSERT INTO contract_sources(
            document_id,inventory_version,source_sha256,inventory_path,status,page_count,
            ocr_pages,ocr_failures,attachment_count,candidate_attachment_count,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(document_id) DO UPDATE SET
            inventory_version=excluded.inventory_version,inventory_path=excluded.inventory_path,
            status=excluded.status,ocr_pages=excluded.ocr_pages,ocr_failures=excluded.ocr_failures,
            attachment_count=excluded.attachment_count,
            candidate_attachment_count=excluded.candidate_attachment_count,
            updated_at=excluded.updated_at,error_message=NULL,owner_pid=NULL,owner_start_time=NULL""",
            (
                document_id,
                INVENTORY_VERSION,
                inventory["source_sha256"],
                inventory["pages_file"],
                "ocr_review_needed" if inventory["ocr_failures"] else "inventoried",
                inventory["page_count"],
                inventory["ocr_pages"],
                inventory["ocr_failures"],
                len(inventory["attachments"]),
                sum(a["candidate"] for a in inventory["attachments"]),
                now,
            ),
        )
        for page in inventory["pages"]:
            connection.execute(
                """INSERT INTO contract_pages VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(document_id,page_number) DO UPDATE SET
                text_chars=excluded.text_chars,text_method=excluded.text_method,
                ocr_error=excluded.ocr_error,evidence_json=excluded.evidence_json""",
                (
                    document_id,
                    page["page_number"],
                    page["native_chars"],
                    page["text_chars"],
                    page["image_fraction"],
                    page["text_method"],
                    page["ocr_error"],
                    json.dumps(page["evidence"]),
                ),
            )
        for attachment in inventory["attachments"]:
            attachment_id = str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"{document_id}:attachment:{attachment['position']}")
            )
            connection.execute(
                """INSERT INTO contract_attachments VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(document_id,position) DO UPDATE SET title=excluded.title,
                candidate=excluded.candidate,evidence_json=excluded.evidence_json""",
                (
                    attachment_id,
                    document_id,
                    attachment["position"],
                    attachment["start_page"],
                    attachment["end_page"],
                    attachment["source_attachment_id"],
                    attachment["title"],
                    int(attachment["candidate"]),
                    json.dumps(attachment, ensure_ascii=False),
                ),
            )
        audit(
            connection,
            "inventory_completed",
            document_id=document_id,
            page_count=inventory["page_count"],
            ocr_failures=inventory["ocr_failures"],
        )


def available_documents(connection, batch_id: str) -> list[dict]:
    return [
        dict(row)
        for row in connection.execute(
            """SELECT DISTINCT d.* FROM documents d
        JOIN dataset_records r ON r.case_id=d.case_id
        JOIN batch_items b ON b.record_id=r.record_id
        LEFT JOIN contract_sources s ON s.document_id=d.document_id
        WHERE b.batch_id=? AND d.validation_status='valid'
        AND d.source_disposition='retained'
        AND (s.status IS NULL OR s.status<>'processing')
        AND (s.document_id IS NULL OR s.status IN ('failed','pending') OR s.inventory_version<>?)
        ORDER BY d.retrieved_at""",
            (batch_id, INVENTORY_VERSION),
        )
    ]


def run_inventory(args) -> int:
    root = args.storage_root.resolve()
    connection = connect_catalog(root / "state/scraper.sqlite3")
    initialize_contract_schema(connection)
    if not connection.execute(
        "SELECT 1 FROM batches WHERE batch_id=?", (args.batch_id,)
    ).fetchone():
        raise ValueError("lote desconocido")
    for language in ("por", "eng"):
        if not (args.tessdata / f"{language}.traineddata").is_file():
            raise ValueError(f"falta modelo OCR {language} en {args.tessdata}")
    attempted, active = set(), {}
    failures = 0
    try:
        with ProcessPoolExecutor(
            max_workers=args.workers, mp_context=multiprocessing.get_context("spawn")
        ) as executor:
            while True:
                recover_inventory_jobs(connection)
                for row in available_documents(connection, args.batch_id):
                    if row["document_id"] in attempted or len(active) >= args.workers:
                        continue
                    if not reserve_inventory_job(connection, row):
                        continue
                    attempted.add(row["document_id"])
                    source_path = (root / row["relative_path"]).resolve()
                    if not source_path.is_relative_to(root):
                        raise ValueError("PDF fuera del almacenamiento")
                    task = {
                        "document_id": row["document_id"],
                        "source_path": str(source_path),
                        "source_sha256": row["sha256"],
                        "page_count": row["page_count"],
                        "folder": str(root / "contract-work" / row["document_id"]),
                        "tessdata": str(args.tessdata.resolve()),
                    }
                    active[executor.submit(inventory_document, task)] = task
                if active:
                    done, _ = wait(active, timeout=5, return_when=FIRST_COMPLETED)
                    for future in done:
                        task = active.pop(future)
                        try:
                            inventory = future.result()
                            save_inventory(connection, inventory)
                            print(
                                json.dumps(
                                    {
                                        "event": "inventoried",
                                        **{
                                            key: inventory[key]
                                            for key in (
                                                "document_id",
                                                "page_count",
                                                "ocr_pages",
                                                "ocr_failures",
                                            )
                                        },
                                    }
                                ),
                                flush=True,
                            )
                        except Exception as exc:
                            failures += 1
                            with connection:
                                connection.execute(
                                    """INSERT INTO contract_sources(
                                    document_id,inventory_version,source_sha256,status,page_count,
                                    updated_at,error_message) VALUES (?,?,?,'failed',?,?,?)
                                    ON CONFLICT(document_id) DO UPDATE SET status='failed',
                                    updated_at=excluded.updated_at,
                                    error_message=excluded.error_message,
                                    owner_pid=NULL,owner_start_time=NULL""",
                                    (
                                        task["document_id"],
                                        INVENTORY_VERSION,
                                        task["source_sha256"],
                                        task["page_count"],
                                        utc_now().isoformat(),
                                        str(exc)[:250],
                                    ),
                                )
                            print(
                                json.dumps(
                                    {
                                        "event": "inventory_failed",
                                        "document_id": task["document_id"],
                                        "error": str(exc)[:250],
                                    }
                                ),
                                flush=True,
                            )
                else:
                    remaining = [
                        r
                        for r in available_documents(connection, args.batch_id)
                        if r["document_id"] not in attempted
                    ]
                    if remaining:
                        continue
                    batch = connection.execute(
                        "SELECT status FROM batches WHERE batch_id=?", (args.batch_id,)
                    ).fetchone()
                    other_jobs = connection.execute(
                        "SELECT 1 FROM contract_sources s JOIN documents d USING(document_id) "
                        "JOIN dataset_records r USING(case_id) JOIN batch_items b USING(record_id) "
                        "WHERE b.batch_id=? AND s.status='processing' LIMIT 1",
                        (args.batch_id,),
                    ).fetchone()
                    if not args.watch or (
                        batch[0] not in ("running", "pending") and not other_jobs
                    ):
                        break
                    time.sleep(5)
    finally:
        connection.close()
    return int(failures > 0)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Inventario local de contratos, sin autoaprobar.")
    parser.add_argument("batch_id")
    parser.add_argument("--storage-root", type=Path, default=Path("data"))
    parser.add_argument("--tessdata", type=Path, default=DEFAULT_TESSDATA)
    parser.add_argument("--workers", type=int, choices=range(1, 17), default=6)
    parser.add_argument("--watch", action="store_true")
    return run_inventory(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
