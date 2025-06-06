import logging
from pathlib import Path
from tqdm import tqdm

from scraper_app.main import ScrapingSession, process_single_pending_url
from scraper_app import db_utils

def process_pending_urls_loop(
    scrape_session: ScrapingSession, 
    run_dir: Path, 
    scrape_mode: str, 
    debug_mode: bool,
    num_to_process: int = 10  # Max number of pending URLs to process in one go
) -> None:
    """
    Fetches and processes URLs marked as 'pending' in the scraping_logs table.
    This is the main loop for Step 3.
    """
    logging.info(f"Starting Step 3: Processing up to {num_to_process} 'pending' URLs from scraping_logs.")
    pending_urls_data = db_utils.fetch_pending_urls(limit=num_to_process)

    if not pending_urls_data:
        logging.info("No 'pending' URLs found in scraping_logs to process in this batch.")
        return

    logging.info(f"Found {len(pending_urls_data)} 'pending' URLs to process.")
    
    progress_bar = tqdm(pending_urls_data, desc="Step 3: Processing Pending URLs", unit="url", disable=debug_mode)
    for log_id, client_id, url_to_scrape in progress_bar:
        progress_bar.set_postfix_str(f"{url_to_scrape[:50]}...")
        # process_single_pending_url contains the logic for scraping, saving, and DB updates for one URL
        process_single_pending_url(
            log_id=log_id,
            client_id=client_id,
            url_to_scrape=url_to_scrape,
            run_dir=run_dir,
            scrape_session=scrape_session,
            scrape_mode=scrape_mode,
            debug_mode=debug_mode
        )
    logging.info(f"Finished processing batch of {len(pending_urls_data)} 'pending' URLs.")

if __name__ == "__main__":
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    # Import here to avoid circular imports
    from scraper_app.main import ScrapingSession, setup_logging
    from scraper_app.config import initialize_run_directory
    
    # Initialize session and run directory
    setup_logging(debug_mode=True)
    scrape_session = ScrapingSession()
    run_dir = initialize_run_directory("pending_urls_processing")
    
    # Run the processing loop
    process_pending_urls_loop(
        scrape_session=scrape_session,
        run_dir=run_dir,
        scrape_mode="both",  # Default to both text and images
        debug_mode=True,
        num_to_process=10
    )