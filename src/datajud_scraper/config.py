from __future__ import annotations

import math
import os
from dataclasses import dataclass, fields, replace
from pathlib import Path

DEFAULT_STORAGE_ROOT = Path("data")
DEFAULT_USER_AGENT = "DataJudScraper/0.1 (responsible PROJUDI/TJBA client)"


@dataclass(frozen=True, slots=True)
class ScraperConfig:
    storage_root: Path = DEFAULT_STORAGE_ROOT
    user_agent: str = DEFAULT_USER_AGENT
    bootstrap_url: str = "https://projudi.tjba.jus.br/projudi/AcessoPublico?codigoHash=22f20646"
    connect_timeout_seconds: float = 10.0
    page_timeout_seconds: float = 30.0
    pdf_timeout_seconds: float = 300.0
    min_request_interval_seconds: float = 3.0
    max_request_jitter_seconds: float = 2.0
    max_html_bytes: int = 20 * 1024 * 1024
    max_pdf_bytes: int = 1024 * 1024 * 1024
    min_free_bytes: int = 1024 * 1024 * 1024
    page_attempts: int = 3
    pdf_attempts: int = 2
    lock_timeout_seconds: float = 10.0
    log_max_bytes: int = 50 * 1024 * 1024
    log_retention_days: int = 30
    stale_temp_hours: int = 24
    challenge_cooldown_seconds: int = 3600
    contract_mode: str = "both"
    contract_retention: str = "purge"

    @classmethod
    def from_env(
        cls,
        storage_root: str | Path | None = None,
        **overrides: str | int | float | None,
    ) -> ScraperConfig:
        """Resolve explicit options, then DATAJUD_* variables, then defaults."""
        options = {"storage_root": storage_root, **overrides}
        known = {item.name for item in fields(cls)}
        unknown = options.keys() - known
        if unknown:
            raise ValueError(f"configuracion desconocida: {', '.join(sorted(unknown))}")
        values = {}
        for item in fields(cls):
            variable = f"DATAJUD_{item.name.upper()}"
            value = options.get(item.name)
            source = f"--{item.name.replace('_', '-')}"
            if value is None:
                value = os.environ.get(variable)
                source = variable
            if value is None:
                continue
            converter = Path if item.name == "storage_root" else type(item.default)
            try:
                if isinstance(value, str) and not value.strip():
                    raise ValueError("valor vacio")
                values[item.name] = (
                    converter(value)
                    if isinstance(value, str) or item.name == "storage_root"
                    else value
                )
            except (ValueError, TypeError, OverflowError) as exc:
                raise ValueError(f"valor invalido para {source}") from exc
        return cls(**values).normalized()

    def normalized(self) -> ScraperConfig:
        from .errors import InvalidInputError
        from .url_validation import validate_input_url

        try:
            validate_input_url(self.bootstrap_url)
        except InvalidInputError as exc:
            raise ValueError(f"bootstrap_url invalida: {exc}") from exc
        root = self.storage_root.expanduser().resolve(strict=False)
        if self.contract_mode not in ("both", "extract", "none"):
            raise ValueError("contract_mode debe ser both, extract o none")
        if self.contract_retention not in ("keep", "purge"):
            raise ValueError("contract_retention debe ser keep o purge")
        if not self.user_agent.strip() or any(
            ord(char) < 32 or ord(char) > 126 for char in self.user_agent
        ):
            raise ValueError("user_agent debe ser texto ASCII no vacio sin caracteres de control")
        positive = {
            "connect_timeout_seconds",
            "page_timeout_seconds",
            "pdf_timeout_seconds",
            "max_html_bytes",
            "max_pdf_bytes",
            "page_attempts",
            "pdf_attempts",
            "log_max_bytes",
        }
        for item in fields(self):
            if not isinstance(item.default, (int, float)):
                continue
            value = getattr(self, item.name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or (isinstance(item.default, int) and not isinstance(value, int))
                or (isinstance(value, float) and not math.isfinite(value))
            ):
                raise ValueError(f"{item.name} debe ser un numero finito del tipo correcto")
            if value < 0 or (item.name in positive and value == 0):
                bound = "positivo" if item.name in positive else "mayor o igual a cero"
                raise ValueError(f"{item.name} debe ser {bound}")
        return replace(self, storage_root=root)
