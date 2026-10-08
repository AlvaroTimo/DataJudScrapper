from __future__ import annotations


class ScraperError(Exception):
    code = "scraper_error"
    exit_code = 1

    def __init__(self, message: str, *, run_id: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.run_id = run_id


class InvalidInputError(ScraperError):
    code = "invalid_input"
    exit_code = 2


class AccessChallengeError(ScraperError):
    code = "access_challenge"
    exit_code = 3


class FetchError(ScraperError):
    code = "fetch_error"
    exit_code = 3


class ParseError(ScraperError):
    code = "parse_error"
    exit_code = 3


class SessionExpiredError(ScraperError):
    code = "session_expired"
    exit_code = 3


class PdfValidationError(ScraperError):
    code = "pdf_validation_error"
    exit_code = 4


class StorageError(ScraperError):
    code = "storage_error"
    exit_code = 5


class BusyError(StorageError):
    code = "storage_busy"


class ContractProcessingError(ScraperError):
    code = "contract_processing_error"


class IdentityError(ParseError):
    code = "identity_mismatch"


class NotFoundError(FetchError):
    code = "case_not_found"


class PauseError(ScraperError):
    exit_code = 3

    def __init__(self, code: str, message: str, retry_at: float | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.retry_at = retry_at
