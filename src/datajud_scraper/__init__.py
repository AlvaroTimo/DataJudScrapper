"""Scraper responsable para expedientes publicos de PROJUDI/TJBA."""

from .batch import BatchService
from .config import ScraperConfig
from .models import ScrapeResult
from .scraper import ScraperService

__all__ = ["BatchService", "ScrapeResult", "ScraperConfig", "ScraperService"]
__version__ = "0.1.0"
