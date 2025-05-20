# Scraper Usage Guide (.env Configuration)

This document provides detailed information about configuring and running the web scraper using the `.env` file. All command-line arguments have been deprecated in favor of environment variable-based configuration.

## Running the Scraper

Once the `.env` file is configured in the project root, run the scraper using the following command:

```bash
python -m src.scraper_app.main
```

The behavior of the scraper will be entirely determined by the settings in your `.env` file.

## Configuration via `.env` File

Create a `.env` file in the root directory of the project. You can copy `.env.example` (if one exists) or create a new file. Below is a comprehensive list of supported environment variables, their purpose, typical values, and defaults.

### Core Scraping Settings

| Variable                     | Description                                                                 | Example Value                      | Default   |
| :--------------------------- | :-------------------------------------------------------------------------- | :--------------------------------- | :-------- |
| `SCRAPER_TARGET_URL`         | A single URL to scrape. If set, `SCRAPER_URL_FILE_PATH` is ignored.         | `"https://example.com"`            | `""`      |
| `SCRAPER_URL_FILE_PATH`      | Path to a text file containing URLs to scrape (one URL per line).           | `"input/urls_to_scrape.txt"`       | `""`      |
| `SCRAPER_DEBUG_MODE`         | Enable verbose debug logging to console and file.                           | `True` / `False`                   | `False`   |
| `SCRAPER_OUTPUT_DIRECTORY`   | Root directory for all output data. Can be relative or absolute.            | `"output_data"`                    | `"data"`  |
| `SCRAPER_MODE`               | Specifies what content to extract.                                          | `"both"` / `"text"` / `"ocr"`      | `"both"`  |
| `SCRAPER_RUN_NAME`           | Optional custom name for the run-specific output folder. Timestamp is always appended. | `"my_special_run"`               | `""`      |
| `SCRAPER_ROOT`               | Optional: Absolute path to the project root if running from a different CWD. | `"/abs/path/to/project"`         | CWD       |

### Logging Levels

