> **Note:** This document describes a previous refactoring iteration from May 2019 that introduced asynchronous/synchronous execution modes and other structural changes. Many of these specific changes, particularly the dual async/sync execution paths for Playwright and the `SCRAPER_EXECUTION_MODE` variable, were subsequently simplified or reverted due to persistent environment-specific issues (detailed in section 8.4). The system now operates in a purely synchronous manner. For the most up-to-date summary of the current architecture and features, please refer to `docs/current_refactoring_summary_2025-05-20.md`.

# Refactoring Summary (May 2025) - Historical Async/Sync Attempt

## 1. Introduction

This document summarizes the major refactoring efforts undertaken in the web scraping system during May 2025. The primary goals of this refactoring were:
*   To centralize all system configurations, moving away from command-line arguments to an environment-based setup using a `.env` file.
*   To introduce distinct execution modes (asynchronous and synchronous) for better flexibility and performance management.
*   To improve code modularity and clarity by restructuring key components of the application.

These changes aim to make the scraper easier to configure, manage, and maintain, while also providing options for different operational needs.

## 2. Major Change: Configuration System Overhaul

The most significant change was the complete overhaul of the configuration system.

### 2.1. From Command-Line Arguments to `.env`
Previously, the scraper utilized a combination of command-line arguments and some environment variables for configuration. This has been entirely replaced by a unified `.env` file system. All operational parameters are now set exclusively through variables in a `.env` file located in the project root.

This change simplifies running the scraper, as the command is now consistently `python -m src.scraper_app.main`, with all behavior dictated by the `.env` settings.

### 2.2. Key `.env` Variables
Numerous `.env` variables were introduced or had their roles clarified. These cover:
*   **URL Sourcing**: `SCRAPER_TARGET_URL`, `SCRAPER_URL_FILE_PATH`, `SCRAPER_SOURCE_FROM_DB`.
*   **Database Source Specifics**: `SCRAPER_DB_NUM_URLS`, `SCRAPER_DB_RANGE`, `SCRAPER_DB_PENDING_BATCH_SIZE`.
*   **Execution Mode**: `SCRAPER_EXECUTION_MODE` (can be `async` or `sync`, defaults to `async`).
*   **Scraping Behavior**: `SCRAPER_MODE` (text, ocr, both), `SCRAPER_RUN_NAME`, `SCRAPER_OUTPUT_DIRECTORY`.
*   **Logging**: `SCRAPER_CONSOLE_LOG_LEVEL`, `SCRAPER_DEBUG_MODE`, `SCRAPER_LOG_SUBDIR`, `SCRAPER_LOG_FILENAME`.
*   **Operational Control**: `SCRAPER_DISABLE_RATE_LIMIT`, `SCRAPER_MAX_REQUESTS_PER_SECOND`, `SCRAPER_RATE_LIMIT_BURST`, `SCRAPER_MAX_CONCURRENT_TASKS`.
*   **Timeouts and Retries**: `SCRAPER_PAGE_TIMEOUT_MS`, `SCRAPER_OCR_TIMEOUT_S`, `SCRAPER_IMAGE_DOWNLOAD_TIMEOUT_S`, `SCRAPER_RETRY_ATTEMPTS`, `SCRAPER_RETRY_DELAY_S`.
*   **Database Connection**: `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`.

(For a complete list and defaults, refer to the updated `README.md`.)

### 2.3. Impact on `src/scraper_app/config.py`
The `config.py` module was refactored to:
*   Load all settings exclusively from the `.env` file using `python-dotenv`.
*   Provide default values for all configuration variables if they are not set in the `.env` file.
*   Handle type conversions for environment variables (e.g., string to boolean or integer).

## 3. Major Change: Code Structure and Execution Flow

Significant restructuring was done to support the new configuration system and introduce execution modes.

### 3.1. Execution Modes (`SCRAPER_EXECUTION_MODE`)
A new configuration `SCRAPER_EXECUTION_MODE` was introduced in `config.py`, allowing the scraper to run in either:
*   **`async` mode (default)**: Utilizes `asyncio` for concurrent processing of URLs, suitable for I/O-bound tasks and potentially faster for multiple URLs.
*   **`sync` mode**: Processes URLs sequentially, which can be simpler for debugging or for environments where `asyncio` might be problematic.

### 3.2. Creation of `src/scraper_app/processing_utils.py`
To improve modularity and avoid circular dependencies, several core processing functions were moved from `main.py` to a new file, `processing_utils.py`. This includes:
*   `process_url_async(url, http_session, rate_limiter)`: Handles the asynchronous processing of a single URL.
*   `process_url_sync(url, rate_limiter)`: Handles the synchronous processing of a single URL.
*   Helper functions: `get_output_paths`, `generate_scraping_summary`, `log_scraping_summary`.

