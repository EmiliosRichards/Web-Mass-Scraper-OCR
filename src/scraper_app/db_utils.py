import psycopg2
import psycopg2.extras
import logging
from typing import Optional, List, Tuple, Any

from . import config

def get_db_connection() -> Optional[psycopg2.extensions.connection]:
    """
    Establishes a connection to the PostgreSQL database.

    Returns:
        psycopg2.extensions.connection: A database connection object or None if connection fails.
    """
    try:
        conn = psycopg2.connect(
            host=config.DB_HOST,
            port=config.DB_PORT,
            dbname=config.DB_NAME,
            user=config.DB_USER,
            password=config.DB_PASSWORD
        )
        logging.info("Successfully connected to the database.")
        return conn
    except psycopg2.Error as e:
        logging.error(f"Error connecting to the database: {e}")
        return None

def fetch_urls_from_db(limit: int, offset: int) -> List[Tuple[Optional[str], str]]:
    """
    Fetches website URLs and their client_ids from the 'companies' table.
    Fetches deterministically with offset and limit.
    Assumes 'companies' table has 'client_id' (UUID), 'website' (TEXT), and 'created_at' columns.

    Args:
        limit (int): The maximum number of URLs to fetch.
        offset (int): The number of records to skip before fetching (0-indexed).

    Returns:
        List[Tuple[Optional[str], str]]: A list of (client_id, website_url) tuples.
                                Returns an empty list if connection fails or no URLs are found.
    """
    logging.debug(f"Attempting to fetch up to {limit} URLs from the database with an offset of {offset}.")
    
    conn = get_db_connection()
    if not conn:
        return []
    
    results: List[Tuple[Optional[str], str]] = []
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            sql_query = "SELECT client_id, website FROM companies WHERE website IS NOT NULL AND website <> '' ORDER BY created_at ASC, client_id ASC LIMIT %s OFFSET %s;"
            params: Tuple[Any, ...] = (limit, offset)
            logging.info(f"Executing fetch: LIMIT {limit}, OFFSET {offset}")

            cur.execute(sql_query, params)
            fetched_rows = cur.fetchall()
            for row in fetched_rows:
                client_id = str(row['client_id']) if row['client_id'] else None
                website_url = row['website']
                if website_url: # Ensure website is not empty
                    results.append((client_id, website_url))
            
            logging.info(f"Fetched {len(results)} URLs from the database.")
    except psycopg2.Error as e:
        logging.error(f"Database error while fetching URLs: {e}")
    finally:
        if conn:
            conn.close()
    return results

def check_url_scraped(url: str, client_id: Optional[str]) -> bool:
    """
    Checks if a URL (and optionally client_id) has already been scraped with 'completed' status
    in the scraping_logs table.

    Args:
        url (str): The URL to check.
        client_id (Optional[str]): The client_id associated with the URL. If None, the check
                                   is performed based on URL only.

    Returns:
        bool: True if the URL (with the given client_id if provided) has a 'completed' status,
              False otherwise or if an error occurs.
    """
    logging.debug(f"Checking completion status for URL '{url}' (Client: {client_id or 'N/A'}).")
    conn = get_db_connection()
    if not conn:
        return False # Cannot check, assume not scraped or treat as error

    try:
        with conn.cursor() as cur:
            if client_id:
                cur.execute(
                    "SELECT 1 FROM scraping_logs WHERE url_scraped = %s AND client_id = %s AND status = 'completed' LIMIT 1;",
                    (url, client_id)
                )
            else:
                # If client_id is not available, we might still want to check by URL alone,
                # though this could lead to skipping if another client scraped the same generic URL.
                # For now, let's assume if client_id is None, we check only by URL.
                # This behavior might need refinement based on exact requirements.
                logging.warning(f"Checking scraped status for URL '{url}' without a client_id. This might not be specific enough.")
                cur.execute(
                    "SELECT 1 FROM scraping_logs WHERE url_scraped = %s AND status = 'completed' LIMIT 1;",
                    (url,)
                )
            
            result = cur.fetchone()
            if result:
                logging.info(f"URL '{url}' (Client: {client_id or 'N/A'}) already marked as 'completed'. Skipping.")
                return True
            return False
    except psycopg2.Error as e:
        logging.error(f"Database error while checking scraped status for URL '{url}': {e}")
        return False # Treat error as "not completed" to allow retry, or handle as critical error
    finally:
        if conn:
            conn.close()

