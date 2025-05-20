import logging
import os
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Iterator, Dict, Any, Tuple
from urllib.parse import urlparse
import sys
import psutil # Used in finally block for playwright cleanup
from tqdm import tqdm # For progress bars
import time # For placeholder log_id

# Local imports
from . import config # Reads from .env
from .scraper import scrape_page # Core scraping function
from . import db_utils # For database interactions
from . import ocr # For generate_ocr_summary (used in process_single_pending_url)
from .url_processor import process_pending_urls_loop # For processing pending queue from DB
from .utils import (
    validate_url, # Used in process_single_pending_url
    create_metadata, # Used in process_single_pending_url
    normalize_hostname, # Used in get_output_paths
    get_url_specific_safe_dirname # Used in process_single_pending_url
)
from .exceptions import ScrapingError, InvalidURLError, ConnectionError, ParsingError, OCRError

class ScrapingSession:
    """Tracks metrics across multiple URLs in a scraping session."""
    
    def __init__(self):
        self.total_urls = 0
        self.total_time = 0.0
        self.total_ocr_attempts = 0
        self.total_ocr_successes = 0
        self.total_ocr_no_text_found = 0
        self.total_ocr_errors_unsupported = 0
        self.total_ocr_errors_processing = 0
        self.total_ocr_errors_file_not_found = 0
        self.total_ocr_errors_tesseract = 0
        self.start_time = datetime.now()
        self.failed_urls: List[Tuple[str, Optional[ScrapingError]]] = []
        self.successful_urls: List[str] = []
        self.warnings: List[Tuple[str, str, datetime]] = []
        self.errors: List[Tuple[str, str, datetime]] = []
    
    def add_url_result(self, url: str, summary: Dict[str, Any], success: bool, error: Optional[ScrapingError] = None) -> None:
        self.total_urls += 1
        if 'timestamp' in summary and 'duration_seconds' in summary['timestamp']:
             self.total_time += summary['timestamp']['duration_seconds']
        
        if 'extraction' in summary and 'metrics' in summary['extraction']:
            metrics = summary['extraction']['metrics']
            self.total_ocr_attempts += metrics.get('ocr_attempts', 0) # This is total images OCR was attempted on
            self.total_ocr_successes += metrics.get('ocr_successes', 0) # Text successfully extracted
            self.total_ocr_no_text_found += metrics.get('ocr_no_text_found_count', 0)
            self.total_ocr_errors_unsupported += metrics.get('ocr_error_unsupported_format_count', 0)
            self.total_ocr_errors_processing += metrics.get('ocr_error_processing_count', 0)
            self.total_ocr_errors_file_not_found += metrics.get('ocr_error_file_not_found_count', 0)
            self.total_ocr_errors_tesseract += metrics.get('ocr_error_tesseract_count', 0)
        
        if success:
            self.successful_urls.append(url)
        else:
            self.failed_urls.append((url, error))
    
    def add_warning(self, url: str, warning_message: str) -> None:
        self.warnings.append((url, warning_message, datetime.now()))
    
    def add_error(self, url: str, error_message: str) -> None:
        self.errors.append((url, error_message, datetime.now()))
    
    def get_session_summary(self) -> Dict[str, Any]:
        end_time = datetime.now()
        total_duration = (end_time - self.start_time).total_seconds()
        total_ocr_errors = (
            self.total_ocr_errors_unsupported +
            self.total_ocr_errors_processing +
            self.total_ocr_errors_file_not_found +
            self.total_ocr_errors_tesseract
        )
        # Base success rate on attempts that didn't error out before OCR could run meaningfully
        # or on images that were processable but yielded no text.
        # Attempts = success + no_text_found + errors
        meaningful_attempts = self.total_ocr_successes + self.total_ocr_no_text_found
        
        avg_ocr_success_rate = 0.0
        if meaningful_attempts > 0 : # Avoid division by zero if all attempts resulted in errors
            avg_ocr_success_rate = (self.total_ocr_successes / meaningful_attempts) * 100
        elif self.total_ocr_attempts > 0 and total_ocr_errors == self.total_ocr_attempts: # All attempts were errors
             avg_ocr_success_rate = 0.0 # Or handle as undefined, but 0% is clear
        # If total_ocr_attempts is 0, it remains 0.0
        
        return {
            'session_duration': {
                'start': self.start_time.isoformat(),
                'end': end_time.isoformat(),
                'total_seconds': total_duration
            },
            'urls_processed': {
                'total': self.total_urls,
                'successful': len(self.successful_urls),
                'failed': len(self.failed_urls)
            },
            'ocr_metrics': {
                'total_images_ocr_attempted': self.total_ocr_attempts, # Renamed for clarity
                'total_ocr_successful_extraction': self.total_ocr_successes, # Renamed
                'total_ocr_no_text_found': self.total_ocr_no_text_found,
                'total_ocr_errors_unsupported_format': self.total_ocr_errors_unsupported,
                'total_ocr_errors_processing': self.total_ocr_errors_processing,
                'total_ocr_errors_file_not_found': self.total_ocr_errors_file_not_found,
                'total_ocr_errors_tesseract': self.total_ocr_errors_tesseract,
                'total_ocr_errors_sum': total_ocr_errors,
                'average_success_rate_on_processable': round(avg_ocr_success_rate, 2) # Clarified rate
            },
            'performance': {
                'total_processing_time': round(self.total_time, 2),
                'average_time_per_url': round(self.total_time / self.total_urls if self.total_urls > 0 else 0, 2)
            },
            'warnings_and_errors': {
                'warnings': [{'url': u, 'message': m, 'timestamp': t.isoformat()} for u, m, t in self.warnings],
                'errors': [{'url': u, 'message': m, 'timestamp': t.isoformat()} for u, m, t in self.errors]
            }
        }

