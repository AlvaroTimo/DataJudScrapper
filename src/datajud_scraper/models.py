from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

SuccessStatus = Literal[
    "downloaded",
    "already_exists",
    "unchanged",
    "contracts_preserved",
    "secret_skipped",
    "secrecy_unknown",
]


def isoformat_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class ValidatedUrl:
    canonical_url: str
    codigo_hash: str | None


@dataclass(frozen=True, slots=True)
class CaseMetadata:
    process_number: str
    process_number_digits: str
    process_year: int
    source_url: str
    codigo_hash: str | None
    projudi_internal_id: str | None
    distribution_at: datetime | None
    subject: str | None
    is_secret: bool | None


@dataclass(frozen=True, slots=True)
class PdfValidation:
    sha256: str
    size_bytes: int
    page_count: int


@dataclass(frozen=True, slots=True)
class ScrapeResult:
    status: SuccessStatus
    process_number: str
    distribution_at: datetime | None
    retrieved_at: datetime | None
    subject: str | None
    is_secret: bool | None
    pdf_path: Path | None
    sha256: str | None
    size_bytes: int | None
    page_count: int | None
    run_id: str
    contract_count: int | None = None
    contract_paths: tuple[Path, ...] = ()

    def to_dict(self) -> dict[str, object]:
        result = {
            "status": self.status,
            "process_number": self.process_number,
            "distribution_at": isoformat_utc(self.distribution_at),
            "retrieved_at": isoformat_utc(self.retrieved_at),
            "subject": self.subject,
            "is_secret": self.is_secret,
            "pdf_path": str(self.pdf_path) if self.pdf_path else None,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "page_count": self.page_count,
            "run_id": self.run_id,
        }
        if self.contract_count is not None:
            result["contract_count"] = self.contract_count
            result["contract_paths"] = [str(path) for path in self.contract_paths]
        return result

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)