def log_pending_scrape(url: str, client_id: Optional[str], source: str) -> Optional[str]:
    """
    Logs a URL scrape as 'pending' in the scraping_logs table.
    Uses gen_random_uuid() for log_id as per SQL examples in implementation.txt.

    Args:
        url (str): The URL being scraped.
        client_id (Optional[str]): The client_id associated with the company. Can be None.
        source (str): The source of the URL (e.g., 'homepage', 'linkedin').

    Returns:
        Optional[str]: The log_id (UUID string) of the newly inserted record, or None if insertion fails.
    """
    logging.debug(f"Logging 'pending' status for URL '{url}' (Client: {client_id or 'N/A'}, Source: {source}).")
    conn = get_db_connection()
    if not conn:
        return None

    log_id_val: Optional[str] = None # Renamed to avoid conflict with any potential 'log_id' column name in scope
    try:
        with conn.cursor() as cur:
            # Using gen_random_uuid() as specified in the implementation.txt examples for log_id
            # client_id can be NULL in the database if it's not available.
            cur.execute(
                """
                INSERT INTO scraping_logs (log_id, client_id, source, url_scraped, status, scraping_date, error_message)
                VALUES (gen_random_uuid(), %s, %s, %s, 'pending', NOW(), NULL)
                RETURNING log_id;
                """,
                (client_id, source, url) # psycopg2 handles None as SQL NULL
            )
            log_id_result = cur.fetchone()
            conn.commit()
            if log_id_result and log_id_result[0]:
                log_id_val = str(log_id_result[0])
                logging.info(f"Logged 'pending' scrape for URL '{url}', client_id: {client_id or 'N/A'}, log_id: {log_id_val}")
                return log_id_val
            else:
                logging.error(f"Failed to retrieve log_id after inserting pending scrape for URL '{url}'.")
    except psycopg2.Error as e:
        logging.error(f"Database error while logging 'pending' scrape for URL '{url}': {e}")
        if conn:
            conn.rollback() # Rollback transaction on error
    finally:
        if conn:
            conn.close()
def get_company_name(client_id: str) -> Optional[str]:
    """
    Fetches the company name for a given client_id from the 'companies' table.
    Assumes 'companies' table has 'client_id' (UUID) and 'company_name' (TEXT) columns.

    Args:
        client_id (str): The client_id to search for.

    Returns:
        Optional[str]: The company name, or None if not found or an error occurs.
    """
    logging.debug(f"Fetching company name for client_id: {client_id}")
    conn = get_db_connection()
    if not conn:
        return None
    
    company_name: Optional[str] = None
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                "SELECT company_name FROM companies WHERE client_id = %s;",
                (client_id,)
            )
            row = cur.fetchone()
            if row:
                company_name = row['company_name']
                logging.info(f"Fetched company name '{company_name}' for client_id '{client_id}'.")
            else:
                logging.warning(f"No company found for client_id '{client_id}'.")
    except psycopg2.Error as e:
        logging.error(f"Database error while fetching company name for client_id '{client_id}': {e}")
    finally:
        if conn:
            conn.close()
    return company_name

def fetch_pending_urls(limit: int = 10) -> List[Tuple[str, Optional[str], str]]:
    """
    Fetches a list of URLs marked as 'pending' from the scraping_logs table.

    Args:
        limit (int): The maximum number of pending URLs to fetch.

    Returns:
        List[Tuple[str, Optional[str], str]]: A list of (log_id, client_id, url_scraped) tuples.
                                              Returns an empty list if connection fails or no pending URLs are found.
    """
    logging.debug(f"Attempting to fetch up to {limit} pending URLs from scraping_logs.")
    conn = get_db_connection()
    if not conn:
        return []
    
    results: List[Tuple[str, Optional[str], str]] = []
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                """
                SELECT log_id, client_id, url_scraped 
                FROM scraping_logs 
                WHERE status = 'pending'
                ORDER BY scraping_date ASC -- Process older pending items first
                LIMIT %s;
                """,
                (limit,)
            )
            fetched_rows = cur.fetchall()
            for row in fetched_rows:
                log_id = str(row['log_id'])
                client_id = str(row['client_id']) if row['client_id'] else None
                url_scraped = row['url_scraped']
                results.append((log_id, client_id, url_scraped))
            
            logging.info(f"Fetched {len(results)} pending URLs from scraping_logs.")
    except psycopg2.Error as e:
        logging.error(f"Database error while fetching pending URLs: {e}")
    finally:
        if conn:
            conn.close()
    return results