| Variable                        | Description                                      | Example Value        | Default   |
| :------------------------------ | :----------------------------------------------- | :------------------- | :-------- |
| `SCRAPER_CONSOLE_LOG_LEVEL`     | Log level for console output.                    | `"DEBUG"` / `"INFO"` | `"INFO"`  |
| `SCRAPER_FILE_LOG_LEVEL`        | Log level for the main `logs/scraper.log` file.  | `"DEBUG"` / `"INFO"` | `"INFO"`  |
*Valid log levels: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`.*

### Database Interaction

| Variable                         | Description                                                                                                | Example Value    | Default   |
| :------------------------------- | :--------------------------------------------------------------------------------------------------------- | :--------------- | :-------- |
| `SCRAPER_USE_DATABASE`           | Master switch to enable or disable all database interactions (reading and writing).                        | `True` / `False` | `False`   |
| `SCRAPER_SOURCE_FROM_DB`         | If `SCRAPER_USE_DATABASE` is `True`, set this to `True` to source URLs from the database.                   | `True` / `False` | `False`   |
| `SCRAPER_DB_NUM_URLS`            | Number of URLs to fetch from DB if `SCRAPER_SOURCE_FROM_DB` is `True` and `SCRAPER_DB_RANGE` is not set.    | `50`             | `10`      |
| `SCRAPER_DB_RANGE`               | Specify a range of records to fetch from DB (e.g., "1-100"). 1-indexed, inclusive. Overrides `SCRAPER_DB_NUM_URLS`. | `"1-500"`        | `""`      |
| `SCRAPER_DB_PENDING_BATCH_SIZE`  | Batch size for processing pending URLs from the database (if applicable to a separate processing script).    | `20`             | `10`      |

### Timeouts and Retries

| Variable                        | Description                                                     | Example Value | Default   |
| :------------------------------ | :-------------------------------------------------------------- | :------------ | :-------- |
| `SCRAPER_IMAGE_TIMEOUT`         | Timeout for individual image downloads in seconds.              | `15`          | `10`      |
| `SCRAPER_IMAGE_RETRY_COUNT`     | Number of retry attempts for failed image downloads.            | `2`           | `3`       |
| `SCRAPER_IMAGE_RETRY_DELAY`     | Delay in seconds between image download retries.                | `2`           | `1`       |
| `SCRAPER_MAX_RETRIES`           | Max retry attempts for scraping a page if it fails.             | `2`           | `1`       |
| `SCRAPER_INITIAL_DELAY`         | Initial delay (seconds) for page scraping retries.              | `1.5`         | `1.0`     |
| `SCRAPER_BACKOFF_FACTOR`        | Multiplier for page scraping retry delay (e.g., 2 for exponential). | `1.5`         | `2.0`     |
| `SCRAPER_MAX_DELAY`             | Maximum delay (seconds) for page scraping retries.              | `30.0`        | `60.0`    |
| `SCRAPER_RETRY_JITTER`          | Apply jitter to retry delays for page scraping.                 | `True` / `False`| `True`    |

### Rate Limiting

| Variable                            | Description                                       | Example Value | Default   |
| :---------------------------------- | :------------------------------------------------ | :------------ | :-------- |
| `SCRAPER_MAX_REQUESTS_PER_SECOND`   | Max requests per second per hostname for scraping.  | `1.0`         | `2.0`     |
| `SCRAPER_RATE_LIMIT_BURST`          | Maximum burst of requests allowed per hostname.     | `3`           | `5`       |

### Database Connection Details
*(Only used if `SCRAPER_USE_DATABASE=True`)*

| Variable      | Description                  | Example Value        | Default        |
| :------------ | :--------------------------- | :------------------- | :------------- |
| `DB_HOST`     | PostgreSQL database host.    | `"localhost"`        | `"localhost"`  |
| `DB_PORT`     | PostgreSQL database port.    | `5433`               | `5432`         |
| `DB_NAME`     | PostgreSQL database name.    | `"my_scraper_data"`  | `"scraper_db"` |
| `DB_USER`     | PostgreSQL database user.    | `"admin"`            | `"scraper_user"`|
| `DB_PASSWORD` | PostgreSQL database password.| `"securepassword123"`| `"scraper_password"`|

## Example `.env` Configurations

**1. Scrape a single URL, OCR images, no database, debug logging:**
```dotenv
SCRAPER_TARGET_URL="https://en.wikipedia.org/wiki/Web_scraping"
SCRAPER_URL_FILE_PATH=""
SCRAPER_DEBUG_MODE=True
SCRAPER_OUTPUT_DIRECTORY="output/single_run"
SCRAPER_MODE="both"
SCRAPER_RUN_NAME="wiki_scrape_test"
SCRAPER_CONSOLE_LOG_LEVEL="DEBUG"
SCRAPER_FILE_LOG_LEVEL="DEBUG"
SCRAPER_USE_DATABASE=False
```

**2. Scrape URLs from a file, text mode only, use database for results:**
```dotenv
SCRAPER_TARGET_URL=""
SCRAPER_URL_FILE_PATH="input/my_urls.txt"
SCRAPER_DEBUG_MODE=False
SCRAPER_OUTPUT_DIRECTORY="data_collection"
SCRAPER_MODE="text"
SCRAPER_CONSOLE_LOG_LEVEL="INFO"
SCRAPER_FILE_LOG_LEVEL="INFO"
SCRAPER_USE_DATABASE=True
SCRAPER_SOURCE_FROM_DB=False # Not sourcing from DB, only writing
DB_HOST="mypostgres.server.com"
DB_PORT=5432
DB_NAME="production_scrapes"
DB_USER="prod_user"
DB_PASSWORD="verysecret"
```

**3. Scrape URLs sourced from database (first 100), OCR mode, default output:**
```dotenv
SCRAPER_TARGET_URL=""
SCRAPER_URL_FILE_PATH=""
SCRAPER_DEBUG_MODE=False
SCRAPER_MODE="ocr"
SCRAPER_USE_DATABASE=True
SCRAPER_SOURCE_FROM_DB=True
SCRAPER_DB_NUM_URLS=100 # Will fetch first 100 if SCRAPER_DB_RANGE is empty
# SCRAPER_DB_RANGE="1-100" # Alternative to SCRAPER_DB_NUM_URLS
# ... other DB connection details ...
```

## Output Structure

The scraper organizes its output in the following structure (assuming `SCRAPER_OUTPUT_DIRECTORY="data"`):

```
data/
└── raw/                      # Raw scraping results
    └── <run_name_timestamp>/ # Run-specific directory (e.g., my_run_20250520_104500)
        ├── pages/            # Scraped page content per hostname
        │   └── <hostname>/   # e.g., example_com
        │       ├── page.html
        │       ├── text.json # Contains extracted text and metadata
        │       ├── text.txt  # Plain extracted text
        │       └── ocr/      # OCR results
        │           ├── ocr_001_image_filename.json # Detailed OCR for one image
        │           ├── ...
        │           └── summary.json # Summary of OCR for all images on this page
        ├── images/           # Downloaded images per hostname
        │   └── <hostname>/
        │       └── image_filename.jpg
        │       └── ...
        ├── session_details.log # Detailed log for this specific scraping session/run
        └── run_summary.json    # JSON summary of this scraping session/run (aggregates all URLs)
logs/ (at project root)
└── scraper.log               # Main application log file, appended across runs
```

## Logging

The scraper maintains two primary log outputs:

1.  **Main Application Log** (`logs/scraper.log` at the project root):
    *   Contains comprehensive logging information from all runs.
    *   The level of detail written to this file is controlled by `SCRAPER_FILE_LOG_LEVEL`.
    *   Useful for troubleshooting and detailed analysis.

2.  **Session-Specific Log** (`data/raw/<run_name_timestamp>/session_details.log`):
    *   Contains logs specific to a single execution run of the scraper.
    *   Includes a JSON summary of the session and detailed logs of processed URLs, warnings, and errors for that run.

Console output verbosity is controlled by `SCRAPER_CONSOLE_LOG_LEVEL`. Setting `SCRAPER_DEBUG_MODE=True` typically sets both console and file levels to `DEBUG` unless overridden by the specific level settings.

## Error Handling

The scraper is designed to handle various errors gracefully:
- Invalid URLs
- Network connection issues (with retries)
- Page loading timeouts
- Image download failures (with retries)
- OCR processing errors (including unsupported image formats)
- File system errors

All significant events, warnings, and errors are logged to both the console (respecting `SCRAPER_CONSOLE_LOG_LEVEL`) and the main log file (`logs/scraper.log`). Run-specific errors are also captured in the `session_details.log` and `run_summary.json`.