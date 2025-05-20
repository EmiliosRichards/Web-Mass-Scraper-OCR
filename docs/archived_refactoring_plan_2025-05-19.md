# Project Refactoring Plan: Synchronous Scraper with `.env` Configuration (2025-05-19)

**Overall Goals:**

1.  Transition all configuration from command-line arguments to a `.env` file.
2.  Make database interaction (both reading and writing) optional, defaulting to OFF.
3.  Ensure the scraper operates in a purely synchronous manner.
4.  Maintain the ability to scrape a single URL, a list of URLs from a file, or (if DB is enabled) URLs from the database.
5.  Produce the same output files as the current working version.

---

**Phase 1: Environment and Configuration Setup**

1.  **Create `.env` File:**
    *   Create a `.env` file in the project root (`c:/Users/EmiliosRichards/Projects/Phone Extraction/7 - Quick Win Martin - Copy/scraper/.env`).
    *   Populate it with the following variables, including sensible defaults (especially for paths and boolean flags). User will need to fill in sensitive details like DB credentials if `SCRAPER_USE_DATABASE` is true.

    ```dotenv
    # --- Scraper Core Settings ---
    SCRAPER_TARGET_URL=""
    SCRAPER_URL_FILE_PATH=""
    SCRAPER_DEBUG_MODE="False"
    SCRAPER_OUTPUT_DIRECTORY="data" # Relative to project root
    SCRAPER_MODE="both" # 'text', 'ocr', or 'both'
    SCRAPER_RUN_NAME=""

    # --- Database Interaction Control ---
    SCRAPER_USE_DATABASE="False" # Master switch for all DB operations

    # --- Database Source Settings (only used if SCRAPER_USE_DATABASE is True) ---
    SCRAPER_SOURCE_FROM_DB="False" # If True, and SCRAPER_USE_DATABASE is True, fetch URLs from DB
    SCRAPER_DB_NUM_URLS="10"
    SCRAPER_DB_RANGE="" # e.g., "0-100"
    SCRAPER_DB_PENDING_BATCH_SIZE="10" # For processing pending URLs from DB

    # --- Database Connection Details (only used if SCRAPER_USE_DATABASE is True) ---
    DB_HOST="localhost"
    DB_PORT="5432"
    DB_NAME="scraper_db"
    DB_USER="scraper_user"
    DB_PASSWORD="scraper_password"

    # --- Existing Configs (from current config.py, ensure they are here or defaults are fine) ---
    SCRAPER_ROOT="" # Can be used to override project root, defaults to Path.cwd()
    SCRAPER_IMAGE_TIMEOUT="10"
    SCRAPER_IMAGE_RETRY_COUNT="3"
    SCRAPER_IMAGE_RETRY_DELAY="1"
    SCRAPER_MAX_REQUESTS_PER_SECOND="2.0"
    SCRAPER_RATE_LIMIT_BURST="5"
    SCRAPER_MAX_RETRIES="1" # For page scraping
    SCRAPER_INITIAL_DELAY="1.0"
    SCRAPER_BACKOFF_FACTOR="2.0"
    SCRAPER_MAX_DELAY="60.0"
    SCRAPER_RETRY_JITTER="True"
    ```

2.  **Refactor `src/scraper_app/config.py`:**
    *   Ensure `load_dotenv()` is called at the very beginning.
    *   Define Python constants for all variables listed in the `.env` file above.
    *   Use `os.getenv()` to load each value, providing appropriate default values (e.g., `False` for booleans, empty strings, default numbers).
    *   Implement type conversion (e.g., `str(os.getenv(...))`, `int(os.getenv(...))`, `os.getenv(...).lower() == 'true'` for booleans).
    *   Remove any variables that were related to `async` mode if they exist.
    *   The `ROOT_DIR` should default to `Path.cwd()` if `SCRAPER_ROOT` is not set. `DATA_DIR` and other paths should be constructed relative to `ROOT_DIR`.

---

**Phase 2: Database Utility Modification**

1.  **Modify `src/scraper_app/db_utils.py`:**
    *   In the `get_db_connection()` function:
        *   At the very beginning, check the value of `config.SCRAPER_USE_DATABASE`.
        *   If `config.SCRAPER_USE_DATABASE` is `False`, log a message (e.g., "Database use is disabled via SCRAPER_USE_DATABASE. Skipping connection.") and immediately return `None`.
        *   The rest of the function (attempting to connect using `psycopg2.connect`) will only execute if `config.SCRAPER_USE_DATABASE` is `True`.
    *   No other changes should be strictly necessary in `db_utils.py` as functions already check for a `None` connection object.

---

**Phase 3: Main Application Logic Refactoring (`src/scraper_app/main.py`)**

1.  **Remove Argument Parsing:**
    *   Delete the entire `argparse` setup section.
