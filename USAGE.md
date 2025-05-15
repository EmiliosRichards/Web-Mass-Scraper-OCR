# Web Scraping System: Usage Guide

This document provides detailed instructions on how to use the web scraping system, including running the scraper, configuring options, setting up the database, and troubleshooting common issues.

## Table of Contents

1. [Running the Scraper](#running-the-scraper)
2. [Command-line Arguments](#command-line-arguments)
3. [Configuration Options](#configuration-options)
4. [Database Setup and Maintenance](#database-setup-and-maintenance)
5. [Troubleshooting](#troubleshooting)
6. [Best Practices](#best-practices)

## Running the Scraper

The scraper can be run in several ways depending on your needs:

### Basic Usage

To run the scraper with default settings:

```bash
python -m src.scraper_app.main
```

### Scraping a Single URL

To scrape a specific URL:

```bash
python -m src.scraper_app.main --url https://example.com
```

### Scraping URLs from a File

To scrape URLs from a text file (one URL per line):

```bash
python -m src.scraper_app.main --url-file urls.txt
```

### Scraping URLs from the Database

To scrape URLs from the `companies` table in the database:

```bash
python -m src.scraper_app.main --from-db --num-urls 10
```

#### Scraping URLs from the Database with `--db-range`

When using `--from-db`, you can control which records are fetched using `--db-range`.

*   `--db-range <START-END>`: Specify a range of records to fetch (e.g., "1-100"). 1-indexed, inclusive. Overrides `--num-urls` if used with `--from-db`.
*   **Ordering:** The database query orders records by `created_at ASC, client_id ASC` to ensure consistent results when using `--db-range`.

**Examples for `--db-range`:**

*   Fetch the first 100 records (1-100):
    ```bash
    python -m src.scraper_app.main --from-db --db-range 1-100
    ```

*   Fetch records 101 through 200:
    ```bash
    python -m src.scraper_app.main --from-db --db-range 101-200 --scrape-mode text
    ```
*   If `--from-db` is used without `--db-range`, it fetches the first `N` records based on `--num-urls` (e.g., `python -m src.scraper_app.main --from-db --num-urls 50` fetches the first 50).


### Processing Pending URLs

To process URLs that are already marked as 'pending' in the `scraping_logs` table (e.g., from a previous interrupted run or if explicitly added via the database), you instruct the main scraper script to fetch zero new URLs and then it will proceed to process the pending queue.

**Basic command to process pending URLs (default batch size of 10):**
```bash
python -m src.scraper_app.main --from-db --num-urls 0
```

**Explanation:**
*   `--from-db`: Tells the script its source for URLs is the database.
*   `--num-urls 0`: Instructs the script to fetch 0 *new* URLs from the `companies` table.
*   The script will then automatically check for and process URLs in the 'pending' state from the `scraping_logs` table, up to the `--pending-batch-size`.

**Processing a specific number of pending URLs:**

You can control how many pending URLs are processed in a run using the `--pending-batch-size` argument (default is 10).

To process up to 50 pending URLs:
```bash
python -m src.scraper_app.main --from-db --num-urls 0 --pending-batch-size 50
```

## Command-line Arguments

The scraper supports the following command-line arguments:

| Argument | Description | Default |
|----------|-------------|---------|
| `--url` | Single URL to scrape | None |
| `--url-file` | Path to a file containing URLs to scrape | None |
| `--from-db` | Fetch URLs from the database | False |
| `--num-urls` | Number of URLs to fetch from the database (used as limit if `--db-range` not set) | 10 |
| `--db-range` | Specify a range of records to fetch (e.g., "1-100"). 1-indexed, inclusive. Overrides --num-urls if used with --from-db. | None |
| `--scrape-mode` | Scraping mode: 'text', 'ocr', or 'both' | 'both' |
| `--run-name` | Custom name for this scraping run | None |
| `--output-dir` | Custom output directory | None |
| `--debug` | Enable debug mode. If true, console logs show DEBUG level and above. If false, console logs show INFO level and above. | False |
| `--no-rate-limit` | Disable rate limiting | False |

### Examples

1. Scrape 50 URLs from the database (using `--num-urls` as `--db-range` is not provided) with debug mode enabled:
   ```bash
   python -m src.scraper_app.main --from-db --num-urls 50 --debug
   ```

2. Scrape URLs from a file with text-only mode:
   ```bash
   python -m src.scraper_app.main --url-file company_urls.txt --scrape-mode text
   ```

3. Scrape a single URL with a custom run name:
   ```bash
   python -m src.scraper_app.main --url https://example.com --run-name example_company
   ```

4. Fetch the first 100 records from the database using `--db-range`:
   ```bash
   python -m src.scraper_app.main --from-db --db-range 1-100
   ```

5. Fetch records 101 through 200 from the database using `--db-range`:
   ```bash
   python -m src.scraper_app.main --from-db --db-range 101-200
   ```

## Configuration Options

### Environment Variables

The scraper can be configured using environment variables or a `.env` file. Here are the available options:

#### Database Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `DB_HOST` | PostgreSQL database host | localhost |
| `DB_PORT` | PostgreSQL database port | 5432 |
| `DB_NAME` | PostgreSQL database name | scraper_db |
| `DB_USER` | PostgreSQL database user | scraper_user |
| `DB_PASSWORD` | PostgreSQL database password | scraper_password |

#### Scraper Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `SCRAPER_ROOT` | Root directory for the project | Current working directory |
| `SCRAPER_IMAGE_TIMEOUT` | Timeout for image downloads (seconds) | 10 |
| `SCRAPER_IMAGE_RETRY_COUNT` | Number of retries for failed image downloads | 3 |
| `SCRAPER_IMAGE_RETRY_DELAY` | Delay between retries (seconds) | 1 |
| `SCRAPER_MAX_REQUESTS_PER_SECOND` | Rate limit for requests | 2.0 |
| `SCRAPER_RATE_LIMIT_BURST` | Maximum burst of requests allowed | 5 |

### Example .env File

```
# Database configuration
DB_HOST=localhost
DB_PORT=5432
DB_NAME=scraper_db
DB_USER=scraper_user
DB_PASSWORD=your_password

# Scraper configuration
SCRAPER_ROOT=/path/to/project
SCRAPER_IMAGE_TIMEOUT=10
SCRAPER_IMAGE_RETRY_COUNT=3
SCRAPER_IMAGE_RETRY_DELAY=1
SCRAPER_MAX_REQUESTS_PER_SECOND=2.0
SCRAPER_RATE_LIMIT_BURST=5
```

## Database Setup and Maintenance

### Initial Database Setup

1. Create a PostgreSQL database:
   ```sql
   CREATE DATABASE scraper_db;
   ```

2. Create a user with appropriate permissions:
   ```sql
   CREATE USER scraper_user WITH PASSWORD 'your_password';
   GRANT ALL PRIVILEGES ON DATABASE scraper_db TO scraper_user;
   ```

3. Create the required tables:
   ```sql
   -- Companies table
   CREATE TABLE companies (
       client_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
       company_name TEXT,
       website TEXT,
       company_phone TEXT,
       linkedin_url TEXT,
       facebook_url TEXT,
       twitter_url TEXT,
       industry TEXT,
       employees INTEGER,
       founded_year INTEGER,
       annual_revenue TEXT,
       number_of_retail_locations INTEGER,
       short_description TEXT,
       seo_description TEXT,
       keywords TEXT,
       technologies TEXT,
       street TEXT,
       city TEXT,
       state TEXT,
       postal_code TEXT,
       country TEXT,
       address TEXT,
       apollo_account_id TEXT,
       created_at TIMESTAMP DEFAULT NOW(),
       updated_at TIMESTAMP DEFAULT NOW()
   );

   -- Scraping logs table
   CREATE TABLE scraping_logs (
       log_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
       client_id UUID REFERENCES companies(client_id),
       source TEXT,
       url_scraped TEXT,
       status TEXT,
       scraping_date TIMESTAMP DEFAULT NOW(),
       error_message TEXT
   );

   -- Scraped pages table
   CREATE TABLE scraped_pages (
       page_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
       client_id UUID REFERENCES companies(client_id),
       url TEXT NOT NULL,
       page_type TEXT,
       scraped_at TIMESTAMP DEFAULT NOW(),
       raw_html_path TEXT,
       plain_text_path TEXT,
       summary TEXT,
       extraction_notes TEXT
   );
   ```

### Database Maintenance

#### Cleaning Up Old Logs

To remove old scraping logs:

```sql
DELETE FROM scraping_logs 
WHERE scraping_date < NOW() - INTERVAL '30 days';
```

#### Identifying Failed Scrapes

To find URLs that failed to scrape:

```sql
SELECT url_scraped, error_message, scraping_date 
FROM scraping_logs 
WHERE status = 'failed' 
ORDER BY scraping_date DESC;
```

#### Resetting Pending URLs

If the scraper was interrupted, you might need to reset 'pending' URLs:

```sql
UPDATE scraping_logs 
SET status = 'pending' 
WHERE status = 'pending' AND scraping_date < NOW() - INTERVAL '1 day';
```

## Troubleshooting

### Common Issues and Solutions

#### Database Connection Issues

**Issue**: Unable to connect to the database.

**Solution**:
1. Check that the PostgreSQL service is running.
2. Verify the database credentials in your `.env` file.
3. Ensure the database and tables exist.
4. Check network connectivity if using a remote database.

#### OCR Issues

**Issue**: OCR is not extracting text from images.

**Solution**:
1. Verify that Tesseract OCR is installed correctly.
2. Check the image quality - OCR works best with clear, high-resolution images.
3. Try adjusting the image enhancement settings in `ocr.py`.

#### Rate Limiting Issues

**Issue**: Scraping is being blocked by websites.

**Solution**:
1. Decrease the scraping rate by adjusting `SCRAPER_MAX_REQUESTS_PER_SECOND`.
2. Add delays between requests.
3. Consider using proxies for high-volume scraping.

#### Memory Issues

**Issue**: The scraper is using too much memory.

**Solution**:
1. Process fewer URLs at once by reducing the `--num-urls` parameter.
2. Implement batch processing for large datasets.
3. Close browser contexts promptly after use.

### Logging and Debugging

The scraper's logging system is designed for clarity and comprehensive record-keeping:

*   **Console Output**: Controlled by the `--debug` flag.
    *   When `--debug` is **not** used (default): Console shows `INFO` level messages and above (e.g., `INFO`, `WARNING`, `ERROR`, `CRITICAL`). This provides a summary of operations without excessive detail.
    *   When `--debug` **is** used: Console shows `DEBUG` level messages and above. This is useful for detailed troubleshooting.
        ```bash
        python -m src.scraper_app.main --url https://example.com --debug
        ```
*   **File Logging**: All log messages, regardless of the console setting (including `DEBUG`, `INFO`, `WARNING`, `ERROR`, and `CRITICAL`), are saved to `logs/scraper.log`. This ensures a complete record of all activities for later analysis.
*   **Session Logs**: Detailed logs for each individual scraping run are also saved in `data/scraping/<timestamp>/session.log`.

To enable detailed console logging for a specific run:
```bash
python -m src.scraper_app.main --url https://example.com --debug
```

You can also check the main log file at `logs/scraper.log` or the specific session log in:

```
data/scraping/<timestamp>/session.log
```

## Best Practices

### Ethical Scraping

1. **Respect robots.txt**: Always check and respect the website's robots.txt file.
2. **Rate limiting**: Use appropriate rate limiting to avoid overloading servers.
3. **Identify your scraper**: Consider adding a user-agent that identifies your scraper.
4. **Legal compliance**: Ensure your scraping activities comply with relevant laws and terms of service.

### Performance Optimization

1. **Batch processing**: Process URLs in batches to manage resources effectively.
2. **Selective scraping**: Only scrape the data you need.
3. **Caching**: Implement caching to avoid re-scraping unchanged content.
4. **Parallel processing**: For large-scale scraping, consider implementing parallel processing.

### Data Management

1. **Regular backups**: Back up your database regularly.
2. **Data cleaning**: Implement data cleaning and validation steps.
3. **Storage management**: Periodically clean up old scraping data to save disk space.
4. **Monitoring**: Set up monitoring for the scraping process to catch issues early.

### System Integration

1. **API endpoints**: Consider adding API endpoints to integrate with other systems.
2. **Scheduled runs**: Set up scheduled runs using cron jobs or similar tools.
3. **Notifications**: Implement notifications for completed or failed scraping runs.
4. **Reporting**: Generate reports on scraping performance and results.