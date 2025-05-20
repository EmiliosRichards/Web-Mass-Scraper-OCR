# Web Scraping System

## Project Overview

This web scraping system is designed to extract data from websites, process it (including OCR on images), and optionally store results in a PostgreSQL database. The system operates synchronously and is configured entirely via an `.env` file for flexibility and ease of use. It allows for dynamic URL selection, comprehensive data extraction, and detailed logging.

## System Architecture

The system consists of several key components:

1.  **URL Selection**: Dynamically selects URLs from a configured `.env` variable (single URL or file path) or optionally from a database.
2.  **Scraping Engine**: Uses Playwright to render web pages and extract content synchronously.
3.  **OCR Processing**: Extracts text from images using Tesseract OCR.
4.  **Data Storage**: Saves extracted data locally to the file system. Optionally, it can update a PostgreSQL database if configured.
5.  **Logging & Monitoring**: Tracks scraping progress and performance metrics with configurable log levels.

### Component Diagram

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   URL Sources   │────▶│  Scraping Engine │────▶│  Data Processing│
│  (.env/DB/File) │     │ (Playwright Sync)│     │  (Text/OCR)     │
└─────────────────┘     └─────────────────┘     └────────┬────────┘
                                                         │
                                                         ▼
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Status Updates │◀────│  Data Storage   │◀────│ Result Analysis │
│  (DB/Logs)      │     │(Files/Optional DB)│     │                 │
└─────────────────┘     └─────────────────┘     └─────────────────┘
```

## Installation

### Prerequisites

- Python 3.8+
- Tesseract OCR engine
- PostgreSQL database (Optional, only if `SCRAPER_USE_DATABASE=True`)

### Setup

1.  Clone the repository:
    ```bash
    git clone <repository-url>
    cd <repository-directory>
    ```

2.  Create a Python virtual environment and activate it (recommended):
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows: venv\Scripts\activate
    ```

3.  Install Python dependencies:
    ```bash
    pip install -r requirements.txt
    ```

