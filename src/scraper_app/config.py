import os
import logging
from pathlib import Path
from typing import Dict, Optional
from datetime import datetime
from urllib.parse import urlparse
from dotenv import load_dotenv

# Load environment variables from .env file
# This should be one of the first things to run
load_dotenv(override=True) # Ensure .env values override system env if conflicts occur

# Get the root directory of the project, allowing override via SCRAPER_ROOT env var
ROOT_DIR = Path(os.getenv('SCRAPER_ROOT', Path.cwd()))

# --- Scraper Core Settings ---
SCRAPER_TARGET_URL: str = os.getenv('SCRAPER_TARGET_URL', "")
SCRAPER_URL_FILE_PATH: str = os.getenv('SCRAPER_URL_FILE_PATH', "")
SCRAPER_DEBUG_MODE: bool = os.getenv('SCRAPER_DEBUG_MODE', 'False').lower() == 'true'
SCRAPER_OUTPUT_DIRECTORY_STR: str = os.getenv('SCRAPER_OUTPUT_DIRECTORY', 'data') # Will be used to define DATA_DIR
SCRAPER_MODE: str = os.getenv('SCRAPER_MODE', 'both') # 'text', 'ocr', or 'both'
SCRAPER_RUN_NAME: str = os.getenv('SCRAPER_RUN_NAME', "")

# --- Logging Levels ---
SCRAPER_CONSOLE_LOG_LEVEL: str = os.getenv('SCRAPER_CONSOLE_LOG_LEVEL', 'INFO').upper()
SCRAPER_FILE_LOG_LEVEL: str = os.getenv('SCRAPER_FILE_LOG_LEVEL', 'INFO').upper()

# --- Database Interaction Control ---
SCRAPER_USE_DATABASE: bool = os.getenv('SCRAPER_USE_DATABASE', 'False').lower() == 'true'

# --- Database Source Settings (only used if SCRAPER_USE_DATABASE is True) ---
SCRAPER_SOURCE_FROM_DB: bool = os.getenv('SCRAPER_SOURCE_FROM_DB', 'False').lower() == 'true'
SCRAPER_DB_NUM_URLS: int = int(os.getenv('SCRAPER_DB_NUM_URLS', '10'))
SCRAPER_DB_RANGE: str = os.getenv('SCRAPER_DB_RANGE', "") # e.g., "0-100"
SCRAPER_DB_PENDING_BATCH_SIZE: int = int(os.getenv('SCRAPER_DB_PENDING_BATCH_SIZE', '10'))

# --- Original settings will follow this block ---
# Timeout in seconds for image download requests
IMAGE_DOWNLOAD_TIMEOUT: int = int(os.getenv('SCRAPER_IMAGE_TIMEOUT', '10'))

# Number of retry attempts if image download fails
IMAGE_RETRY_COUNT: int = int(os.getenv('SCRAPER_IMAGE_RETRY_COUNT', '3'))

# Delay (in seconds) between image download retries
IMAGE_RETRY_DELAY: int = int(os.getenv('SCRAPER_IMAGE_RETRY_DELAY', '1'))

# Rate limiting configuration
MAX_REQUESTS_PER_SECOND: float = float(os.getenv('SCRAPER_MAX_REQUESTS_PER_SECOND', '2.0'))
RATE_LIMIT_BURST: int = int(os.getenv('SCRAPER_RATE_LIMIT_BURST', '5'))  # Maximum burst of requests allowed

# Page scraping retry configuration
SCRAPE_MAX_RETRIES: int = int(os.getenv('SCRAPER_MAX_RETRIES', '1'))
SCRAPE_INITIAL_DELAY: float = float(os.getenv('SCRAPER_INITIAL_DELAY', '1.0'))
SCRAPE_BACKOFF_FACTOR: float = float(os.getenv('SCRAPER_BACKOFF_FACTOR', '2.0'))
SCRAPE_MAX_DELAY: float = float(os.getenv('SCRAPER_MAX_DELAY', '60.0'))
SCRAPE_RETRY_JITTER: bool = os.getenv('SCRAPER_RETRY_JITTER', 'True').lower() == 'true'

# Default file extension for images when no extension is found in URL
DEFAULT_IMAGE_EXTENSION = ".jpg"

# Root directory where all scraper output will be stored.
# Defined using SCRAPER_OUTPUT_DIRECTORY_STR from .env (inserted earlier)
_temp_output_path = Path(SCRAPER_OUTPUT_DIRECTORY_STR)
if _temp_output_path.is_absolute():
    DATA_DIR = _temp_output_path
else:
    DATA_DIR = ROOT_DIR / _temp_output_path
DATA_DIR.mkdir(parents=True, exist_ok=True) # Ensure it exists

# Scraping directory where run-specific folders will be created
SCRAPING_DIR = DATA_DIR / "raw"
SCRAPING_DIR.mkdir(parents=True, exist_ok=True) # Ensure it exists

# Current run directory (will be set when scraping starts)
CURRENT_RUN_DIR = None