def get_output_paths(base_dir: Path, url: str, content_type: str = 'text') -> Dict[str, Path]:
    hostname = normalize_hostname(url)
    extensions = {
        'text': '.txt', 'json': '.json', 'html': '.html',
        'raw': '.raw', 'ocr': '.ocr.txt', 'ocr_summary': '.ocr.json'
    }
    run_dir = config.get_run_directory()
    paths = {
        'page': run_dir / config.PAGES_SUBDIR / hostname / f"page{extensions['html']}",
        'text': run_dir / config.PAGES_SUBDIR / hostname / f"text{extensions['text']}",
        'raw_text': run_dir / config.PAGES_SUBDIR / hostname / f"raw{extensions['raw']}",
        'images_dir': run_dir / config.IMAGES_SUBDIR / hostname,
        'ocr_dir': run_dir / config.PAGES_SUBDIR / hostname / config.OCR_SUBDIR,
        'ocr_summary': run_dir / config.PAGES_SUBDIR / hostname / config.OCR_SUBDIR / f"summary{extensions['ocr_summary']}"
    }
    for path_obj in paths.values():
        if isinstance(path_obj, Path):
            path_obj.parent.mkdir(parents=True, exist_ok=True)
    return paths

def generate_scraping_summary(url: str, result: Dict[str, Any], start_time: datetime) -> Dict[str, Any]:
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    output_paths = get_output_paths(config.DATA_DIR, url) # DATA_DIR is the base for run_dir
    
    output_paths['page'].parent.mkdir(parents=True, exist_ok=True)
    
    image_stats = {
        'total_images_found': 0, 'by_type': {}, 'total_size_bytes': 0, 'extensions': set(),
        'ocr_attempts': 0, # Total images OCR was attempted on
        'ocr_successes': 0, # Text successfully extracted
        'ocr_no_text_found_count': 0,
        'ocr_error_unsupported_format_count': 0,
        'ocr_error_processing_count': 0,
        'ocr_error_file_not_found_count': 0,
        'ocr_error_tesseract_count': 0,
        'ocr_total_errors': 0,
        'ocr_success_rate_on_processable': 0.0
    }
    if 'images' in result and isinstance(result['images'], list):
        image_stats['total_images_found'] = len(result['images']) # This is the number of images found on page
        image_stats['ocr_attempts'] = len(result['images']) # OCR is attempted on all found images

        for img in result['images']: # result['images'] contains ocr_item dicts from scraper.py
            img_type = img.get('image_type', 'unknown')
            image_stats['by_type'][img_type] = image_stats['by_type'].get(img_type, 0) + 1
            if 'extension' in img: image_stats['extensions'].add(img['extension'])
            image_stats['total_size_bytes'] += img.get('size_bytes', 0)

            ocr_status = img.get('ocr_status', 'error_processing') # Get the detailed status
            if ocr_status == 'success':
                image_stats['ocr_successes'] += 1
            elif ocr_status == 'no_text_found':
                image_stats['ocr_no_text_found_count'] += 1
            elif ocr_status == 'error_unsupported_format':
                image_stats['ocr_error_unsupported_format_count'] += 1
            elif ocr_status == 'error_processing':
                image_stats['ocr_error_processing_count'] += 1
            elif ocr_status == 'error_file_not_found':
                image_stats['ocr_error_file_not_found_count'] += 1
            elif ocr_status == 'error_tesseract':
                image_stats['ocr_error_tesseract_count'] += 1
        
        image_stats['ocr_total_errors'] = (
            image_stats['ocr_error_unsupported_format_count'] +
            image_stats['ocr_error_processing_count'] +
            image_stats['ocr_error_file_not_found_count'] +
            image_stats['ocr_error_tesseract_count']
        )
        
        meaningful_attempts_for_rate = image_stats['ocr_successes'] + image_stats['ocr_no_text_found_count']
        if meaningful_attempts_for_rate > 0:
            image_stats['ocr_success_rate_on_processable'] = (image_stats['ocr_successes'] / meaningful_attempts_for_rate) * 100
        elif image_stats['ocr_attempts'] > 0 and image_stats['ocr_total_errors'] == image_stats['ocr_attempts']:
             image_stats['ocr_success_rate_on_processable'] = 0.0


    ocr_summary_present = image_stats['ocr_successes'] > 0 # Keep this for deciding if OCR summary file is written

    if image_stats.get('total_images_found', 0) > 0: # Check if any images were found to process
        output_paths['images_dir'].mkdir(parents=True, exist_ok=True)
        if ocr_summary_present:
             (output_paths['ocr_dir']).mkdir(parents=True, exist_ok=True)

    image_stats['extensions'] = sorted(list(image_stats['extensions']))
    
    text_stats = {'length': 0, 'word_count': 0, 'paragraph_count': 0, 'has_content': False, 'format': 'plain'}
    if 'text_data' in result and isinstance(result['text_data'], dict):
        text_stats['length'] = result['text_data'].get('text_length',0)
        text_stats['word_count'] = result['text_data'].get('word_count',0)
        text_stats['paragraph_count'] = result['text_data'].get('paragraph_count', 0)
        text_stats['has_content'] = bool(result.get('text',"").strip())
        text_stats['format'] = result.get('text_format', 'plain')

    summary = {
        'timestamp': {'start': start_time.isoformat(), 'end': end_time.isoformat(), 'duration_seconds': duration},
        'url': {'original': url, 'parsed': urlparse(url).geturl()},
        'extraction': {
            'success': bool(result), 'text': text_stats, 'images': image_stats,
            'metrics': {
                'total_time_seconds': duration,
                'ocr_attempts': image_stats['ocr_attempts'], # Total images OCR was attempted on
                'ocr_successes': image_stats['ocr_successes'], # Text successfully extracted
                'ocr_no_text_found_count': image_stats['ocr_no_text_found_count'],
                'ocr_error_unsupported_format_count': image_stats['ocr_error_unsupported_format_count'],
                'ocr_error_processing_count': image_stats['ocr_error_processing_count'],
                'ocr_error_file_not_found_count': image_stats['ocr_error_file_not_found_count'],
                'ocr_error_tesseract_count': image_stats['ocr_error_tesseract_count'],
                'ocr_total_errors': image_stats['ocr_total_errors'],
                'ocr_success_rate_on_processable': round(image_stats['ocr_success_rate_on_processable'], 2)
            }
        },
        'output_files': {
            'page': str(output_paths['page']), 'text': str(output_paths['text']),
            'raw_text': str(output_paths['raw_text']),
            'images_dir': str(output_paths['images_dir']) if image_stats.get('total_images_found', 0) > 0 else None,
            'ocr_dir': str(output_paths['ocr_dir']) if ocr_summary_present else None,
            'ocr_summary': str(output_paths['ocr_summary']) if ocr_summary_present else None
        }
    }
    return summary