2.  **Update `main()` Function:**
    *   Replace all references to `args.variable_name` with `config.CORRESPONDING_VARIABLE_NAME`.
    *   **URL Sourcing Logic:**
        *   If `config.SCRAPER_TARGET_URL` is set, use it.
        *   Else if `config.SCRAPER_URL_FILE_PATH` is set, read from that file.
        *   Else if `config.SCRAPER_USE_DATABASE` is `True` AND `config.SCRAPER_SOURCE_FROM_DB` is `True`, then attempt to fetch from the database.
        *   If none of these primary sources are configured, log an error and exit.
    *   **Database Interaction Points:**
        *   Before any call to a `db_utils` function, explicitly check `if config.SCRAPER_USE_DATABASE:`.
        *   If `False`, skip the DB operation or handle `None`/empty results gracefully.
        *   Example for logging pending scrape:
            ```python
            if config.SCRAPER_USE_DATABASE:
                log_id = db_utils.log_pending_scrape(...)
                # ...
                db_utils.update_scraping_log_status(...)
            else:
                log_id = f"local_run_{datetime.now().isoformat()}" # Placeholder
                logging.info("Database is disabled. Skipping DB logging.")
            ```
3.  **Remove Asynchronous Code:**
    *   Remove `async` and `await` keywords.
    *   Remove unused `asyncio` and `httpx.AsyncClient` imports.
    *   Refactor or merge `async def process_url(...)` into a synchronous flow.
    *   Ensure Playwright usage is purely synchronous (`sync_playwright`).

---

**Phase 4: Refactoring Supporting Modules (Synchronous Conversion)**

1.  **`src/scraper_app/scraper.py`:**
    *   Ensure all functions, especially `scrape_page`, are purely synchronous. Use `sync_playwright`.
    *   Remove any internal async/sync branching logic.
2.  **`src/scraper_app/url_processor.py`:**
    *   Convert `process_pending_urls_loop` to a synchronous loop.
    *   It should only attempt to fetch pending URLs if `config.SCRAPER_USE_DATABASE` is `True`.
3.  **`src/scraper_app/rate_limiter.py`:**
    *   Simplify `RateLimiter` for a synchronous context (e.g., `threading.Lock` if needed, or basic `time.sleep`).
4.  **`src/scraper_app/retry.py`:**
    *   Ensure the retry decorator uses `time.sleep()` and works with synchronous functions.

---

**Phase 5: Testing**

1.  **Test Case 1: Single URL, DB OFF**
    *   Set `SCRAPER_TARGET_URL="<some_test_url>"`
    *   Set `SCRAPER_USE_DATABASE="False"`
    *   Run and verify output files, no DB interaction.
2.  **Test Case 2: URL File, DB OFF**
    *   Create `test_urls.txt`. Set `SCRAPER_URL_FILE_PATH="test_urls.txt"`.
    *   Set `SCRAPER_USE_DATABASE="False"`. Run and verify.
3.  **Test Case 3: Single URL, DB ON (Sourcing from URL, Writing to DB)**
    *   Set `SCRAPER_TARGET_URL="<some_test_url>"`.
    *   Set `SCRAPER_USE_DATABASE="True"`, `SCRAPER_SOURCE_FROM_DB="False"`.
    *   Configure DB credentials. Run and verify outputs and DB records.
4.  **Test Case 4: DB Source, DB ON**
    *   Populate `companies` table.
    *   Set `SCRAPER_USE_DATABASE="True"`, `SCRAPER_SOURCE_FROM_DB="True"`.
    *   Configure `SCRAPER_DB_NUM_URLS` or `SCRAPER_DB_RANGE`.
    *   Run and verify DB fetch, processing, and DB writes.
5.  **Test Case 5: Invalid Configs**
    *   Test behavior with missing essential `.env` vars.

---

**Phase 6: Documentation**

1.  **Update `README.md` / `USAGE.md` / `docs/scraper_usage.md`:**
    *   Remove command-line argument documentation.
    *   Add section detailing all `.env` variables, purpose, and examples.
    *   Explain `SCRAPER_USE_DATABASE` and default behavior.

---

**Visual Plan (Mermaid Diagram):**

```mermaid
graph TD
    A[Start Refactoring] --> B{Define .env Structure};
    B --> C[Create .env file with defaults];
    B --> D[Update config.py to load all from .env];
    D --> E{Modify db_utils.py};
    E --> F[get_db_connection checks SCRAPER_USE_DATABASE];
    F --> G{Refactor main.py};
    G --> H[Remove argparse];
    G --> I[Use config.py for all settings];
    G --> J[Implement conditional DB logic based on SCRAPER_USE_DATABASE];
    G --> K[Ensure URL sourcing logic uses .env vars];
    G --> L[Remove async/await, use sync processing];
    L --> M{Refactor Supporting Modules};
    M --> N[scraper.py: Purely synchronous];
    M --> O[url_processor.py: Synchronous loop, conditional DB fetch];
    M --> P[rate_limiter.py: Simplify for sync];
    M --> Q[retry.py: Use time.sleep];
    Q --> R{Testing Phase};
    R -- Test Case 1 --> S[Single URL, DB OFF];
    R -- Test Case 2 --> T[URL File, DB OFF];
    R -- Test Case 3 --> U[Single URL, DB ON (Write)];
    R -- Test Case 4 --> V[DB Source, DB ON (Read/Write)];
    V --> W{Documentation};
    W --> X[Update README/USAGE with .env guide];
    X --> Y[End Refactoring];