4.  Install Tesseract OCR:
    -   **Windows**: Download from [UB-Mannheim/tesseract](https://github.com/UB-Mannheim/tesseract/wiki) and ensure `tesseract.exe` is in your system's PATH.
    -   **Linux**: `sudo apt-get install tesseract-ocr`
    -   **macOS**: `brew install tesseract`

5.  Install Playwright browsers:
    ```bash
    python -m playwright install
    ```

6.  Create a `.env` file in the project root by copying `.env.example` (if available) or creating a new one. Populate it with your configuration:
    ```dotenv
    # --- Scraper Core Settings ---
    SCRAPER_TARGET_URL="https://example.com" # Or "" if using SCRAPER_URL_FILE_PATH or DB
    SCRAPER_URL_FILE_PATH="" # e.g., "input/urls.txt"
    SCRAPER_DEBUG_MODE=False
    SCRAPER_OUTPUT_DIRECTORY="data" # Root for all output, relative to project root or absolute
    SCRAPER_MODE="both"  # 'text', 'ocr', or 'both'
    SCRAPER_RUN_NAME="my_scraping_run" # Optional: custom name for the run folder

    # --- Logging Levels ---
    SCRAPER_CONSOLE_LOG_LEVEL="INFO" # DEBUG, INFO, WARNING, ERROR, CRITICAL
    SCRAPER_FILE_LOG_LEVEL="INFO"    # DEBUG, INFO, WARNING, ERROR, CRITICAL

    # --- Database Interaction Control ---
    SCRAPER_USE_DATABASE=False # True to enable database interactions

    # --- Database Source Settings (only used if SCRAPER_USE_DATABASE=True and SCRAPER_SOURCE_FROM_DB=True) ---
    SCRAPER_SOURCE_FROM_DB=False # True to source URLs from DB
    SCRAPER_DB_NUM_URLS=10
    SCRAPER_DB_RANGE="" # e.g., "1-100"
    SCRAPER_DB_PENDING_BATCH_SIZE=10

    # --- Timeouts and Retries ---
    SCRAPER_IMAGE_TIMEOUT=10
    SCRAPER_IMAGE_RETRY_COUNT=3
    SCRAPER_IMAGE_RETRY_DELAY=1
    SCRAPER_MAX_RETRIES=1 # For page scraping
    SCRAPER_INITIAL_DELAY=1.0
    SCRAPER_BACKOFF_FACTOR=2.0
    SCRAPER_MAX_DELAY=60.0
    SCRAPER_RETRY_JITTER=True

    # --- Rate Limiting ---
    SCRAPER_MAX_REQUESTS_PER_SECOND=2.0
    SCRAPER_RATE_LIMIT_BURST=5

    # --- Database Connection (only if SCRAPER_USE_DATABASE=True) ---
    DB_HOST=localhost
    DB_PORT=5432
    DB_NAME=scraper_db
    DB_USER=scraper_user
    DB_PASSWORD=your_password

    # SCRAPER_ROOT=/path/to/project # Optional: if project root is different from CWD
    ```

## Configuration Options

The system is configured entirely through environment variables defined in a `.env` file in the project root.
Refer to `docs/USAGE.md` for a detailed list of all supported `.env` variables, their purpose, and default values.
Key variables include URL sourcing, database usage, scraping mode, logging levels, and output directories.

## Running the Scraper

Once configured via the `.env` file, run the scraper using:
```bash
python -m src.scraper_app.main
```
The behavior of the scraper will be determined by the settings in your `.env` file.

## Directory Structure

```
.
├── src/                      # Source code
│   ├── scraper_app/          # Core scraper package
│   │   ├── __init__.py       # Package initialization
│   │   ├── config.py         # Configuration settings
│   │   ├── db_utils.py       # Database utilities
│   │   ├── exceptions.py     # Custom exceptions
│   │   ├── logging_utils.py  # Logging utilities
│   │   ├── main.py           # Main entry point
│   │   ├── ocr.py            # OCR processing
│   │   ├── rate_limiter.py   # Rate limiting functionality
│   │   ├── retry.py          # Retry mechanisms
│   │   ├── scraper.py        # Core scraping functionality
│   │   └── url_processor.py  # URL processing utilities
│   └── scripts/              # Utility scripts (if any)
├── docs/                     # Documentation
│   ├── USAGE.md              # Detailed .env configuration guide
│   ├── current_refactoring_summary_YYYY-MM-DD.md # Summary of latest refactoring
│   └── ...                   # Other documentation files
├── data/                     # Default data storage directory (configurable via SCRAPER_OUTPUT_DIRECTORY)
│   └── raw/                  # Raw scraping results
│       └── <run_name_timestamp>/ # Run-specific directory
│           ├── pages/        # Scraped page content per hostname
│           │   └── <hostname>/
│           │       ├── page.html
│           │       ├── text.json
│           │       ├── text.txt
│           │       └── ocr/      # OCR results per image and summary
│           │           ├── ocr_001_....json
│           │           └── summary.json
│           ├── images/       # Downloaded images per hostname
│           │   └── <hostname>/
│           │       └── ...
│           ├── session_details.log # Detailed log for this specific scraping session
│           └── run_summary.json    # JSON summary of this scraping session
├── logs/                     # General log files directory (location of scraper.log)
│   └── scraper.log           # Main application log file
├── tests/                    # Test suite (if any)
├── requirements.txt          # Python dependencies
└── .env                      # Environment variables (user-created)
```

## Database Schema

The system can optionally interact with a PostgreSQL database. If `SCRAPER_USE_DATABASE` is set to `True` in the `.env` file, the following tables are relevant:

### 1. `companies`
Stores company information including website URLs to scrape.
*(Schema details as previously listed, can be truncated for brevity here if preferred, or link to a separate schema doc)*

Key columns: `client_id`, `company_name`, `website`, etc.

### 2. `scraping_logs`
Tracks the scraping process status for each URL when database interaction is enabled.

Columns: `log_id`, `client_id`, `source`, `url_scraped`, `status`, `scraping_date`, `error_message`.

### 3. `scraped_pages`
Stores metadata about scraped pages when database interaction is enabled.

Columns: `page_id`, `client_id`, `url`, `page_type`, `scraped_at`, `raw_html_path`, `plain_text_path`, `summary`, `extraction_notes`.

## Error Handling

The scraper handles various types of errors, including invalid URLs, connection issues, parsing errors, and OCR processing errors. All errors are logged to both the console and the main log file (`logs/scraper.log`), with detailed information.