def log_scraping_summary(summary: Dict[str, Any]) -> None:
    logging.info("\n\n[SUMMARY] Scraping Result Summary:")
    logging.info(f"URL: {summary['url']['original']}")
    logging.info(f"Duration: {summary['timestamp']['duration_seconds']:.2f} seconds")
    text_stats = summary['extraction']['text']
    logging.info("\n[TEXT] Text Statistics:")
    logging.info(f"• Length: {text_stats['length']} characters, Words: {text_stats['word_count']}, Paragraphs: {text_stats['paragraph_count']}")
    logging.info(f"• Format: {text_stats['format']}, Has content: {'Yes' if text_stats['has_content'] else 'No'}")
    img_stats = summary['extraction']['images'] # This is the image_stats dict from generate_scraping_summary
    logging.info("\n[IMAGES] Image Statistics:")
    logging.info(f"• Total images found on page: {img_stats.get('total_images_found',0)}") # Changed from 'total'
    logging.info(f"• Total size: {img_stats.get('total_size_bytes', 0) / 1024:.2f} KB")
    logging.info(f"• OCR Attempts: {img_stats.get('ocr_attempts',0)}")
    logging.info(f"  - Successful Text Extraction: {img_stats.get('ocr_successes',0)}")
    logging.info(f"  - No Text Found (but processed): {img_stats.get('ocr_no_text_found_count',0)}")
    logging.info(f"  - Errors (Unsupported Format): {img_stats.get('ocr_error_unsupported_format_count',0)}")
    logging.info(f"  - Errors (Processing): {img_stats.get('ocr_error_processing_count',0)}")
    logging.info(f"  - Errors (File Not Found): {img_stats.get('ocr_error_file_not_found_count',0)}")
    logging.info(f"  - Errors (Tesseract): {img_stats.get('ocr_error_tesseract_count',0)}")
    logging.info(f"  - Total OCR Errors: {img_stats.get('ocr_total_errors',0)}")
    logging.info(f"• OCR Success Rate (on processable images): {img_stats.get('ocr_success_rate_on_processable',0.0):.1f}%")
    if img_stats['by_type']: logging.info(f"• Types: {img_stats['by_type']}")
    if img_stats['extensions']: logging.info(f"• File extensions: {', '.join(sorted(img_stats['extensions']))}")
    # The metrics in summary['extraction']['metrics'] already contain the detailed OCR counts
    # So no need to log them again here if they are covered by the [IMAGES] section.
    # We can log other non-OCR metrics if needed.
    metrics = summary['extraction']['metrics']
    logging.info("\n[METRICS] Performance Metrics (Overall Page):")
    logging.info(f"• Total page processing time: {metrics['total_time_seconds']:.2f}s")
    # Individual OCR counts are now part of img_stats and logged above.
    # logging.info(f"• OCR attempts: {metrics['ocr_attempts']}, OCR successes: {metrics['ocr_successes']}")
    logging.info("\n[SAVED] Output Files:")
    for key, path in summary['output_files'].items(): logging.info(f"• {key}: {path}")
    logging.info("\n[STATS] JSON Summary:\n" + json.dumps(summary, indent=2))