# Subdirectory names (used for path construction)
IMAGES_SUBDIR = "images"
PAGES_SUBDIR = "pages"
OCR_SUBDIR = "ocr"
LLM_OUTPUTS_SUBDIR = "llm_outputs"
LLM_OUTPUTS_V2_SUBDIR = "llm_outputs_v2"
LOGS_SUBDIR = "logs"

# Subdirectories for different types of content
IMAGES_DIR = DATA_DIR / IMAGES_SUBDIR
PAGES_DIR = DATA_DIR / PAGES_SUBDIR
LLM_OUTPUTS_DIR = DATA_DIR / LLM_OUTPUTS_SUBDIR
LLM_OUTPUTS_V2_DIR = DATA_DIR / LLM_OUTPUTS_V2_SUBDIR

# Path to the log file where scraper logs will be written
LOG_FILE = ROOT_DIR / LOGS_SUBDIR / "scraper.log"

# Database Configuration (placeholders, use environment variables or a secure config file in production)
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = int(os.getenv('DB_PORT', '5432'))
DB_NAME = os.getenv('DB_NAME', 'scraper_db')
DB_USER = os.getenv('DB_USER', 'scraper_user')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'scraper_password')
def initialize_run_directory(run_name: Optional[str] = None) -> Path:
    """Initialize a new run directory with timestamp and optional name.
    
    Args:
        run_name: Optional name to include in the directory name
        
    Returns:
        Path to the created run directory
    """
    global CURRENT_RUN_DIR
    
    # If CURRENT_RUN_DIR is already set, return it
    if CURRENT_RUN_DIR is not None:
        return CURRENT_RUN_DIR
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    if run_name: # Use SCRAPER_RUN_NAME from config
        # Clean the run name to be filesystem safe
        safe_name = "".join(c for c in run_name if c.isalnum() or c in (' ', '-', '_')).strip()
        safe_name = safe_name.replace(' ', '_')
        dir_name = f"{safe_name}_{timestamp}"
    else:
        dir_name = timestamp
    
    run_dir = SCRAPING_DIR / dir_name
    run_dir.mkdir(parents=True, exist_ok=True)
    
    # Create subdirectories
    (run_dir / 'images').mkdir(exist_ok=True)
    (run_dir / 'pages').mkdir(exist_ok=True)
    
    CURRENT_RUN_DIR = run_dir
    logging.info(f"Initialized run directory: {run_dir}")
    return run_dir

def get_run_directory() -> Path:
    """Get the current run directory, creating it if it doesn't exist.
    
    Returns:
        Path: The path to the current run directory
    """
    if CURRENT_RUN_DIR is None:
        # Pass SCRAPER_RUN_NAME to initialize_run_directory
        return initialize_run_directory(SCRAPER_RUN_NAME if SCRAPER_RUN_NAME else None)
    return CURRENT_RUN_DIR

# The set_output_directory function is removed as DATA_DIR is now configured 
# from .env by SCRAPER_OUTPUT_DIRECTORY_STR at startup.

def ensure_directories(hostname: str | None = None) -> Dict[str, Path]:
    """Ensure all required directories exist for the scraper's operation.
    
    Args:
        hostname: Optional hostname to create hostname-specific directories
        
    Returns:
        Dict mapping directory types to their paths
    """
    try:
        # Create base directories
        base_dirs = [
            IMAGES_DIR,
            PAGES_DIR,
            LLM_OUTPUTS_DIR,
            LLM_OUTPUTS_V2_DIR,
            LOG_FILE.parent
        ]
        
        for directory in base_dirs:
            if not directory.exists():
                directory.mkdir(parents=True, exist_ok=True)
                logging.debug(f"Created directory: {directory}")
            else:
                logging.debug(f"Directory already exists: {directory}")
        
        if hostname:
            # Create hostname-specific directories
            hostname_images_dir = IMAGES_DIR / hostname
            hostname_pages_dir = PAGES_DIR / hostname
            hostname_ocr_dir = hostname_pages_dir / OCR_SUBDIR
            
            for directory in [hostname_images_dir, hostname_pages_dir, hostname_ocr_dir]:
                if not directory.exists():
                    directory.mkdir(parents=True, exist_ok=True)
                    logging.debug(f"Created directory: {directory}")
                else:
                    logging.debug(f"Directory already exists: {directory}")
            
            logging.info(f"Ensured hostname-specific directories for {hostname}")
            logging.debug(f"Images directory: {hostname_images_dir}")
            logging.debug(f"Pages directory: {hostname_pages_dir}")
            logging.debug(f"OCR directory: {hostname_ocr_dir}")
            
            return {
                'images_dir': hostname_images_dir,
                'pages_dir': hostname_pages_dir,
                'ocr_dir': hostname_ocr_dir
            }
        else:
            logging.debug("Ensured base directories only (no hostname specified)")
            return {
                'images_dir': IMAGES_DIR,
                'pages_dir': PAGES_DIR,
                'llm_outputs': LLM_OUTPUTS_DIR,
                'llm_outputs_v2': LLM_OUTPUTS_V2_DIR
            }
            
    except Exception as e:
        logging.error(f"Failed to create directories: {str(e)}")
        raise
