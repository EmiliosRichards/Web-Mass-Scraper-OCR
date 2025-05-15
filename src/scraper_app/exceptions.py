from datetime import datetime
from typing import Optional, Dict, Any

class ScrapingError(Exception):
    """Base exception for scraping errors."""
    def __init__(self, message: str, error_type: str = "Unknown", details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.error_type = error_type
        self.details = details or {}
        self.timestamp = datetime.now()

class InvalidURLError(ScrapingError):
    """Raised when the URL is invalid."""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, "InvalidURL", details)

class ConnectionError(ScrapingError):
    """Raised when there are connection issues."""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, "Connection", details)

class ParsingError(ScrapingError):
    """Raised when there are issues parsing the page content."""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, "Parsing", details)

class OCRError(ScrapingError):
    """Raised when there are issues with OCR processing."""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, "OCR", details)

class ServerError(ConnectionError):
    """Raised when a server returns an error response (5xx)."""
    def __init__(self, message: str, status_code: int, details: Optional[Dict[str, Any]] = None):
        details = details or {}
        details['status_code'] = status_code
        super().__init__(message, details)
        self.status_code = status_code

class ServiceUnavailableError(ServerError):
    """Raised specifically for HTTP 503 Service Unavailable errors."""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, 503, details)

class RateLimitError(ServerError):
    """Raised when rate limiting is detected (429 Too Many Requests)."""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, 429, details)