### 3.3. Modifications to `src/scraper_app/main.py`
The main entry point, `main.py`, was heavily refactored:
*   Removed all `argparse` logic.
*   Introduced `main_async()` and `main_sync()` functions to handle the respective execution flows.
*   A `run_main()` function now reads `SCRAPER_EXECUTION_MODE` from `config.py` and calls either `main_sync()` or `asyncio.run(main_async())`.
*   Imports `process_url_async` and `process_url_sync` from `processing_utils.py`.
*   Manages the `ScrapingSession` and high-level orchestration. Helper functions for logging session summaries and writing run files were kept in `main.py`.

### 3.4. Modifications to `src/scraper_app/url_processor.py`
The `url_processor.py` module, which handles fetching and processing URLs from the database, was updated:
*   `process_pending_urls_loop` is now an `async` function.
*   It imports and calls `process_url_async` from `processing_utils.py`.
*   It uses global configuration settings from `config.py` for its operations (e.g., batch sizes, scrape mode).
*   Manages an `AsyncClient` session for its child tasks.

### 3.5. Modifications to `src/scraper_app/scraper.py`
The core scraping logic in `scraper.py` was adapted:
*   The main `scrape_page` function was effectively split by the introduction of `_scrape_page_async_internal` and `_scrape_page_sync_internal` (though these specific internal names might have been further refactored into the `scrape_page` function in `scraper.py` now taking parameters to guide its sync/async behavior or Playwright usage). The key is that `processing_utils.py` now calls `scraper.py` functions appropriately for sync/async execution.
*   Internal rate limiting logic was removed from `scrape_page` functions; rate limiting is handled by the callers in `processing_utils.py`.
*   The `@retry_with_backoff` decorator now uses the new retry configuration variables from `config.py`.

### 3.6. Modifications to `src/scraper_app/rate_limiter.py`
The `rate_limiter.py` was enhanced:
*   It now supports an explicit `mode` ('async' or 'sync') during initialization, determining whether to use `asyncio.Lock` or `threading.Lock`.
*   Provides distinct methods for async and sync operations:
    *   `acquire()` (async) and `acquire_sync()`
    *   `wait()` (async) and `wait_sync()`
    *   `reset()` (async) and `reset_sync()` (or a single `reset` that respects the mode).
*   The `get_rate_limiter` factory function now also accepts and uses a `mode` parameter.

## 4. Major Change: Database Interaction Adjustments

While `db_utils.py` continues to use `psycopg2` for direct database communication:
*   Functions in `db_utils.py` manage their own database connections.
*   Callers in `processing_utils.py` and `url_processor.py` no longer pass database session/connection objects.
*   Signatures of `db_utils` functions like `insert_scraped_page_data` and `update_scraping_log_status` were clarified, and calls to them were corrected.

## 5. Major Change: Logging Adjustments

*   The custom logging formatter in `logging_utils.py` supports a `category` attribute passed via the `extra` dictionary.
*   Basic logging calls (e.g., `logging.info()`) in `main.py` and other modules were reviewed; the `category` parameter is intended for use with the custom helper functions (`logging_utils.info`, `logging_utils.error`, etc.) or via the `extra` dict.

## 6. Documentation Updates

All user-facing documentation files were updated:
*   `README.md`: Overhauled "Configuration Options" and removed "Command-Line Interface".
*   `scraper_usage.md`: Rewritten to focus on key `.env` configurations.
*   `USAGE.md`: Extensively revised to detail the new `.env`-based configuration, execution modes, and updated operational instructions.

## 7. Conclusion

This refactoring effort has modernized the scraper's configuration and execution mechanisms, providing a more streamlined and flexible system. The move to a fully `.env`-based configuration simplifies setup and deployment, while the introduction of selectable execution modes allows for better adaptation to different use cases and environments. The code restructuring enhances modularity, aiming for improved maintainability in the long term.

## 8. Subsequent Enhancements and Debugging Efforts (May 19, 2025)

Following the initial refactoring, further enhancements and debugging efforts were undertaken:

### 8.1. Enhanced Database Interaction Control

*   **Conditional Database Writes**: The `SCRAPER_SOURCE_FROM_DB` configuration variable in `.env` (and `config.py`) was enhanced. Previously, it controlled only whether URLs were *sourced* from the database. It now also dictates whether the application attempts to *write* any data (logs, scraped content) to the database.
    *   If `SCRAPER_SOURCE_FROM_DB=False`, all database write attempts are bypassed. This was achieved by modifying `src/scraper_app/db_utils.py` (specifically the `get_db_connection` function) to check this flag and return `None` if set to `False`, preventing connection establishment.
    *   This change was tested using a dedicated script (`test_db_bypass.py`) and by observing log outputs.