def log_session_summary(session: ScrapingSession) -> None:
    summary = session.get_session_summary()
    logging.info("\n\n[SESSION SUMMARY] Overall Scraping Session Results:")
    logging.info(f"Session Duration: {summary['session_duration']['total_seconds']:.2f} seconds")
    logging.info("\n[URLS] Processing Statistics:")
    logging.info(f"• Total: {summary['urls_processed']['total']}, Successful: {summary['urls_processed']['successful']}, Failed: {summary['urls_processed']['failed']}")
    logging.info("\n[OCR] Overall OCR Statistics (Aggregated for Session):")
    ocr_m = summary['ocr_metrics']
    logging.info(f"• Total Images OCR Attempted: {ocr_m.get('total_images_ocr_attempted',0)}")
    logging.info(f"  - Successful Text Extraction: {ocr_m.get('total_ocr_successful_extraction',0)}")
    logging.info(f"  - No Text Found (but processed): {ocr_m.get('total_ocr_no_text_found',0)}")
    logging.info(f"  - Errors (Unsupported Format): {ocr_m.get('total_ocr_errors_unsupported_format',0)}")
    logging.info(f"  - Errors (Processing): {ocr_m.get('total_ocr_errors_processing',0)}")
    logging.info(f"  - Errors (File Not Found): {ocr_m.get('total_ocr_errors_file_not_found',0)}")
    logging.info(f"  - Errors (Tesseract): {ocr_m.get('total_ocr_errors_tesseract',0)}")
    logging.info(f"  - Sum of All OCR Errors: {ocr_m.get('total_ocr_errors_sum',0)}")
    logging.info(f"• Avg Success Rate (on processable images): {ocr_m.get('average_success_rate_on_processable',0.0):.2f}%")
    logging.info("\n[PERFORMANCE] Processing Performance:")
    logging.info(f"• Total processing time: {summary['performance']['total_processing_time']:.2f}s, Avg time/URL: {summary['performance']['average_time_per_url']:.2f}s")
    logging.info("\n[WARNINGS AND ERRORS] Summary:")
    for type_key in ['warnings', 'errors']:
        if summary['warnings_and_errors'][type_key]:
            logging.info(f"\n{type_key.upper()}:")
            for item in summary['warnings_and_errors'][type_key]:
                logging.info(f"• URL: {item['url']}, Time: {item['timestamp']}, Message: {item['message']}")
        else:
            logging.info(f"\nNo {type_key} recorded.")
    if session.failed_urls:
        logging.info("\n[FAILED] Failed URLs:")
        for url, error in session.failed_urls:
            error_details = f"Error Type: {error.error_type}, Msg: {str(error)}, Details: {error.details}" if error else "Unknown error"
            logging.info(f"• {url} - {error_details}")
    logging.info("\n[STATS] Session JSON Summary:\n" + json.dumps(summary, indent=2))