def update_scraping_log_status(log_id: str, status: str, error_message: Optional[str] = None) -> bool:
    """
    Updates the status of a specific log entry in the scraping_logs table.
    Also updates scraping_date to NOW() upon completion or failure.

    Args:
        log_id (str): The UUID of the log entry to update.
        status (str): The new status ('completed' or 'failed').
        error_message (Optional[str]): An error message if the status is 'failed'.

    Returns:
        bool: True if the update was successful, False otherwise.
    """
    logging.debug(f"Updating scraping_logs for log_id '{log_id}' to status '{status}'.")
    conn = get_db_connection()
    if not conn:
        return False

    success = False
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE scraping_logs
                SET status = %s, error_message = %s, scraping_date = NOW()
                WHERE log_id = %s;
                """,
                (status, error_message, log_id)
            )
            conn.commit()
            if cur.rowcount > 0:
                logging.info(f"Successfully updated log_id '{log_id}' to status '{status}'.")
                success = True
            else:
                logging.warning(f"No log entry found for log_id '{log_id}' to update, or status was already set.")
                # If no row was updated, it might mean the log_id doesn't exist.
                # Depending on strictness, this could be an error or just a warning.
    except psycopg2.Error as e:
        logging.error(f"Database error while updating log_id '{log_id}': {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()
    return success

def insert_scraped_page_data(
    client_id: Optional[str], 
    url: str, 
    page_type: str, 
    raw_html_path: Optional[str], 
    plain_text_path: Optional[str], 
    summary: Optional[str], 
    extraction_notes: Optional[str] = None
) -> Optional[str]:
    """
    Inserts a new record into the scraped_pages table.
    Uses gen_random_uuid() for page_id.

    Args:
        client_id (Optional[str]): The client_id associated with the company.
        url (str): The URL of the scraped page.
        page_type (str): Type of the page (e.g., 'homepage', 'about').
        raw_html_path (Optional[str]): Filesystem path to the raw HTML file.
        plain_text_path (Optional[str]): Filesystem path to the plain text file.
        summary (Optional[str]): A summary of the scraping result.
        extraction_notes (Optional[str]): Any notes related to the extraction.

    Returns:
        Optional[str]: The page_id (UUID string) of the newly inserted record, or None if insertion fails.
    """
    logging.debug(f"Inserting scraped page data for URL '{url}', client_id: {client_id or 'N/A'}.")
    conn = get_db_connection()
    if not conn:
        return None

    page_id_val: Optional[str] = None
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO scraped_pages (
                    page_id, client_id, url, page_type, scraped_at,
                    raw_html_path, plain_text_path, summary, extraction_notes
                ) VALUES (
                    gen_random_uuid(), %s, %s, %s, NOW(),
                    %s, %s, %s, %s
                )
                RETURNING page_id;
                """,
                (
                    client_id, url, page_type, 
                    raw_html_path, plain_text_path, 
                    summary, extraction_notes
                )
            )
            page_id_result = cur.fetchone()
            conn.commit()
            if page_id_result and page_id_result[0]:
                page_id_val = str(page_id_result[0])
                logging.info(f"Successfully inserted into scraped_pages for URL '{url}', page_id: {page_id_val}.")
            else:
                logging.error(f"Failed to retrieve page_id after inserting into scraped_pages for URL '{url}'.")
    except psycopg2.Error as e:
        logging.error(f"Database error while inserting into scraped_pages for URL '{url}': {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()
    return page_id_val