### 8.2. Playwright Asynchronous Refactoring and Sync Fallback

To address a "Playwright Sync API inside asyncio loop" error that prevented scraping, a significant refactor was performed:

*   **Initial Async Conversion**:
    *   The core scraping function `scrape_page` in `src/scraper_app/scraper.py` was converted to an `async def` function.
    *   Playwright calls within it were changed to use the `async_api` (e.g., `async_playwright`, `await page.goto()`).
    *   Synchronous helper functions called from this async path (e.g., for file I/O, OCR, image downloads) were wrapped using `await asyncio.to_thread(...)`.
    *   The `@retry_with_backoff` decorator in `src/scraper_app/retry.py` was updated to support `async` functions, using `asyncio.sleep()` for delays.
    *   The calling function `process_url` in `src/scraper_app/processing_utils.py` was updated to `await` the `scrape_page` call.

*   **Introduction of `SCRAPER_EXECUTION_MODE`**:
    *   Due to a persistent `NotImplementedError` with Playwright's async subprocess handling on the Windows/Python 3.13 test environment, a fallback mechanism was introduced.
    *   A new configuration variable, `SCRAPER_EXECUTION_MODE` (values: `async` or `sync`, defaulting to `async`), was added to `src/scraper_app/config.py` and the `.env` file.
    *   **Dual Execution Paths Created**:
        *   **`src/scraper_app/scraper.py`**: Now contains `_scrape_page_async_internal` (using `async_playwright`) and `_scrape_page_sync_internal` (reconstructed to use `sync_playwright`). A top-level `async def scrape_page` chooser function calls the appropriate internal version based on `SCRAPER_EXECUTION_MODE`. The sync internal version is called via `asyncio.to_thread` when selected by the async chooser if the main loop is async, or directly if the main loop is sync.
        *   **`src/scraper_app/retry.py`**: A new `retry_with_backoff_sync` decorator (using `time.sleep`) was created alongside the existing async-compatible `retry_with_backoff_async` (aliased as `retry_with_backoff`). The respective internal scrape functions are decorated appropriately.
        *   **`src/scraper_app/rate_limiter.py`**: Enhanced to support `mode` ('async' or 'sync') during initialization, using the correct lock type (`asyncio.Lock` or `threading.Lock`) and providing distinct `wait()`/`wait_sync()` and `acquire()`/`acquire_sync()` methods. The `get_rate_limiter` factory was updated.
        *   **`src/scraper_app/processing_utils.py`**: Split `process_url` into `process_url_async` and `process_url_sync`. Each calls the respective internal scraper function and uses the appropriate rate limiter methods and synchronous/asynchronous calls for helpers.
        *   **`src/scraper_app/main.py`**: The `run_main` function now checks `SCRAPER_EXECUTION_MODE`. If `'sync'`, it calls a new `main_sync()` function (which processes URLs sequentially using `process_url_sync`). If `'async'`, it calls `asyncio.run(main_async())` as before (which uses `process_url_async` and `asyncio.Semaphore` for concurrency).

### 8.3. URL Handling and Minor Fixes

*   Corrected URLs in `urls_to_scrape.txt` to include the `https://` scheme, resolving "Invalid URL scheme" errors.
*   Attempted to resolve the Playwright `NotImplementedError` by moving the `WindowsSelectorEventLoopPolicy` setting to the top of `main.py` and running `playwright install`. These steps did not resolve the underlying error.
*   The Playwright check in the `finally` block of `run_main` in `main.py` was commented out as a debugging step for the `NotImplementedError`.

### 8.4. Current Status and Outstanding Issues

*   The system is now highly configurable via the `.env` file for URL sourcing, database interaction control, and execution mode (sync/async for Playwright).
*   Documentation in `scraper_usage.md` has been updated to reflect these operational modes and configuration details.
*   **Persistent Blocker**: Despite the extensive refactoring to support both async and a fully synchronous Playwright execution path, a fundamental `NotImplementedError` related to `asyncio.base_events.py` (`_make_subprocess_transport`) continues to prevent Playwright from successfully launching its browser driver process in the Windows Python 3.13 test environment. This error occurs in both `async` and `sync` execution modes, indicating an issue at the Playwright/asyncio/OS level.
*   Some Pylance static type checking warnings remain in `scraper.py` related to the type inference of `asyncio.to_thread` return values, which are believed to be quirks of the analyzer rather than runtime errors for the intended logic.

Further investigation into the `NotImplementedError` would likely require environment-specific debugging (e.g., testing different Python versions) or consulting Playwright issue trackers for similar problems on Windows with Python 3.13.