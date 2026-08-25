from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

DEFAULT_STORAGE_ROOT = Path("/mnt/hdd/datajud-scraper")
DEFAULT_USER_AGENT = "DataJudScraper/0.1 (responsible PROJUDI/TJBA client)"


@dataclass(frozen=True, slots=True)
class ScraperConfig:
    storage_root: Path = DEFAULT_STORAGE_ROOT
    user_agent: str = DEFAULT_USER_AGENT
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

    @classmethod
    def from_env(cls, storage_root: str | Path | None = None) -> ScraperConfig:
        env_root = os.environ.get("DATAJUD_STORAGE_ROOT")
        root = Path(storage_root or env_root or DEFAULT_STORAGE_ROOT)
        user_agent = os.environ.get("DATAJUD_USER_AGENT", DEFAULT_USER_AGENT)
        return cls(storage_root=root, user_agent=user_agent).normalized()

    def normalized(self) -> ScraperConfig:
        root = self.storage_root.expanduser().resolve(strict=False)
        if not root.is_absolute():
            raise ValueError("storage_root debe ser una ruta absoluta")
        if self.min_request_interval_seconds < 0 or self.max_request_jitter_seconds < 0:
            raise ValueError("los intervalos de peticiones no pueden ser negativos")
        if self.max_html_bytes <= 0 or self.max_pdf_bytes <= 0:
            raise ValueError("los limites de tamano deben ser positivos")
        if self.page_attempts < 1 or self.pdf_attempts < 1:
            raise ValueError("debe existir al menos un intento HTTP")
        if self.challenge_cooldown_seconds < 0:
            raise ValueError("el cooldown de desafios no puede ser negativo")
        return replace(self, storage_root=root)
