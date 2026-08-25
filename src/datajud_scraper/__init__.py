"""Scraper responsable para expedientes publicos de PROJUDI/TJBA."""

from .config import ScraperConfig
from .models import ScrapeResult
from .scraper import ScraperService, scrape_url

__all__ = ["ScrapeResult", "ScraperConfig", "ScraperService", "scrape_url"]
__version__ = "0.1.0"