def read_urls_from_file(file_path_str: str) -> Iterator[str]:
    file_path = Path(file_path_str)
    if not file_path.is_file():
        raise FileNotFoundError(f"URL file not found: {file_path_str}")
    with open(file_path, 'r') as f:
        urls = [line.strip() for line in f if line.strip()]
    if not urls:
        raise InvalidURLError(f"No URLs found in file: {file_path_str}")
    for url in urls:
        yield url

def setup_logging() -> None: # debug_mode now comes from config
    from .logging_utils import configure_logging, info as log_info # Avoid conflict
    # Use specific log level configs, defaulting to INFO if not valid or not set
    # The .upper() and default 'INFO' are handled in config.py
    console_level = config.SCRAPER_CONSOLE_LOG_LEVEL
    file_log_level = config.SCRAPER_FILE_LOG_LEVEL
    # Ensure LOG_FILE parent directory exists
    log_file_path = Path(config.LOG_FILE)
    log_file_path.parent.mkdir(parents=True, exist_ok=True)

    configure_logging(
        log_file=log_file_path,
        console_level=console_level,
        file_level=file_log_level,
        include_emojis=True,
        include_context=True
    )
    log_info(f"Logging configured with debug mode: {config.SCRAPER_DEBUG_MODE}", category='CONFIG')

def write_session_log(session: ScrapingSession, run_dir: Path) -> None:
    log_file = run_dir / "session_details.log"
    summary = session.get_session_summary()
    try:
        with open(log_file, 'w', encoding='utf-8') as f:
            f.write("SESSION SUMMARY:\n")
            f.write(json.dumps(summary, indent=2))
            f.write("\n\nDETAILED LOGS:\n")
            f.write("Successful URLs:\n")
            for url in session.successful_urls:
                f.write(f"- {url}\n")
            f.write("\nFailed URLs:\n")
            for url, error in session.failed_urls:
                error_str = str(error) if error else "N/A"
                f.write(f"- {url}: {error_str}\n")
            f.write("\nWarnings:\n")
            for url, msg, ts in session.warnings:
                f.write(f"- [{ts.isoformat()}] {url}: {msg}\n")
            f.write("\nErrors (captured by handler):\n")
            for url, msg, ts in session.errors:
                f.write(f"- [{ts.isoformat()}] {url}: {msg}\n")
        logging.info(f"Session log written to {log_file}")
    except IOError as e:
        logging.error(f"Failed to write session log: {e}")

def update_history_log(session: ScrapingSession) -> None:
    if not config.SCRAPER_USE_DATABASE:
        logging.info("Database disabled. Skipping history log update.")
        return
    logging.info("update_history_log called. (Actual DB interaction depends on SCRAPER_USE_DATABASE)")

def get_formatted_output_paths(run_dir: Path, company_name: str, url: str) -> Dict[str, Path]:
    hostname = normalize_hostname(url)
    safe_company_name = "".join(c for c in company_name if c.isalnum() or c in (' ', '-', '_')).strip().replace(' ', '_')
    base_path = run_dir / safe_company_name / hostname
    base_path.mkdir(parents=True, exist_ok=True)
    return {
        'html': base_path / "page.html",
        'text': base_path / "text.txt",
        'images_dir': base_path / "images"
    }

