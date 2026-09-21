from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qsl

from .errors import InvalidInputError
from .parsing import _validate_cnj_number
from .url_validation import _validate_common

DOWNLOAD_PATH = "/projudi/acoes/DownloadProcesso"
CASE_PATH = "/projudi/listagens/DadosProcesso"


def _strict_json(text: str):
    def object_hook(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("clave JSON duplicada")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError("constante no valida en JSON")

    return json.loads(text, object_pairs_hook=object_hook, parse_constant=reject_constant)


@dataclass(frozen=True)
class DatasetRecord:
    line_number: int
    raw_json: str
    data: dict

    @property
    def cnj(self) -> str:
        return self.data["process_number"]


@dataclass(frozen=True)
class LoadedDataset:
    dataset_id: str
    path: str
    metadata_path: str
    sha256: str
    metadata_sha256: str
    metadata_json: str
    records: list[DatasetRecord]


def validate_record(data: object, line_number: int, raw_json: str = "") -> DatasetRecord:
    try:
        if not isinstance(data, dict):
            raise ValueError("se esperaba un objeto JSON")
        required = {
            "url_download",
            "process_number",
            "process_number_digits",
            "process_year",
            "source_url",
            "codigo_hash",
            "projudi_internal_id",
            "distribution_at",
            "subject",
            "is_secret",
            "classe",
            "datajud_subjects",
            "orgao_julgador",
            "data_ajuizamento",
            "arquivos",
            "avisos",
        }
        if missing := required - data.keys():
            raise ValueError(f"faltan campos: {', '.join(sorted(missing))}")
        cnj = data["process_number"]
        if not isinstance(cnj, str) or not re.fullmatch(
            r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}", cnj
        ):
            raise ValueError("formato CNJ invalido")
        digits, year = _validate_cnj_number(cnj)
        if data["process_number_digits"] != digits:
            raise ValueError("process_number_digits no coincide con el CNJ")
        if type(data["process_year"]) is not int or data["process_year"] != year:
            raise ValueError("process_year no coincide con el CNJ")
        internal = data["projudi_internal_id"]
        if not isinstance(internal, str) or not re.fullmatch(r"[0-9]{1,20}", internal):
            raise ValueError("projudi_internal_id debe ser una cadena numerica")
        for key, path, value in (
            ("url_download", DOWNLOAD_PATH, cnj),
            ("source_url", CASE_PATH, internal),
        ):
            if not isinstance(data[key], str):
                raise ValueError(f"{key} debe ser texto")
            parsed, _ = _validate_common(data[key])
            if parsed.path != path or parse_qsl(parsed.query, keep_blank_values=True) != [
                ("numeroProcesso", value)
            ]:
                raise ValueError(f"{key} no coincide con la identidad del registro")
        if digits[13:16] != "805":
            raise ValueError("el CNJ no pertenece a TJBA")
        if data["codigo_hash"] is not None:
            raise ValueError("codigo_hash debe ser null para una ficha DadosProcesso")
        if data["is_secret"] is not None and type(data["is_secret"]) is not bool:
            raise ValueError("is_secret debe ser booleano o null")
        for key in ("distribution_at", "data_ajuizamento"):
            if not isinstance(data[key], str):
                raise ValueError(f"{key} debe ser una fecha")
        if datetime.fromisoformat(data["distribution_at"].replace("Z", "+00:00")).tzinfo is None:
            raise ValueError("distribution_at debe incluir zona horaria")
        date.fromisoformat(data["data_ajuizamento"])
        for key in ("subject", "classe", "orgao_julgador"):
            if data[key] is not None and not isinstance(data[key], str):
                raise ValueError(f"{key} debe ser texto o null")
        for key in ("datajud_subjects", "avisos"):
            if not isinstance(data[key], list) or not all(isinstance(v, str) for v in data[key]):
                raise ValueError(f"{key} debe ser una lista de textos")
        files = data["arquivos"]
        if not isinstance(files, dict) or any(
            type(files.get(k)) is not int or files[k] < 0
            for k in ("links_detectados", "restricoes_detectadas")
        ):
            raise ValueError("arquivos debe contener recuentos enteros no negativos")
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise InvalidInputError(f"linea {line_number}: {exc}") from exc
    except Exception as exc:
        # Parser/URL errors have safe messages; never echo a raw input row.
        raise InvalidInputError(f"linea {line_number}: {exc}") from exc
    return DatasetRecord(line_number, raw_json or json.dumps(data, ensure_ascii=False), data)


