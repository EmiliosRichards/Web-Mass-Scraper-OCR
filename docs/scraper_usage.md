# Scraper Usage Guide

This document provides detailed information about the command-line arguments and usage examples for the web scraper.

## Command-Line Arguments

### Basic Arguments

| Argument | Description | Required | Default |
|----------|-------------|----------|---------|
| `url_pos` | URL to scrape (positional argument) | Yes* | - |
| `--url` | URL to scrape (alternative to positional argument) | Yes* | - |
| `--url-file` | Path to file containing URLs to scrape (one per line) | Yes* | - |

*Note: One of `url_pos`, `--url`, or `--url-file` must be provided.

### Optional Arguments

| Argument | Description | Default | Choices |
|----------|-------------|---------|---------|
| `--scrape-mode` | What to scrape | 'both' | 'text', 'ocr', 'both' |
| `--output-dir` | Custom directory for output files | './data/scraping/' | - |
| `--log-level` | Set logging level | 'DEBUG' | 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL' |
| `--run-name` | Name for this scraping run | - | - |
| `--debug` | Enable debug mode | False | True, False |
| `--from-db` | Fetch URLs from the database | False | - |
| `--num-urls` | Number of URLs to fetch from the database (used as limit if `--db-range` not set) | 10 | - |
| `--db-range` | Specify a range of records to fetch (e.g., "1-100"). 1-indexed, inclusive. Overrides --num-urls if used with --from-db. | None | - |


## Usage Examples

### Basic Usage

1. Scrape a single URL:
```bash
python -m src.scraper_app.main --url "https://example.com"
```

2. Scrape multiple URLs from a file:
```bash
python -m src.scraper_app.main --url-file urls.txt
```

## Debug Mode

The scraper supports two modes of operation:

1. **Normal Mode** (default):
   - Minimal console output
   - Only shows warnings and errors
   - All logs are still saved to the log file
   ```bash
   python -m src.scraper_app.main --url "https://example.com"
   ```

2. **Debug Mode**:
   - Verbose console output
   - Shows all debug information
   - All logs are saved to the log file
   ```bash
   python -m src.scraper_app.main --url "https://example.com" --debug
   ```

## Advanced Options

1. Custom output directory:
```bash
python -m src.scraper_app.main --url "https://example.com" --output-dir "./custom_data"
```

2. Scraping mode selection:
```bash
python -m src.scraper_app.main --url "https://example.com" --scrape-mode text    # Text only
python -m src.scraper_app.main --url "https://example.com" --scrape-mode ocr     # OCR only
python -m src.scraper_app.main --url "https://example.com" --scrape-mode both    # Both (default)
```

3. Name your scraping run:
```bash
python -m src.scraper_app.main --url "https://example.com" --run-name "my_scrape_run"
```

### Combined Examples

1. Full featured example with all options:
```bash
python -m src.scraper_app.main --url "https://example.com" --scrape-mode both --output-dir "./data/scraping/" --debug --run-name "test_run"
```

2. Process multiple URLs with custom settings:
```bash
python -m src.scraper_app.main --url-file urls.txt --scrape-mode both --output-dir "./data/scraping/" --debug --run-name "batch_run"
```

### Database Operations

To scrape URLs sourced from the project's database:

*   If `--from-db` is used without `--db-range`, it fetches the first `N` records based on `--num-urls`:
    ```bash
    python -m src.scraper_app.main --from-db --num-urls 50
    ```
    This fetches the first 50 records.

*   **Scrape a specific range of URLs from the database using `--db-range`:**
    The `--db-range <START-END>` argument allows you to fetch a deterministic slice of URLs from the database when `--from-db` is active.
    *   **Format:** `START-END` (e.g., "1-100").
    *   **Indexing:** 1-indexed, inclusive. So, "1-100" fetches records 1 through 100.
    *   **Ordering:** For consistent slicing, the database query orders records by `created_at ASC, client_id ASC`.
    *   **Interaction with `--num-urls`:** If `--db-range` is provided, it **overrides** the `--num-urls` argument for determining the count and selection of URLs from the database.
    *   **Interaction with `--from-db`:** `--db-range` is only active if `--from-db` is also used. If `--from-db` is not present, `--db-range` is ignored.
    *   **Help Text:** 'Specify a range of records to fetch (e.g., "1-100"). 1-indexed, inclusive. Overrides --num-urls if used with --from-db.'

    **Examples for `--db-range`:**

    *   Fetch the first 100 records (1-100):
        ```bash
        python -m src.scraper_app.main --from-db --db-range 1-100
        ```

    *   Fetch records 101 through 200:
        ```bash
        python -m src.scraper_app.main --from-db --db-range 101-200 --scrape-mode text
        ```

    *   Using `--db-range` with `--num-urls` (note: `--num-urls` will be ignored for DB record count):
        ```bash
        python -m src.scraper_app.main --from-db --db-range 1-20 --num-urls 500
        ```
        In this case, only records 1-20 will be fetched, as specified by `--db-range`. `--num-urls 500` is ignored for database fetching.

    *   Using `--db-range` without `--from-db` (note: `--db-range` will be ignored):
        ```bash
        python -m src.scraper_app.main --url "https://example.com" --db-range 1-10
        ```
        Here, `--db-range` has no effect because `--from-db` is not specified. The scraper will process "https://example.com".


*   Scrape URLs from the database (using `--num-urls`) with debug mode enabled:
    ```bash
    python -m src.scraper_app.main --from-db --num-urls 10 --debug
    ```
## Output Structure

The scraper organizes its output in the following structure:

```
data/
└── scraping/                 # Scraping results
    └── <timestamp_or_run_name>/ # Run-specific directories (timestamp by default, or custom if --run-name is used)
        ├── pages/            # Scraped page content
        │   └── <company>/    # Company-specific content (derived from URL or other metadata)
        │       ├── page.html # Raw HTML
        │       ├── text.txt  # Extracted text
        │       ├── text.json # Structured data (if applicable)
        │       └── ocr/      # OCR results for images within this page/company context
        ├── images/           # Downloaded images (if saved, structure might vary)
        ├── session.log       # Log for this specific scraping session/run
        └── summary.json      # Summary of this scraping session/run
logs/ (at project root)
├── scraper.log               # General scraper operational logs
└── scrape_history.log        # Historical log of all scraping runs
```

## Environment Variables

The scraper can be configured using environment variables:

- `SCRAPER_ROOT`: Root directory for the project (default: current working directory)
- `SCRAPER_IMAGE_TIMEOUT`: Timeout for image downloads in seconds (default: 10)
- `SCRAPER_IMAGE_RETRY_COUNT`: Number of retry attempts for failed image downloads (default: 3)
- `SCRAPER_IMAGE_RETRY_DELAY`: Delay between image download retries in seconds (default: 1)
- `SCRAPER_MAX_REQUESTS_PER_SECOND`: Rate limit for requests (default: 2.0)
- `SCRAPER_RATE_LIMIT_BURST`: Maximum burst of requests allowed (default: 5)

## Logging

The scraper maintains two types of logs:

1. **Main Log File** (`logs/scraper.log`):
   - Contains all logging information
   - Includes debug, info, warning, and error messages
   - Useful for troubleshooting and analysis

2. **History Log** (`logs/scrape_history.log`):
   - Contains a summary of each scraping run
   - Includes success rates, timing, and error counts
   - Useful for tracking performance over time

## Error Handling

The scraper handles various types of errors:

- Invalid URLs
- Connection issues
- Parsing errors
- OCR processing errors
- File system errors

All errors are logged to both the console (in debug mode) and the log file, with detailed information about the error type and context.