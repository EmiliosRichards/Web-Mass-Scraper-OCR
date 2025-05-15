# Web Scraping System

## Project Overview

This web scraping system is designed to extract data from websites, process it (including OCR on images), and store results in a PostgreSQL database. The system is built with scalability, reliability, and flexibility in mind, allowing for dynamic URL selection, comprehensive data extraction, and detailed logging.

## System Architecture

The system consists of several key components:

1. **URL Selection**: Dynamically selects URLs from a database or file for scraping.
2. **Scraping Engine**: Uses Playwright to render web pages and extract content.
3. **OCR Processing**: Extracts text from images using Tesseract OCR.
4. **Data Storage**: Saves extracted data locally and updates PostgreSQL database.
5. **Logging & Monitoring**: Tracks scraping progress and performance metrics.

### Component Diagram

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   URL Sources   │────▶│  Scraping Engine │────▶│  Data Processing│
│ (DB/File/CLI)   │     │   (Playwright)   │     │  (Text/OCR)     │
└─────────────────┘     └─────────────────┘     └────────┬────────┘
                                                         │
                                                         ▼
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Status Updates │◀────│  Data Storage   │◀────│ Result Analysis │
│  (DB/Logs)      │     │ (Files/Database)│     │                 │
└─────────────────┘     └─────────────────┘     └─────────────────┘
```

## Installation

### Prerequisites

- Python 3.8+
- PostgreSQL database
- Tesseract OCR engine

### Setup

1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd <repository-directory>
   ```

2. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Install Tesseract OCR:
   - **Windows**: Download from [UB-Mannheim/tesseract](https://github.com/UB-Mannheim/tesseract/wiki)
   - **Linux**: `sudo apt-get install tesseract-ocr`
   - **macOS**: `brew install tesseract`

4. Install Playwright browsers:
   ```bash
   python -m playwright install
   ```

5. Create a `.env` file with your configuration:
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

## Configuration Options

The system can be configured through environment variables or a `.env` file:

| Variable | Description | Default |
|----------|-------------|---------|
| `DB_HOST` | PostgreSQL database host | localhost |
| `DB_PORT` | PostgreSQL database port | 5432 |
| `DB_NAME` | PostgreSQL database name | scraper_db |
| `DB_USER` | PostgreSQL database user | scraper_user |
| `DB_PASSWORD` | PostgreSQL database password | scraper_password |
| `SCRAPER_ROOT` | Root directory for the project | Current working directory |
| `SCRAPER_IMAGE_TIMEOUT` | Timeout for image downloads (seconds) | 10 |
| `SCRAPER_IMAGE_RETRY_COUNT` | Number of retries for failed image downloads | 3 |
| `SCRAPER_IMAGE_RETRY_DELAY` | Delay between retries (seconds) | 1 |
| `SCRAPER_MAX_REQUESTS_PER_SECOND` | Rate limit for requests | 2.0 |
| `SCRAPER_RATE_LIMIT_BURST` | Maximum burst of requests allowed | 5 |

## Command-Line Interface

The scraper offers various command-line arguments to control its behavior. Key arguments include:

*   `--url <URL>`: Scrape a single URL.
*   `--url-file <FILE_PATH>`: Scrape URLs from a file.
*   `--from-db`: Fetch URLs to scrape from the database.
    *   `--num-urls <NUMBER>`: Specify the number of URLs to fetch from the database. If `--db-range` is not used, this determines the count of the first N records.
    *   `--db-range <START-END>`: Specify a range of records to fetch (e.g., "1-100"). 1-indexed, inclusive. Overrides --num-urls if used with --from-db. The database query will order records by `created_at ASC, client_id ASC` for consistent slicing.
        *   Example: `python -m src.scraper_app.main --from-db --db-range 1-50` (fetches the first 50 records).
        *   Example: `python -m src.scraper_app.main --from-db --db-range 51-100` (fetches records 51 through 100).
*   `--scrape-mode <MODE>`: Set scraping mode ('text', 'ocr', 'both').
*   `--output-dir <DIR_PATH>`: Specify a custom output directory.
*   `--run-name <NAME>`: Assign a name to the scraping run.
*   `--debug`: Enable debug mode for verbose logging.

For a comprehensive list and detailed explanations, please refer to the [`docs/scraper_usage.md`](docs/scraper_usage.md:1) file.
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
│   └── scripts/              # Utility scripts
│       └── process_pending_urls_loop.py # Script to process pending URLs
├── docs/                     # Documentation
│   ├── database_structure_analysis.md
│   ├── implementation_notes.md
│   ├── scraper_usage.md
│   └── images/
│       └── pipeline_diagram.png
├── data/                     # Data storage directory
│   └── scraping/             # Scraping results
│       └── <timestamp>/      # Run-specific directories
│           ├── pages/        # Scraped page content
│           │   └── <company>/# Company-specific content
│           │       ├── page.html    # Raw HTML
│           │       ├── text.txt     # Extracted text
│           │       ├── text.json    # Structured data
│           │       └── ocr/         # OCR results
│           ├── images/       # Downloaded images
│           ├── session.log   # Session log
│           └── summary.json  # Session summary
├── logs/                     # Log files
│   ├── scraper.log           # Main log file
│   └── scrape_history.log    # Historical scraping data
├── tests/                    # Test suite
│   └── __init__.py           # Package initialization
├── requirements.txt          # Python dependencies
└── .env                      # Environment variables
```

## Database Schema

The system uses three main database tables:

### 1. `companies`
Stores company information including website URLs to scrape.

Key columns:
- `client_id` (UUID): Primary key
- `company_name` (TEXT): Name of the company
- `website` (TEXT): Company website URL
- `number_of_retail_locations` (INTEGER): Number of retail locations
- `founded_year` (INTEGER): Year the company was founded
- `employees` (INTEGER): Number of employees
- `created_at` (TIMESTAMP): Timestamp of record creation
- `updated_at` (TIMESTAMP): Timestamp of last record update
- `industry` (TEXT): Industry classification
- `annual_revenue` (NUMERIC): Annual revenue
- `short_description` (TEXT): Brief company description
- `keywords` (TEXT[]): Associated keywords
- `technologies` (TEXT[]): Technologies used
- `street` (TEXT): Street name and number
- `city` (TEXT): City name
- `state` (TEXT): State or province
- `postal_code` (TEXT): Postal or ZIP code
- `country` (TEXT): Country name
- `address` (TEXT): Full street address
- `apollo_account_id` (TEXT): Apollo.io account ID
- `seo_description` (TEXT): SEO-optimized description
- `company_phone` (TEXT): Main phone number
- `linkedin_url` (TEXT): LinkedIn profile URL
- `facebook_url` (TEXT): Facebook page URL
- `twitter_url` (TEXT): Twitter profile URL

### 2. `scraping_logs`
Tracks the scraping process status for each URL.

Columns:
- `log_id` (UUID): Primary key
- `client_id` (UUID): Foreign key to companies
- `source` (TEXT): Source of the URL (e.g., 'homepage', 'linkedin')
- `url_scraped` (TEXT): The URL that was scraped
- `status` (TEXT): Status of the scrape ('pending', 'completed', 'failed')
- `scraping_date` (TIMESTAMP): When the scrape was performed
- `error_message` (TEXT): Error message if the scrape failed

### 3. `scraped_pages`
Stores metadata about scraped pages.

Columns:
- `page_id` (UUID): Primary key
- `client_id` (UUID): Foreign key to companies
- `url` (TEXT): The URL that was scraped
- `page_type` (TEXT): Type of page (e.g., 'homepage', 'about')
- `scraped_at` (TIMESTAMP): When the page was scraped
- `raw_html_path` (TEXT): Path to the saved HTML file
- `plain_text_path` (TEXT): Path to the saved text file
- `summary` (TEXT): Summary of the scraping result
- `extraction_notes` (TEXT): Notes about the extraction process