def load_dataset(path: Path, metadata_path: Path) -> LoadedDataset:
    try:
        content = path.read_bytes()
        metadata_content = metadata_path.read_bytes()
        metadata_text = metadata_content.decode("utf-8-sig")
        try:
            metadata = _strict_json(metadata_text)
        except ValueError as exc:
            raise InvalidInputError("metadata: JSON invalido") from exc
        if not isinstance(metadata, dict) or metadata.get("version_esquema") != 2:
            raise InvalidInputError("metadata: se requiere version_esquema 2")
        records = []
        seen_cnj, seen_id = set(), set()
        try:
            dataset_text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            line = content[: exc.start].count(b"\n") + 1
            raise InvalidInputError(f"linea {line}: UTF-8 invalido") from exc
        for number, line in enumerate(dataset_text.splitlines(), 1):
            if not line.strip():
                raise InvalidInputError(f"linea {number}: linea vacia")
            try:
                data = _strict_json(line)
            except ValueError as exc:
                raise InvalidInputError(f"linea {number}: JSON invalido") from exc
            record = validate_record(data, number, line)
            if record.cnj in seen_cnj or data["projudi_internal_id"] in seen_id:
                raise InvalidInputError(f"linea {number}: CNJ o ID interno duplicado")
            seen_cnj.add(record.cnj)
            seen_id.add(data["projudi_internal_id"])
            records.append(record)
        if not records or metadata.get("total_registros") != len(records):
            raise InvalidInputError("metadata: total_registros no coincide con el JSONL")
        if coverage := metadata.get("cobertura_campos"):
            if not isinstance(coverage, dict):
                raise InvalidInputError("metadata: cobertura_campos debe ser un objeto")
            for field, count in coverage.items():
                if (
                    type(count) is not int
                    or sum(r.data.get(field) is not None for r in records) != count
                ):
                    raise InvalidInputError(f"metadata: cobertura incorrecta para {field}")
        sha = hashlib.sha256(content).hexdigest()
        meta_sha = hashlib.sha256(metadata_content).hexdigest()
        fingerprint = hashlib.sha256(f"{sha}:{meta_sha}".encode()).hexdigest()
        return LoadedDataset(
            fingerprint,
            str(path.resolve()),
            str(metadata_path.resolve()),
            sha,
            meta_sha,
            metadata_text,
            records,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InvalidInputError(f"no se pudo leer el dataset o su metadata: {exc}") from exc


def select_records(
    records: list[DatasetRecord],
    limit: int | None,
    sample: str,
    seed: int,
) -> list[DatasetRecord]:
    if limit is None or sample == "first":
        return records[:] if limit is None else records[:limit]
    if sample == "stratified":
        from .sampling import stratified_records

        return stratified_records(records, limit, seed)
    chosen: list[DatasetRecord] = []
    seen, courts, classes = set(), set(), set()

    def pick(predicate, count):
        for _ in range(count):
            candidates = [r for r in records if r.cnj not in seen and predicate(r.data)]
            if not candidates or len(chosen) >= limit:
                return
            r = min(
                candidates,
                key=lambda r: (
                    r.data["orgao_julgador"] in courts,
                    r.data["classe"] in classes,
                    hashlib.sha256(f"{seed}:{r.cnj}".encode()).hexdigest(),
                ),
            )
            chosen.append(r)
            seen.add(r.cnj)
            courts.add(r.data["orgao_julgador"])
            classes.add(r.data["classe"])

    pick(lambda r: r["subject"] is None, 1)
    pick(lambda r: not r["datajud_subjects"], 1)
    pick(lambda r: r["arquivos"]["links_detectados"] == 0 and bool(r["avisos"]), 1)
    pick(lambda r: r["arquivos"]["links_detectados"] == 0 and not r["avisos"], 2)
    pick(lambda r: r["arquivos"]["links_detectados"] > 0 and bool(r["avisos"]), 2)

    def ordinary(r):
        return (
            r["arquivos"]["links_detectados"] > 0
            and not r["avisos"]
            and r["subject"] is not None
            and bool(r["datajud_subjects"])
        )

    for year, count in ((2026, 4), (2025, 2), (2024, 1)):
        pick(lambda r, y=year: ordinary(r) and r["process_year"] == y, count)
    pick(lambda r: ordinary(r) and r["process_year"] < 2024, 1)
    pick(lambda r: True, limit - len(chosen))
    return chosen
