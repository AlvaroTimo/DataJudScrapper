"""Small shared primitives; exported reports never contain OCR or personal values."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..pdf_validation import hash_file


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf8"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf8") as out:
        os.chmod(temporary, 0o600)
        json.dump(value, out, ensure_ascii=False, indent=2)
        out.write("\n")
        out.flush()
        os.fsync(out.fileno())
    temporary.replace(path)


def readonly(path):
    connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def workspace(root):
    return Path(root).resolve() / "adhesion-v1"


def source_path(root, source):
    path = (Path(root) / source["relative_path"]).resolve()
    if not path.is_relative_to(Path(root).resolve() / "pdfs"):
        raise ValueError("source outside original PDF storage")
    if not path.is_file() or hash_file(path) != source["sha256"]:
        raise ValueError("source missing or changed")
    return path
