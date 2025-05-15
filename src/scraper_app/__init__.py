# Import exceptions first to avoid circular imports
from .exceptions import (
    ScrapingError, InvalidURLError, ConnectionError, ParsingError, OCRError,
    ServerError, ServiceUnavailableError, RateLimitError
)

# config: Configuration constants and directory management
from . import config

# ocr_image: Extract text from images using OCR
from .ocr import ocr_image

# download_image: Download images with retry logic and error handling
# get_safe_filename: Convert URLs to safe, unique filenames
from .utils import download_image, get_safe_filename

# rate_limiter: Rate limiting functionality for requests
from .rate_limiter import get_rate_limiter

# retry: Retry decorator with exponential backoff
from .retry import retry_with_backoff

# logging_utils: Enhanced logging functionality
from .logging_utils import configure_logging, debug, info, warning, error, critical

# scrape_page: Main function to scrape a webpage and extract text/images
from .scraper import scrape_page

# URL processing functions
from .url_processor import process_pending_urls_loop

from typing import List

__all__: List[str] = [
    'scrape_page',           # Main function to scrape a webpage and extract text/images
    'ocr_image',             # Extract text from images using OCR
    'download_image',        # Download images with retry logic and error handling
    'get_safe_filename',     # Convert URLs to safe, unique filenames
    'config',                # Configuration constants and directory management
    'get_rate_limiter',      # Get rate limiter instance for request throttling
    'retry_with_backoff',    # Retry decorator with exponential backoff
    'configure_logging',     # Configure enhanced logging
    'debug', 'info', 'warning', 'error', 'critical',  # Enhanced logging functions
    'ScrapingError',         # Base exception for scraping errors
    'InvalidURLError',       # Exception for invalid URLs
    'ConnectionError',       # Exception for connection issues
    'ParsingError',          # Exception for parsing issues
    'OCRError',              # Exception for OCR issues
    'ServerError',           # Exception for server errors (5xx)
    'ServiceUnavailableError', # Exception for HTTP 503 errors
    'RateLimitError',        # Exception for rate limiting (HTTP 429)
    'process_pending_urls_loop', # Process pending URLs from the database
]