def process_single_pending_url(
    log_id: Optional[str], 
    client_id: Optional[str],
    url_to_scrape: str,
    run_dir: Path, 
    scrape_session: ScrapingSession, 
    scrape_mode: str, 
    debug_mode: bool
) -> None:
    start_time = datetime.now()
    page_data = None
    success_flag = False
    scraping_error_obj: Optional[ScrapingError] = None

    try:
        logging.info(f"Processing URL: {url_to_scrape} (Client: {client_id or 'N/A'}, Log ID: {log_id or 'N/A - DB Disabled'})")
        
        is_valid, validation_msg = validate_url(url_to_scrape)
        if not is_valid:
            raise InvalidURLError(validation_msg, details={'url': url_to_scrape})

        # The get_url_specific_safe_dirname was previously called with company_name,
        # but it only accepts a URL. The output directory structure is primarily handled
        # by get_output_paths using the run_dir and hostname.
        # If a company-specific sub-folder within the run_dir/hostname was intended,
        # that logic would need to be integrated into get_output_paths or scrape_page.
        # For now, removing the problematic call to get_url_specific_safe_dirname.
        # url_specific_dir_name = get_url_specific_safe_dirname(url_to_scrape) # Corrected call, but output not used.
        
        page_data = scrape_page(
            url=url_to_scrape,
            scrape_mode=scrape_mode,
        )

        if page_data:
            logging.info(f"Successfully scraped content from {url_to_scrape}")
            success_flag = True
            if config.SCRAPER_USE_DATABASE and log_id:
                db_utils.update_scraping_log_status(log_id, 'completed')
            
            if config.SCRAPER_USE_DATABASE:
                summary_for_db = generate_scraping_summary(url_to_scrape, page_data, start_time)
                db_utils.insert_scraped_page_data(
                    client_id=client_id,
                    url=url_to_scrape,
                    page_type="website", 
                    raw_html_path=summary_for_db['output_files'].get('page'),
                    plain_text_path=summary_for_db['output_files'].get('text'),
                    summary=json.dumps(summary_for_db['extraction']),
                    extraction_notes=f"Mode: {scrape_mode}"
                )
        else:
            raise ScrapingError("Scraping returned no data.", error_type="NoData", details={'url': url_to_scrape})

    except InvalidURLError as e:
        logging.error(f"Invalid URL {url_to_scrape}: {e}")
        scraping_error_obj = e # e already includes details if provided at raise
    except ConnectionError as e:
        logging.error(f"Connection error for {url_to_scrape}: {e}")
        scraping_error_obj = e
    except ParsingError as e:
        logging.error(f"Parsing error for {url_to_scrape}: {e}")
        scraping_error_obj = e
    except OCRError as e:
        logging.error(f"OCR error for {url_to_scrape}: {e}")
        scraping_error_obj = e
    except ScrapingError as e: 
        logging.error(f"Scraping error for {url_to_scrape}: {e}")
        scraping_error_obj = e
    except Exception as e: 
        logging.error(f"Unexpected error processing {url_to_scrape}: {e}", exc_info=debug_mode)
        scraping_error_obj = ScrapingError(f"Unexpected error: {str(e)}", error_type="Unexpected", details={'url': url_to_scrape})
    finally:
        final_summary_data = page_data if page_data else {
            'images': [], 'text_data': {}, 'text': '',
        }
        current_summary = generate_scraping_summary(url_to_scrape, final_summary_data, start_time)
        scrape_session.add_url_result(url_to_scrape, current_summary, success_flag, scraping_error_obj)
        
        if not success_flag and config.SCRAPER_USE_DATABASE and log_id:
            error_msg_for_db = str(scraping_error_obj)[:1023] if scraping_error_obj else "Unknown error during processing"
            db_utils.update_scraping_log_status(log_id, 'failed', error_message=error_msg_for_db)
        
        if debug_mode or not success_flag : 
             log_scraping_summary(current_summary)

def write_run_summary(session: ScrapingSession, run_dir: Path) -> None:
    summary_file = run_dir / "run_summary.json"
    try:
        with open(summary_file, 'w', encoding='utf-8') as f:
            json.dump(session.get_session_summary(), f, indent=2)
        logging.info(f"Run summary written to {summary_file}")
    except IOError as e:
        logging.error(f"Failed to write run summary: {e}")

def main() -> None:
    setup_logging() 

    run_dir = config.get_run_directory() 
    logging.info(f"Scraper run starting. Output directory: {run_dir}")

    session = ScrapingSession()

    class WarningErrorHandler(logging.Handler):
        def emit(self, record):
            url_context = getattr(record, 'url', 'N/A') 
            if record.levelno == logging.WARNING:
                session.add_warning(url_context, record.getMessage())
            elif record.levelno >= logging.ERROR:
                session.add_error(url_context, record.getMessage())
    
    warning_error_handler = WarningErrorHandler()
    warning_error_handler.setLevel(logging.WARNING)
    logging.getLogger().addHandler(warning_error_handler)

    urls_to_process: List[Tuple[Optional[str], str]] = []
    source_description = "No source"

    try:
        if config.SCRAPER_TARGET_URL:
            urls_to_process = [(None, config.SCRAPER_TARGET_URL)]
            source_description = f"single URL: {config.SCRAPER_TARGET_URL}"
        elif config.SCRAPER_URL_FILE_PATH:
            source_description = f"file: {config.SCRAPER_URL_FILE_PATH}"
            try:
                urls_to_process = [(None, url) for url in read_urls_from_file(config.SCRAPER_URL_FILE_PATH)]
            except FileNotFoundError:
                logging.error(f"URL file not found: {config.SCRAPER_URL_FILE_PATH}")
                sys.exit(1)
            except InvalidURLError as e: # This InvalidURLError is from read_urls_from_file
                logging.error(f"Error reading URL file: {str(e)}")
                sys.exit(1)
        elif config.SCRAPER_USE_DATABASE and config.SCRAPER_SOURCE_FROM_DB:
            offset_val = 0
            limit_val = config.SCRAPER_DB_NUM_URLS
            source_description_detail = f"limit: {limit_val}, offset: {offset_val}"

            if config.SCRAPER_DB_RANGE:
                try:
                    if '-' not in config.SCRAPER_DB_RANGE:
                        raise ValueError("Invalid SCRAPER_DB_RANGE format. Expected START-END.")
                    start_str, end_str = config.SCRAPER_DB_RANGE.split('-')
                    start_index, end_index = int(start_str), int(end_str)
                    if start_index < 0 or end_index < start_index: 
                        raise ValueError("Invalid SCRAPER_DB_RANGE values.")
                    offset_val = start_index
                    limit_val = end_index - start_index + 1 # Inclusive range for user, so +1 for limit
                    source_description_detail = f"range: {config.SCRAPER_DB_RANGE} (limit: {limit_val}, offset: {offset_val})"
                except ValueError as e:
                    logging.error(f"Invalid SCRAPER_DB_RANGE '{config.SCRAPER_DB_RANGE}': {e}. Using default num_urls.")
            
            source_description = f"database ({source_description_detail})"
            logging.info(f"Attempting to fetch URLs from {source_description}")
            db_url_tuples = db_utils.fetch_urls_from_db(limit=limit_val, offset=offset_val)
            if db_url_tuples:
                urls_to_process = db_url_tuples
                logging.info(f"Successfully fetched {len(urls_to_process)} URLs from the database.")
            else:
                logging.info("No URLs fetched from the database based on the criteria.")
        else:
            logging.error("No URL source specified. Configure SCRAPER_TARGET_URL, SCRAPER_URL_FILE_PATH, or enable SCRAPER_USE_DATABASE and SCRAPER_SOURCE_FROM_DB in .env file.")
            sys.exit(1)

        if not urls_to_process:
            logging.info(f"No URLs to process from {source_description}.")
        else:
            logging.info(f"Processing {len(urls_to_process)} URLs from {source_description}")
            
            progress_bar = None
            if not config.SCRAPER_DEBUG_MODE and len(urls_to_process) > 1:
                print(f"\nStarting batch processing of {len(urls_to_process)} URLs from {source_description}...")
                progress_bar = tqdm(total=len(urls_to_process), desc="Processing URLs", unit="url")

            for client_id_from_source, url_from_source in urls_to_process:
                log_id_for_this_url: Optional[str] = None
                if config.SCRAPER_USE_DATABASE:
                    if db_utils.check_url_scraped(url_from_source, client_id_from_source):
                        logging.info(f"DB: Skipping already completed URL: {url_from_source} (Client: {client_id_from_source or 'N/A'})")
                        if progress_bar: progress_bar.update(1)
                        session.add_url_result(url_from_source, {'timestamp': {'duration_seconds': 0}, 'extraction': {'metrics': {}}}, True)
                        continue
                    
                    log_id_for_this_url = db_utils.log_pending_scrape(url_from_source, client_id_from_source, source=source_description)
                    if not log_id_for_this_url:
                        logging.error(f"DB: Failed to log 'pending' status for {url_from_source}. Skipping.")
                        session.add_url_result(url_from_source, {'timestamp': {'duration_seconds': 0}, 'extraction': {'metrics': {}}}, False, ScrapingError("DB pending log failed", details={'url': url_from_source}))
                        if progress_bar: progress_bar.update(1)
                        continue
                    logging.info(f"DB: Logged 'pending' for {url_from_source}, log_id: {log_id_for_this_url}")
                else:
                    log_id_for_this_url = f"local_{time.time()}" 
                    logging.info(f"DB disabled. Processing locally: {url_from_source}")

                process_single_pending_url(
                    log_id=log_id_for_this_url,
                    client_id=client_id_from_source,
                    url_to_scrape=url_from_source,
                    run_dir=run_dir,
                    scrape_session=session,
                    scrape_mode=config.SCRAPER_MODE,
                    debug_mode=config.SCRAPER_DEBUG_MODE
                )
                if progress_bar:
                    progress_bar.update(1)
                    progress_bar.set_postfix_str(f"Success: {len(session.successful_urls)}, Failed: {len(session.failed_urls)}")
            
            if progress_bar:
                progress_bar.close()
                print(f"\nProcessing complete! Success: {len(session.successful_urls)}, Failed: {len(session.failed_urls)}")

        if config.SCRAPER_USE_DATABASE and config.SCRAPER_DB_PENDING_BATCH_SIZE > 0:
            logging.info(f"DB: Checking for 'pending' URLs from previous runs (batch size: {config.SCRAPER_DB_PENDING_BATCH_SIZE}).")
            process_pending_urls_loop(
                scrape_session=session,
                run_dir=run_dir,
                scrape_mode=config.SCRAPER_MODE,
                debug_mode=config.SCRAPER_DEBUG_MODE,
                num_to_process=config.SCRAPER_DB_PENDING_BATCH_SIZE
            )
        elif config.SCRAPER_DB_PENDING_BATCH_SIZE <= 0 and config.SCRAPER_USE_DATABASE : # only log if db is enabled but batch size is 0
             logging.info("DB enabled, but skipping processing of pending queue as SCRAPER_DB_PENDING_BATCH_SIZE is 0 or less.")
        elif not config.SCRAPER_USE_DATABASE:
            logging.info("DB disabled. Skipping processing of pending queue.")

    except KeyboardInterrupt:
        logging.info("\n[INTERRUPT] Scraping interrupted by user. Finalizing logs...")
    except Exception as e:
        logging.critical(f"An unhandled exception occurred in main: {e}", exc_info=True)
    finally:
        logging.info("Finalizing run...")
        log_session_summary(session) 
        write_session_log(session, run_dir) 
        if config.SCRAPER_USE_DATABASE: 
            update_history_log(session)
        write_run_summary(session, run_dir) 
        
        logging.info(f"Run finalized. Results and logs in: {run_dir}")

        try:
            from playwright.sync_api import sync_playwright 
            with sync_playwright() as p:
                browser = getattr(p, '_default_browser', None)
                if browser and browser.is_connected():
                    logging.debug("Attempting to close Playwright default browser.")
                    browser.close()
        except ImportError:
            logging.debug("Playwright not installed or accessible, skipping cleanup.")
        except Exception as e:
            logging.debug(f"Error during Playwright browser cleanup: {str(e)}")
        
        try:
            for proc in psutil.process_iter(['pid', 'name']):
                if 'playwright' in proc.info['name'].lower():
                    logging.debug(f"Attempting to kill lingering Playwright process: {proc.info['name']} (PID: {proc.info['pid']})")
                    proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass 
        except Exception as e:
            logging.warning(f"Error during Playwright process cleanup: {str(e)}")
            
        logging.info("Scraper finished.")
        logging.shutdown()

if __name__ == "__main__":
    main()