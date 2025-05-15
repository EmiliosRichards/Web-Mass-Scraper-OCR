import argparse
import logging
import os
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Iterator, Dict, Any, Tuple
from urllib.parse import urlparse, ParseResult
import requests
from requests.exceptions import RequestException
import sys
import psutil
from tqdm import tqdm
import time
from httpx import AsyncClient
from sqlalchemy.orm import Session
from .rate_limiter import RateLimiter  # Corrected import for RateLimiter

# This check is no longer needed as we've restructured the imports
# to avoid circular dependencies

from . import config
from .scraper import scrape_page
from . import db_utils # Import the new db_utils module
from . import ocr # For generate_ocr_summary
from .url_processor import process_pending_urls_loop
from .utils import (
    validate_url,
    create_metadata,
    normalize_hostname,
    get_url_specific_safe_dirname # ADDED
)

from .exceptions import ScrapingError, InvalidURLError, ConnectionError, ParsingError, OCRError

class ScrapingSession:
    """Tracks metrics across multiple URLs in a scraping session."""
    
    def __init__(self):
        self.total_urls = 0
        self.total_time = 0.0
        self.total_ocr_attempts = 0
        self.total_ocr_successes = 0
        self.start_time = datetime.now()
        self.failed_urls = []  # List of tuples (url, error)
        self.successful_urls = []
        self.warnings = []  # List of tuples (url, warning_message, timestamp)
        self.errors = []    # List of tuples (url, error_message, timestamp)
    
    def add_url_result(self, url: str, summary: Dict[str, Any], success: bool, error: Optional[ScrapingError] = None) -> None:
        """Add results from a single URL to the session metrics.
        
        Args:
            url: The URL that was processed
            summary: The scraping summary for this URL
            success: Whether the URL was processed successfully
            error: The error that occurred, if any
        """
        self.total_urls += 1
        
        # Add timing
        self.total_time += summary['timestamp']['duration_seconds']
        
        # Add OCR metrics if available
        if 'metrics' in summary.get('extraction', {}):
            metrics = summary['extraction']['metrics']
            self.total_ocr_attempts += metrics['ocr_attempts']
            self.total_ocr_successes += metrics['ocr_successes']
        else:
            # No metrics available (likely an error occurred)
            self.total_ocr_attempts += 0
            self.total_ocr_successes += 0
        
        # Track URL status
        if success:
            self.successful_urls.append(url)
        else:
            self.failed_urls.append((url, error))
    
    def add_warning(self, url: str, warning_message: str) -> None:
        """Add a warning message for a URL.
        
        Args:
            url: The URL where the warning occurred
            warning_message: The warning message
        """
        self.warnings.append((url, warning_message, datetime.now()))
    
    def add_error(self, url: str, error_message: str) -> None:
        """Add an error message for a URL.
        
        Args:
            url: The URL where the error occurred
            error_message: The error message
        """
        self.errors.append((url, error_message, datetime.now()))
    
    def get_session_summary(self) -> Dict[str, Any]:
        """Generate a summary of the entire scraping session.
        
        Returns:
            Dict containing session-wide metrics and statistics
        """
        end_time = datetime.now()
        total_duration = (end_time - self.start_time).total_seconds()
        
        # Calculate average OCR success rate
        avg_ocr_success_rate = 0.0
        if self.total_ocr_attempts > 0:
            avg_ocr_success_rate = (self.total_ocr_successes / self.total_ocr_attempts) * 100
        
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
                'total_attempts': self.total_ocr_attempts,
                'total_successes': self.total_ocr_successes,
                'average_success_rate': round(avg_ocr_success_rate, 2)
            },
            'performance': {
                'total_processing_time': round(self.total_time, 2),
                'average_time_per_url': round(self.total_time / self.total_urls if self.total_urls > 0 else 0, 2)
            },
            'warnings_and_errors': {
                'warnings': [
                    {
                        'url': url,
                        'message': message,
                        'timestamp': timestamp.isoformat()
                    }
                    for url, message, timestamp in self.warnings
                ],
                'errors': [
                    {
                        'url': url,
                        'message': message,
                        'timestamp': timestamp.isoformat()
                    }
                    for url, message, timestamp in self.errors
                ]
            }
        }

def get_output_paths(base_dir: Path, url: str, content_type: str = 'text') -> Dict[str, Path]:
    """Generate output paths with appropriate extensions based on content type.
    
    Args:
        base_dir: Base directory for output files
        url: The URL being scraped (used for filename)
        content_type: Type of content ('text', 'json', 'html', etc.)
        
    Returns:
        Dict mapping file types to their paths. Note: Directories are not created here.
        They should be created only when content is ready to be saved.
    """
    # Create a safe filename from the URL
    hostname = normalize_hostname(url)
    
    # Define extensions based on content type
    extensions = {
        'text': '.txt',
        'json': '.json',
        'html': '.html',
        'raw': '.raw',
        'ocr': '.ocr.txt',
        'ocr_summary': '.ocr.json'
    }
    
    # Get the current run directory
    run_dir = config.get_run_directory()
    
    # Generate paths with appropriate extensions
    paths = {
        'page': run_dir / 'pages' / hostname / f"page{extensions['html']}",
        'text': run_dir / 'pages' / hostname / f"text{extensions['text']}",
        'raw_text': run_dir / 'pages' / hostname / f"raw{extensions['raw']}",
        'images_dir': run_dir / 'images' / hostname,
        'ocr_dir': run_dir / 'pages' / hostname / "ocr",
        'ocr_summary': run_dir / 'pages' / hostname / "ocr" / f"summary{extensions['ocr_summary']}"
    }
    
    # Create directories if they don't exist
    for path in paths.values():
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
    
    return paths

def generate_scraping_summary(url: str, result: Dict[str, Any], start_time: datetime) -> Dict[str, Any]:
    """Generate a structured summary of the scraping results.
    
    Args:
        url: The URL that was scraped
        result: The scraping result dictionary
        start_time: When the scraping started
        
    Returns:
        Dict containing the structured summary
    """
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    
    # Generate output paths with appropriate extensions
    output_paths = get_output_paths(config.DATA_DIR, url)
    
    # Create base directory for files that will always exist
    output_paths['page'].parent.mkdir(parents=True, exist_ok=True)
    
    # Extract image statistics
    image_stats = {
        'total': len(result['images']),
        'by_type': {},
        'total_size_bytes': 0,
        'extensions': set(),  # Will be converted to list before returning
        'ocr_attempts': 0,
        'ocr_successes': 0,
        'ocr_success_rate': 0.0  # Initialize success rate
    }
    
    ocr_success_count = 0
    for img in result['images']:
        # Count image types and collect extensions
        img_type = img.get('image_type', 'unknown')
        image_stats['by_type'][img_type] = image_stats['by_type'].get(img_type, 0) + 1
        if 'extension' in img:
            image_stats['extensions'].add(img['extension'])
        
        # Sum up image sizes
        image_stats['total_size_bytes'] += img.get('size_bytes', 0)
        
        # Track OCR attempts and successes
        if img.get('ocr_attempted', False):
            image_stats['ocr_attempts'] += 1
            if img.get('ocr_text'):
                ocr_success_count += 1
                image_stats['ocr_successes'] += 1
    
    # Calculate OCR success rate
    if image_stats['ocr_attempts'] > 0:
        image_stats['ocr_success_rate'] = (image_stats['ocr_successes'] / image_stats['ocr_attempts']) * 100
    
    if image_stats['total'] > 0:
        # Create images directory only if we have images
        output_paths['images_dir'].mkdir(parents=True, exist_ok=True)
        
        # Create OCR directory only if we have OCR results
        if ocr_success_count > 0:
            output_paths['ocr_dir'].mkdir(parents=True, exist_ok=True)
    
    # Convert extensions set to sorted list for JSON serialization
    image_stats['extensions'] = sorted(list(image_stats['extensions']))
    
    # Generate text statistics
    text_stats = {
        'length': result['text_data']['text_length'],
        'word_count': result['text_data']['word_count'],
        'paragraph_count': result['text_data'].get('paragraph_count', 0),
        'has_content': bool(result['text'].strip()),
        'format': result.get('text_format', 'plain')
    }
    
    # Create the summary
    summary = {
        'timestamp': {
            'start': start_time.isoformat(),
            'end': end_time.isoformat(),
            'duration_seconds': duration
        },
        'url': {
            'original': url,
            'parsed': urlparse(url).geturl()
        },
        'extraction': {
            'success': bool(result),
            'text': text_stats,
            'images': image_stats,
            'metrics': {
                'total_time_seconds': duration,
                'ocr_attempts': image_stats['ocr_attempts'],
                'ocr_successes': image_stats['ocr_successes'],
                'ocr_success_rate': round(image_stats['ocr_success_rate'], 2)  # Round to 2 decimal places
            }
        },
        'output_files': {
            'page': str(output_paths['page']),
            'text': str(output_paths['text']),
            'raw_text': str(output_paths['raw_text']),
            'images_dir': str(output_paths['images_dir']) if image_stats['total'] > 0 else None,
            'ocr_dir': str(output_paths['ocr_dir']) if ocr_success_count > 0 else None,
            'ocr_summary': str(output_paths['ocr_summary']) if ocr_success_count > 0 else None
        }
    }
    
    return summary

def log_scraping_summary(summary: Dict[str, Any]) -> None:
    """Log the scraping summary in both human-readable and JSON formats.
    
    Args:
        summary: The structured summary dictionary
    """
    # Log human-readable summary
    logging.info("\n\n[SUMMARY] Scraping Result Summary:")
    logging.info(f"URL: {summary['url']['original']}")
    logging.info(f"Duration: {summary['timestamp']['duration_seconds']:.2f} seconds")
    
    # Text statistics
    text_stats = summary['extraction']['text']
    logging.info("\n[TEXT] Text Statistics:")
    logging.info(f"• Length: {text_stats['length']} characters")
    logging.info(f"• Words: {text_stats['word_count']} words")
    logging.info(f"• Paragraphs: {text_stats['paragraph_count']} paragraphs")
    logging.info(f"• Format: {text_stats['format']}")
    logging.info(f"• Has content: {'Yes' if text_stats['has_content'] else 'No'}")
    
    # Image statistics
    img_stats = summary['extraction']['images']
    logging.info("\n[IMAGES] Image Statistics:")
    logging.info(f"• Total images: {img_stats['total']}")
    logging.info(f"• Total size: {img_stats['total_size_bytes'] / 1024:.2f} KB")
    logging.info(f"• OCR success rate: {img_stats['ocr_success_rate']:.1f}%")
    if img_stats['by_type']:
        logging.info("• Types:")
        for img_type, count in img_stats['by_type'].items():
            logging.info(f"  - {img_type}: {count}")
    if img_stats['extensions']:
        logging.info("• File extensions:")
        for ext in sorted(img_stats['extensions']):
            logging.info(f"  - {ext}")
    
    # Performance metrics
    metrics = summary['extraction']['metrics']
    logging.info("\n[METRICS] Performance Metrics:")
    logging.info(f"• Total time: {metrics['total_time_seconds']:.2f} seconds")
    logging.info(f"• OCR attempts: {metrics['ocr_attempts']}")
    logging.info(f"• OCR successes: {metrics['ocr_successes']}")
    logging.info(f"• OCR success rate: {metrics['ocr_success_rate']:.1f}%")
    
    # Output files
    logging.info("\n[SAVED] Output Files:")
    for key, path in summary['output_files'].items():
        logging.info(f"• {key}: {path}")
    
    # Log JSON summary for machine parsing
    logging.info("\n[STATS] JSON Summary:")
    logging.info(json.dumps(summary, indent=2))

def log_session_summary(session: ScrapingSession) -> None:
    """Log the session-wide summary in a human-readable format.
    
    Args:
        session: The ScrapingSession object containing session metrics
    """
    summary = session.get_session_summary()
    
    logging.info("\n\n[SESSION SUMMARY] Overall Scraping Session Results:")
    logging.info(f"Session Duration: {summary['session_duration']['total_seconds']:.2f} seconds")
    
    # URLs processed
    logging.info("\n[URLS] Processing Statistics:")
    logging.info(f"• Total URLs processed: {summary['urls_processed']['total']}")
    logging.info(f"• Successful: {summary['urls_processed']['successful']}")
    logging.info(f"• Failed: {summary['urls_processed']['failed']}")
    
    # OCR metrics
    logging.info("\n[OCR] Overall OCR Statistics:")
    logging.info(f"• Total OCR attempts: {summary['ocr_metrics']['total_attempts']}")
    logging.info(f"• Total OCR successes: {summary['ocr_metrics']['total_successes']}")
    logging.info(f"• Average OCR success rate: {summary['ocr_metrics']['average_success_rate']:.2f}%")
    
    # Performance metrics
    logging.info("\n[PERFORMANCE] Processing Performance:")
    logging.info(f"• Total processing time: {summary['performance']['total_processing_time']:.2f} seconds")
    logging.info(f"• Average time per URL: {summary['performance']['average_time_per_url']:.2f} seconds")
    
    # Warnings and Errors Summary
    logging.info("\n[WARNINGS AND ERRORS] Summary:")
    if summary['warnings_and_errors']['warnings']:
        logging.info("\nWARNINGS:")
        for warning in summary['warnings_and_errors']['warnings']:
            logging.info(f"• URL: {warning['url']}")
            logging.info(f"  Time: {warning['timestamp']}")
            logging.info(f"  Message: {warning['message']}")
    else:
        logging.info("\nNo warnings recorded.")
    
    if summary['warnings_and_errors']['errors']:
        logging.info("\nERRORS:")
        for error in summary['warnings_and_errors']['errors']:
            logging.info(f"• URL: {error['url']}")
            logging.info(f"  Time: {error['timestamp']}")
            logging.info(f"  Message: {error['message']}")
    else:
        logging.info("\nNo errors recorded.")
    
    # Log failed URLs if any
    if session.failed_urls:
        logging.info("\n[FAILED] Failed URLs:")
        for url, error in session.failed_urls:
            logging.info(f"• {url}")
            if error is not None:
                logging.info(f"Error Type: {error.error_type}")
                logging.info(f"Error Message: {str(error)}")
                logging.info(f"Timestamp: {error.timestamp.isoformat()}")
                if error.details:
                    logging.info("Additional Details:")
                    for key, value in error.details.items():
                        logging.info(f"  {key}: {value}")
            else:
                logging.info("Error: Unknown error occurred")
    
    # Log JSON summary for machine parsing
    logging.info("\n[STATS] Session JSON Summary:")
    logging.info(json.dumps(summary, indent=2))

def read_urls_from_file(file_path: str) -> Iterator[str]:
    """Read URLs from a file, one per line.
    
    Args:
        file_path: Path to the file containing URLs
        
    Yields:
        str: Each URL from the file
        
    Raises:
        FileNotFoundError: If the file doesn't exist
        InvalidURLError: If the file is empty or contains invalid URLs
    """
    try:
        with open(file_path, 'r') as f:
            urls = [line.strip() for line in f if line.strip()]
            
        if not urls:
            raise InvalidURLError(f"No URLs found in file: {file_path}")
            
        for url in urls:
            yield url
            
    except FileNotFoundError:
        raise FileNotFoundError(f"URL file not found: {file_path}")

def get_log_level(level_str: str) -> int:
    """Convert string log level to logging constant."""
    level_map = {
        'DEBUG': logging.DEBUG,
        'INFO': logging.INFO,
        'WARNING': logging.WARNING,
        'ERROR': logging.ERROR,
        'CRITICAL': logging.CRITICAL
    }
    return level_map.get(level_str.upper(), logging.INFO)

def setup_logging(debug_mode: bool = False) -> None:
    """Configure logging to both file and console with timestamps and log levels.
    
    Args:
        debug_mode: If True, shows all debug information in console. If False, shows minimal console output.
    """
    from .logging_utils import configure_logging
    
    # Configure logging using our enhanced logging utilities
    console_level = 'DEBUG' if debug_mode else 'INFO'
    file_log_level = 'DEBUG' if debug_mode else 'INFO'  # Make file_level conditional
    configure_logging(
        log_file=config.LOG_FILE,
        console_level=console_level,
        file_level=file_log_level,  # Use the conditional level
        include_emojis=True,
        include_context=True
    )
    
    # Log the configured mode
    from .logging_utils import info
    info(f"Logging configured with debug mode: {debug_mode}", category='CONFIG')

async def process_url(
    url: str,
    session: AsyncClient,
    db: Session,
    output_dir: Path,
    rate_limiter: RateLimiter,
    debug_mode: bool = False
) -> Dict[str, Any]:
    """
    Process a single URL with comprehensive error handling and logging.
    
    Args:
        url: The URL to process
        session: AsyncClient session for making requests
        db: Database session
        output_dir: Directory to save output files
        rate_limiter: Rate limiter for controlling request frequency
        debug_mode: Whether to enable debug logging
        
    Returns:
        Dict containing processing results and metadata
    """
    start_time = time.time()
    result = {
        'url': url,
        'status': 'pending',
        'error': None,
        'html_path': None,
        'text_path': None,
        'images': [],
        'processing_time': 0,
        'timestamp': datetime.now().isoformat()
    }
    
    try:
        # Validate URL
        if not url.startswith(('http://', 'https://')):
            raise ValueError(f"Invalid URL scheme: {url}")
            
        # Create URL-specific directory
        url_dir = output_dir / get_safe_filename(url)
        url_dir.mkdir(parents=True, exist_ok=True)
        
        # Scrape the page
        logging.info(f"Starting scrape of {url}", extra={'category': 'SCRAPE'})
        html_content = await scrape_page(url, session, rate_limiter)
        
        # Save HTML
        html_path = url_dir / 'page.html'
        html_path.write_text(html_content, encoding='utf-8')
        result['html_path'] = str(html_path)
        
        # Extract text
        text_content = extract_text_from_html(html_content)
        text_path = url_dir / 'text.txt'
        text_path.write_text(text_content, encoding='utf-8')
        result['text_path'] = str(text_path)
        
        # Extract and process images
        image_urls = extract_image_urls(html_content, url)
        if image_urls:
            logging.info(f"Found {len(image_urls)} images to process", extra={'category': 'SCRAPE'})
            for img_url in image_urls:
                try:
                    img_path = url_dir / get_safe_filename(img_url)
                    await download_image(img_url, img_path, rate_limiter)
                    result['images'].append(str(img_path))
                except Exception as e:
                    if debug_mode:
                        logging.warning(f"Failed to process image {img_url}: {str(e)}", 
                                      extra={'category': 'SCRAPE'})
        
        # Update database
        update_url_status(db, url, 'completed')
        result['status'] = 'completed'
        
    except Exception as e:
        error_msg = str(e)
        result['status'] = 'failed'
        result['error'] = error_msg
        
        if debug_mode:
            logging.error(f"Failed to process {url}: {error_msg}", 
                         extra={'category': 'SCRAPE'})
        else:
            logging.error(f"Failed to process {url}", 
                         extra={'category': 'SCRAPE'})
        
        update_url_status(db, url, 'failed', error_msg)
    
    finally:
        result['processing_time'] = time.time() - start_time
        if debug_mode:
            logging.info(f"Processed {url} in {result['processing_time']:.2f}s", 
                        extra={'category': 'SCRAPE'})
    
    return result

def write_session_log(session: ScrapingSession, run_dir: Path) -> None:
    """Write a detailed session log with comprehensive information about the scraping run.
    
    Args:
        session: The ScrapingSession object containing session metrics
        run_dir: The directory for this scraping run
    """
    # Generate log file path in the run directory
    log_file = run_dir / 'session.log'
    
    summary = session.get_session_summary()
    
    with open(log_file, 'w', encoding='utf-8') as f:
        # Write header with session information
        f.write("=" * 80 + "\n")
        f.write("SCRAPING SESSION SUMMARY\n")
        f.write("=" * 80 + "\n\n")
        
        # Session Information
        f.write("SESSION INFORMATION\n")
        f.write("-" * 80 + "\n")
        f.write(f"Start Time: {summary['session_duration']['start']}\n")
        f.write(f"End Time: {summary['session_duration']['end']}\n")
        f.write(f"Total Duration: {summary['session_duration']['total_seconds']:.2f} seconds\n")
        f.write(f"Command: {' '.join(sys.argv)}\n")
        f.write(f"Python Version: {sys.version}\n")
        f.write(f"Platform: {sys.platform}\n")
        f.write(f"Run Directory: {run_dir}\n\n")
        
        # URL Processing Summary
        f.write("URL PROCESSING SUMMARY\n")
        f.write("-" * 80 + "\n")
        f.write(f"Total URLs Processed: {summary['urls_processed']['total']}\n")
        f.write(f"Successful URLs: {summary['urls_processed']['successful']}\n")
        f.write(f"Failed URLs: {summary['urls_processed']['failed']}\n")
        success_rate = (summary['urls_processed']['successful'] / summary['urls_processed']['total'] * 100) if summary['urls_processed']['total'] > 0 else 0
        f.write(f"Success Rate: {success_rate:.1f}%\n\n")
        
        # Warnings and Errors Summary
        f.write("WARNINGS AND ERRORS\n")
        f.write("-" * 80 + "\n")
        
        # Write warnings
        if summary['warnings_and_errors']['warnings']:
            f.write("\nWARNINGS:\n")
            for warning in summary['warnings_and_errors']['warnings']:
                f.write(f"• URL: {warning['url']}\n")
                f.write(f"  Time: {warning['timestamp']}\n")
                f.write(f"  Message: {warning['message']}\n\n")
        else:
            f.write("\nNo warnings recorded.\n")
        
        # Write errors
        if summary['warnings_and_errors']['errors']:
            f.write("\nERRORS:\n")
            for error in summary['warnings_and_errors']['errors']:
                f.write(f"• URL: {error['url']}\n")
                f.write(f"  Time: {error['timestamp']}\n")
                f.write(f"  Message: {error['message']}\n\n")
        else:
            f.write("\nNo errors recorded.\n")
        
        # Performance Metrics
        f.write("\nPERFORMANCE METRICS\n")
        f.write("-" * 80 + "\n")
        f.write(f"Total Processing Time: {summary['performance']['total_processing_time']:.2f} seconds\n")
        f.write(f"Average Time per URL: {summary['performance']['average_time_per_url']:.2f} seconds\n")
        
        # Calculate processing speed safely
        if summary['performance']['total_processing_time'] > 0:
            processing_speed = summary['urls_processed']['total'] / (summary['performance']['total_processing_time'] / 60)
            f.write(f"Processing Speed: {processing_speed:.1f} URLs/minute\n")
        else:
            f.write("Processing Speed: N/A (no processing time recorded)\n")
        f.write("\n")
        
        # OCR Statistics
        f.write("OCR STATISTICS\n")
        f.write("-" * 80 + "\n")
        f.write(f"Total OCR Attempts: {summary['ocr_metrics']['total_attempts']}\n")
        f.write(f"Total OCR Successes: {summary['ocr_metrics']['total_successes']}\n")
        f.write(f"Average OCR Success Rate: {summary['ocr_metrics']['average_success_rate']:.2f}%\n\n")
        
        # Successful URLs
        f.write("SUCCESSFUL URLS\n")
        f.write("-" * 80 + "\n")
        for url in session.successful_urls:
            f.write(f"• {url}\n")
        f.write("\n")
        
        # Failed URLs with Error Details
        if session.failed_urls:
            f.write("FAILED URLS AND ERRORS\n")
            f.write("-" * 80 + "\n")
            for url, error in session.failed_urls:
                f.write(f"URL: {url}\n")
                if error is not None:
                    f.write(f"Error Type: {error.error_type}\n")
                    f.write(f"Error Message: {str(error)}\n")
                    f.write(f"Timestamp: {error.timestamp.isoformat()}\n")
                    if error.details:
                        f.write("Additional Details:\n")
                        for key, value in error.details.items():
                            f.write(f"  {key}: {value}\n")
                else:
                    f.write("Error: Unknown error occurred\n")
                f.write("\n")
        
        # System Information
        f.write("SYSTEM INFORMATION\n")
        f.write("-" * 80 + "\n")
        f.write(f"CPU Count: {os.cpu_count()}\n")
        f.write(f"Memory Usage: {psutil.Process().memory_info().rss / 1024 / 1024:.1f} MB\n")
        f.write(f"Disk Space Available: {psutil.disk_usage('.').free / 1024 / 1024 / 1024:.1f} GB\n\n")
        
        # JSON Summary for Machine Parsing
        f.write("JSON SUMMARY\n")
        f.write("-" * 80 + "\n")
        f.write(json.dumps(summary, indent=2))
    
    logging.info(f"\n[LOG] Session log written to: {log_file}")

def update_history_log(session: ScrapingSession) -> None:
    """Update the history log with a summary of the current scraping session.
    
    Args:
        session: The ScrapingSession object containing session metrics
    """
    # Use the same directory as the main log file
    log_dir = config.LOG_FILE.parent
    log_dir.mkdir(parents=True, exist_ok=True)
    
    history_file = log_dir / 'scrape_history.log'
    summary = session.get_session_summary()
    
    # Format the session summary for the history log
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    total_urls = summary['urls_processed']['total']
    avg_ocr_rate = summary['ocr_metrics']['average_success_rate']
    time_taken = summary['session_duration']['total_seconds']
    
    # Create the concise history entry
    history_entry = (
        f"{timestamp} | {total_urls} URLs Scraped | "
        f"Avg OCR Success Rate: {avg_ocr_rate:.1f}% | "
        f"Time Taken: {time_taken:.1f}s"
    )
    
    # Add error summary if there were any failures
    if session.failed_urls:
        error_types = {}
        for _, error in session.failed_urls:
            if error is not None:
                error_type = error.error_type if hasattr(error, 'error_type') else 'Unknown'
                error_types[error_type] = error_types.get(error_type, 0) + 1
            else:
                error_types['Unknown'] = error_types.get('Unknown', 0) + 1
        
        if error_types:
            error_summary = " | Errors: " + ", ".join(
                f"{error_type}({count})" for error_type, count in error_types.items()
            )
            history_entry += error_summary
    
    history_entry += "\n"
    
    try:
        # Append the entry to the history log
        with open(history_file, 'a', encoding='utf-8') as f:
            f.write(history_entry)
        
        # If we successfully wrote to the new location, try to migrate old logs
        old_log_dir = Path('scrape_logs')
        old_history_file = old_log_dir / 'scrape_history.log'
        if old_history_file.exists():
            try:
                # Read old logs
                with open(old_history_file, 'r', encoding='utf-8') as old_f:
                    old_entries = old_f.readlines()
                
                # Append old entries to new file
                with open(history_file, 'a', encoding='utf-8') as new_f:
                    new_f.writelines(old_entries)
                
                # Remove old file and directory if empty
                old_history_file.unlink()
                if not any(old_log_dir.iterdir()):
                    old_log_dir.rmdir()
                
                logging.info(f"Successfully migrated old history logs to {history_file}")
            except Exception as e:
                logging.warning(f"Failed to migrate old history logs: {str(e)}")
        
        logging.info(f"\n[LOG] Session summary added to history log: {history_file.absolute()}")
    except Exception as e:
        logging.error(f"Failed to write to history log: {str(e)}")

def get_formatted_output_paths(run_dir: Path, company_name: str, url: str) -> Dict[str, Path]:
    """
    Generates specific output paths for Step 3 of the scraping process.
    <timestamp> is part of run_dir.
    The main directory under 'pages' and 'images' will be based on the normalized hostname from the URL.
    Output structure: run_dir/pages/normalized_hostname_from_url/{page.html, text.txt, etc.}
    """
    # Consistently use the normalized hostname from the URL for the main directory name
    # This overrides any 'company_name' passed in from client_id for directory structure purposes,
    # ensuring consistency and preventing duplicate folders like 'LST-Laserschneidtechnik_GmbH' vs 'lstgmbh_de'.
    sane_hostname_from_url = normalize_hostname(url)
    if not sane_hostname_from_url: # Ultimate fallback
        sane_hostname_from_url = "unknown_hostname"

    # Define the base directory for this URL's content, directly under the normalized hostname
    url_content_base_dir = run_dir / 'pages' / sane_hostname_from_url
    images_base_dir = run_dir / 'images' / sane_hostname_from_url
    ocr_base_dir = url_content_base_dir / "ocr"

    paths = {
        'html_file': url_content_base_dir / "page.html",
        'images_dir': images_base_dir, # Directory for storing image files
        'text_file': url_content_base_dir / "text.txt",
        'json_page_summary_file': url_content_base_dir / "text.json", # For "Plain JSON" as per spec
        'ocr_image_summary_file': ocr_base_dir / "summary.json", # For OCR summary of images
        'url_file': url_content_base_dir / "url.txt" # For storing the exact URL
    }

    # Ensure directories exist
    url_content_base_dir.mkdir(parents=True, exist_ok=True)
    images_base_dir.mkdir(parents=True, exist_ok=True)
    ocr_base_dir.mkdir(parents=True, exist_ok=True)
            
    return paths

def process_single_pending_url(
    log_id: str,
    client_id: Optional[str],
    url_to_scrape: str,
    run_dir: Path,
    scrape_session: ScrapingSession,
    scrape_mode: str = 'both',
    debug_mode: bool = False
) -> None:
    """
    Processes a single URL that is marked 'pending' in scraping_logs.
    This includes scraping, saving data locally, and updating database records.
    """
    start_time = datetime.now()
    logging.info(f"Processing pending URL: {url_to_scrape} (Log ID: {log_id}, Client ID: {client_id})")

    # Use normalized hostname as default instead of "unknown_company"
    company_name: Optional[str] = normalize_hostname(url_to_scrape)
    if client_id:
        fetched_company_name = db_utils.get_company_name(client_id)
        if fetched_company_name:
            company_name = fetched_company_name
        else:
            logging.warning(f"Could not fetch company name for client_id: {client_id}. Using default '{company_name}'.")
    else:
        logging.warning(f"No client_id provided for URL {url_to_scrape}. Using normalized hostname '{company_name}' as company name.")
        # company_name will be 'unknown_company', or we can use normalize_hostname(url_to_scrape)
        # get_formatted_output_paths handles None or empty company_name by using normalize_hostname

    output_paths = get_formatted_output_paths(run_dir, company_name if company_name else normalize_hostname(url_to_scrape), url_to_scrape)
    
    page_specific_summary_dict = {} # To store the summary for this page, for json.txt

    try:
        # 1. Scrape Content (HTML and Images)
        # scrape_page is expected to handle OCR internally if scrape_mode includes it.
        logging.info(f"Starting scrape for {url_to_scrape} with mode '{scrape_mode}'")
        try:
            result = scrape_page(url_to_scrape, scrape_mode=scrape_mode, use_rate_limiter=True)
            # result keys: 'html', 'text', 'images', 'text_data', 'metadata'
        except InvalidURLError as e:
            raise ScrapingError(f"Invalid URL: {str(e)}", error_type="InvalidURL", details={"url": url_to_scrape})
        except ConnectionError as e:
            raise ScrapingError(f"Connection error: {str(e)}", error_type="Connection", details={"url": url_to_scrape})
        except ParsingError as e:
            raise ScrapingError(f"Parsing error: {str(e)}", error_type="Parsing", details={"url": url_to_scrape})
        except OCRError as e:
            raise ScrapingError(f"OCR error: {str(e)}", error_type="OCR", details={"url": url_to_scrape})
        except Exception as e:
            raise ScrapingError(f"Unexpected error in scrape_page: {str(e)}", error_type="ScrapePageFailed")

        # 2. Text Extraction is done by scrape_page -> result['text']

        # 3. Local Data Saving
        # HTML
        with open(output_paths['html_file'], 'w', encoding='utf-8') as f:
            f.write(result['html'])
        logging.info(f"Saved HTML to: {output_paths['html_file']}")

        # Plain Text
        with open(output_paths['text_file'], 'w', encoding='utf-8') as f:
            f.write(result['text'])
        logging.info(f"Saved plain text to: {output_paths['text_file']}")

        # Save the exact URL to url.txt
        if 'url_file' in output_paths:
            with open(output_paths['url_file'], 'w', encoding='utf-8') as f:
                f.write(url_to_scrape)
            logging.info(f"Saved URL to: {output_paths['url_file']}")

        # Images and OCR Summary
        if result['images']:
            logging.info(f"Processing {len(result['images'])} images for {url_to_scrape}.")
            saved_image_count = 0
            for img_data in result['images']:
                if img_data.get('content'):
                    img_filename = img_data.get('filename', f"image_{saved_image_count}.{img_data.get('extension', 'png')}")
                    img_path = output_paths['images_dir'] / img_filename
                    try:
                        with open(img_path, 'wb') as img_f:
                            img_f.write(img_data['content'])
                        saved_image_count += 1
                    except IOError as e:
                        logging.error(f"Failed to save image {img_filename}: {e}")
            logging.info(f"Saved {saved_image_count} images to: {output_paths['images_dir']}")

            # OCR Summary for images (content from ocr.generate_ocr_summary)
            # generate_ocr_summary expects a list of image dicts, result['images'] is that list.
            ocr_summary_content = ocr.generate_ocr_summary(result['images']) # This returns a dict
            with open(output_paths['ocr_image_summary_file'], 'w', encoding='utf-8') as ocr_f:
                json.dump(ocr_summary_content, ocr_f, indent=2)
            logging.info(f"Saved OCR summary to: {output_paths['ocr_image_summary_file']}")
        else:
            logging.info(f"No images found or processed for {url_to_scrape}.")

        # "Plain JSON" to json.txt - using the generate_scraping_summary for this page
        # This summary is different from the OCR summary. It's a general summary of the page scrape.
        page_specific_summary_dict = generate_scraping_summary(url_to_scrape, result, start_time)
        with open(output_paths['json_page_summary_file'], 'w', encoding='utf-8') as json_summary_f:
            json.dump(page_specific_summary_dict, json_summary_f, indent=2)
        logging.info(f"Saved page JSON summary to: {output_paths['json_page_summary_file']}")
        
        # 4. Database Updates
        # Update scraping_logs to 'completed'
        db_utils.update_scraping_log_status(log_id, 'completed')
        
        # Insert into scraped_pages
        page_type = "website" # Default, can be enhanced later
        # The 'summary' for scraped_pages table can be a brief note.
        # The detailed JSON summary is in json_page_summary_file.
        scraped_pages_summary = f"Scraped successfully. Details in {output_paths['json_page_summary_file']}"
        
        db_utils.insert_scraped_page_data(
            client_id=client_id,
            url=url_to_scrape,
            page_type=page_type,
            raw_html_path=str(output_paths['html_file']),
            plain_text_path=str(output_paths['text_file']),
            summary=scraped_pages_summary,
            extraction_notes=f"OCR summary at {output_paths['ocr_image_summary_file'] if result['images'] else 'N/A'}"
        )
        
        scrape_session.add_url_result(url_to_scrape, page_specific_summary_dict, success=True)
        logging.info(f"Successfully processed and logged {url_to_scrape}")

    except Exception as e:
        error_message = f"Failed to process pending URL {url_to_scrape} (Log ID: {log_id}): {type(e).__name__} - {str(e)}"
        logging.error(error_message, exc_info=debug_mode)
        db_utils.update_scraping_log_status(log_id, 'failed', error_message=str(e)[:1023]) # Limit error message length for DB
        
        # Try to generate a minimal summary for failed attempts if possible
        if not page_specific_summary_dict: # If summary wasn't generated before error
             # Create a basic error summary
            page_specific_summary_dict = {
                'timestamp': {
                    'start': start_time.isoformat(),
                    'end': datetime.now().isoformat(),
                    'duration_seconds': (datetime.now() - start_time).total_seconds()
                },
                'url': {'original': url_to_scrape},
                'extraction': {
                    'success': False,
                    'error': error_message,
                    'metrics': {
                        'ocr_attempts': 0,
                        'ocr_successes': 0
                    }
                },
                'output_files': {key: str(value) for key, value in output_paths.items()} # Log attempted paths
            }
        
        # Create a ScrapingError instance for session logging
        scraping_err = ScrapingError(message=str(e), error_type=type(e).__name__, details={'log_id': log_id})
        scrape_session.add_url_result(url_to_scrape, page_specific_summary_dict, success=False, error=scraping_err)
        scrape_session.add_error(url_to_scrape, error_message)

def write_run_summary(session: ScrapingSession, run_dir: Path) -> None:
    """Write a detailed summary of the scraping run to a JSON file.
    
    Args:
        session: The ScrapingSession object containing session metrics
        run_dir: The directory for this scraping run
    """
    summary = session.get_session_summary()
    
    # Add additional run-specific information
    run_summary = {
        'run_info': {
            'timestamp': datetime.now().isoformat(),
            'run_directory': str(run_dir),
            'command_line': ' '.join(sys.argv),
            'python_version': sys.version,
            'platform': sys.platform
        },
        'session_metrics': summary,
        'failed_urls': [
            {
                'url': url,
                'error_type': error.error_type if error else 'Unknown',
                'error_message': str(error) if error else 'Unknown error',
                'timestamp': error.timestamp.isoformat() if error else None,
                'details': error.details if error and error.details else None
            }
            for url, error in session.failed_urls
        ],
        'successful_urls': session.successful_urls,
        'performance': {
            'total_duration': summary['session_duration']['total_seconds'],
            'average_time_per_url': summary['performance']['average_time_per_url'],
            'start_time': summary['session_duration']['start'],
            'end_time': summary['session_duration']['end']
        },
        'content_metrics': {
            'total_urls_processed': summary['urls_processed']['total'],
            'successful_scrapes': summary['urls_processed']['successful'],
            'failed_scrapes': summary['urls_processed']['failed'],
            'success_rate': (summary['urls_processed']['successful'] / summary['urls_processed']['total'] * 100) if summary['urls_processed']['total'] > 0 else 0
        }
    }
    
    # Write the summary to a JSON file
    summary_file = run_dir / 'summary.json'
    try:
        with open(summary_file, 'w', encoding='utf-8') as f:
            json.dump(run_summary, f, indent=2, ensure_ascii=False)
        logging.info(f"\n[SUMMARY] Run summary written to: {summary_file}")
    except Exception as e:
        logging.error(f"Failed to write run summary: {str(e)}")
# This function has been moved to process_pending_urls_loop.py to avoid circular imports

def main() -> None:
    parser = argparse.ArgumentParser(description='Scrape a webpage and extract text and images.')
    
    # URL source group - mutually exclusive
    url_source_group = parser.add_mutually_exclusive_group(required=True)
    url_source_group.add_argument('--url', help='URL to scrape')
    url_source_group.add_argument('--url-file', help='Path to file containing URLs to scrape (one per line)')
    url_source_group.add_argument('--from-db', action='store_true', help='Fetch URLs from the database')
    parser.add_argument('--num-urls', type=int, default=10, help='Number of URLs to fetch from the database (default: 10)')
    parser.add_argument('--db-range', type=str, default=None, help='Specify a range of URLs to fetch from the database, e.g., "0-100" (0-indexed, end-exclusive). Overrides --num-urls for DB fetching if used when --from-db is active.')

    parser.add_argument('--debug', action='store_true', help='Enable debug mode for verbose console output')
    parser.add_argument('--output-dir',
                       help='Custom directory where scraper results will be saved')
    parser.add_argument('--scrape-mode',
                       choices=['text', 'ocr', 'both'],
                       default='both',
                       help='What to scrape: text only, OCR only, or both (default: both)')
    parser.add_argument('--run-name',
                       help='Name for this scraping run (will be included in the run directory name)')
    parser.add_argument('--pending-batch-size', type=int, default=10,
                       help='Maximum number of pending URLs to process from the queue (Step 3)')
    args = parser.parse_args()

    # Initialize logging with debug mode
    setup_logging(debug_mode=args.debug)
    
    # Set custom output directory if specified
    if args.output_dir:
        output_dir = Path(args.output_dir)
        if not output_dir.exists():
            output_dir.mkdir(parents=True, exist_ok=True)
            logging.info(f"Created output directory: {output_dir}")
        config.set_output_directory(output_dir)
    
    # Initialize a new run directory with optional name
    run_dir = config.initialize_run_directory(args.run_name)
    logging.info(f"Created new run directory: {run_dir}")
    
    # Initialize session tracking
    session = ScrapingSession()
    
    # Add a custom logging handler to capture warnings and errors
    class WarningErrorHandler(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.WARNING:
                url = getattr(record, 'url', 'Unknown URL') # Ensure URL is attached to log record if possible
                if record.levelno == logging.WARNING:
                    session.add_warning(url, record.getMessage())
                else: # ERROR or CRITICAL
                    session.add_error(url, record.getMessage())
    
    # Add the handler to the root logger
    warning_error_handler = WarningErrorHandler()
    warning_error_handler.setLevel(logging.WARNING) # Capture WARNING, ERROR, CRITICAL
    logging.getLogger().addHandler(warning_error_handler)
    
    try:
        urls_to_process = []
        source_description = ""

        if args.url_file:
            source_description = f"file: {args.url_file}"
            try:
                urls_to_process = [(None, url) for url in read_urls_from_file(args.url_file)] # Store as (client_id, url) tuples
            except FileNotFoundError:
                logging.error(f"URL file not found: {args.url_file}")
                parser.error(f"URL file not found: {args.url_file}")
            except InvalidURLError as e:
                logging.error(f"Error reading URL file: {e}")
                parser.error(f"Error reading URL file: {e}")
        
        elif args.from_db:
            offset_val = 0
            limit_val = args.num_urls # Default limit

            if args.db_range:
                try:
                    if '-' not in args.db_range:
                        parser.error("Invalid --db-range format. Expected START-END, e.g., 1-100.")
                    
                    start_str, end_str = args.db_range.split('-')
                    start_index = int(start_str)
                    end_index = int(end_str)

                    if start_index < 1:
                        parser.error("Invalid --db-range value. Start index must be >= 1.")
                    if end_index < start_index:
                        parser.error("Invalid --db-range value. End index must be >= start index.")

                    offset_val = start_index - 1  # Convert to 0-based
                    limit_val = end_index - start_index + 1
                    logging.info(f"Attempting to fetch URLs in range {args.db_range} (limit: {limit_val}, offset: {offset_val}) from the database.")
                    source_description = f"database (range: {args.db_range}, limit: {limit_val}, offset: {offset_val})"
                except ValueError:
                    parser.error("Invalid --db-range format. START and END must be integers, e.g., 1-100.")
                except Exception as e: # Catch any other parsing errors
                    parser.error(f"Error parsing --db-range: {e}. Expected START-END, e.g., 1-100.")
            else:
                logging.info(f"Attempting to fetch the first {limit_val} URLs (offset: {offset_val}) from the database.")
                source_description = f"database (limit: {limit_val}, offset: {offset_val})"
            
            # Call fetch_urls_from_db with the determined limit and offset
            db_url_tuples = db_utils.fetch_urls_from_db(limit=limit_val, offset=offset_val)
            
            # Initialize urls_to_process as a list of tuples, which is expected by the downstream loop
            urls_to_process: List[Tuple[Optional[str], str]] = []
            
            if db_url_tuples:
                logging.info(f"Successfully fetched {len(db_url_tuples)} URLs from the database.")
                for client_id, url_str in db_url_tuples:
                    if url_str:
                        # The check_url_scraped and log_pending_scrape logic will be handled
                        # inside the main processing loop for each URL, just before calling
                        # process_single_pending_url. This keeps the fetching logic clean.
                        urls_to_process.append((client_id, url_str))
                    else:
                        logging.warning(f"Skipping database entry with empty URL (Client ID: {client_id}).")
            else:
                logging.info("No URLs fetched from the database based on the criteria.")

        
        else: # Single URL from args.url
            single_url = args.url
            source_description = f"single URL: {single_url}"
            urls_to_process = [(None, single_url)] # Store as (client_id, url) tuple

        if not urls_to_process:
            logging.info(f"No URLs to process from {source_description}.")
        else:
            logging.info(f"Processing {len(urls_to_process)} URLs from {source_description}")
            
            if not args.debug and len(urls_to_process) > 1:
                print(f"\nStarting batch processing of {len(urls_to_process)} URLs from {source_description}...")
                pbar = tqdm(total=len(urls_to_process), desc="Processing URLs", unit="url")

            for actual_client_id, actual_url in urls_to_process: # Correctly unpack client_id and url
                # Attach url to the handler for better error/warning context
                # This assumes the last handler is WarningErrorHandler, which might be fragile.
                # A more robust way would be to pass url explicitly or use a logging.Filter with context.
                if logging.getLogger().handlers: # Check if handlers exist
                    custom_handler = logging.getLogger().handlers[-1]
                    if isinstance(custom_handler, WarningErrorHandler):
                        # Set context for the WarningErrorHandler
                        # Note: This is a simplified way to pass context.
                        # A more robust method would involve logging.Filter or passing 'extra' to log calls.
                        setattr(custom_handler, 'current_url_context', actual_url)


                try:
                    start_time = datetime.now()

                    # 1. Check if URL (+ client_id if available) is already 'completed' in scraping_logs
                    if db_utils.check_url_scraped(actual_url, actual_client_id):
                        logging.info(f"Skipping already completed URL: {actual_url} (Client: {actual_client_id or 'N/A'})")
                        if not args.debug and len(urls_to_process) > 1 and 'pbar' in locals():
                            pbar.update(1)
                        continue

                    # 2. Log 'pending' status to scraping_logs
                    log_id = db_utils.log_pending_scrape(actual_url, actual_client_id, source='homepage')
                    if not log_id:
                        logging.error(f"Failed to log 'pending' status for URL: {actual_url} (Client: {actual_client_id or 'N/A'}). Skipping.")
                        if not args.debug and len(urls_to_process) > 1 and 'pbar' in locals():
                            pbar.update(1)
                        session.add_url_result(actual_url, { # Use actual_url for session tracking
                            'timestamp': {'duration_seconds': (datetime.now() - start_time).total_seconds()},
                            'extraction': {'metrics': {'ocr_attempts': 0, 'ocr_successes': 0}}
                        }, False, ScrapingError(f"Failed to log pending status for {actual_url}."))
                        continue
                    
                    logging.info(f"Logged 'pending' status for URL: {actual_url}, client_id: {actual_client_id or 'N/A'}, log_id: {log_id}. Proceeding to scrape.")

                    # Process the URL immediately using the existing process_single_pending_url function
                    # This handles scraping, saving data locally, and updating database records
                    process_single_pending_url(
                        log_id=log_id,
                        client_id=actual_client_id,
                        url_to_scrape=actual_url,
                        run_dir=run_dir,
                        scrape_session=session,
                        scrape_mode=args.scrape_mode,
                        debug_mode=args.debug
                    )
                                           # This might need a new session method like `session.add_pending_url(url)`

                    if not args.debug and len(urls_to_process) > 1 and 'pbar' in locals():
                        pbar.update(1)
                        # We don't have a success/failure of actual scraping yet.
                        # The progress bar postfix might need adjustment or be updated later.
                        # For now, let's show total attempted.
                        pbar.set_postfix_str(f"Attempted: {session.total_urls}")

                except KeyboardInterrupt:
                    if not args.debug and len(urls_to_process) > 1 and 'pbar' in locals(): pbar.close()
                    logging.info("\n[INTERRUPT] Scraping interrupted by user. Saving current progress...")
                    raise # Re-raise to be caught by the outer try-except KeyboardInterrupt
                except ScrapingError as e: # This would catch errors from validate_url or our DB calls if they raised ScrapingError
                    logging.error(f"Pre-scraping error for URL: {actual_url} (Client: {actual_client_id or 'N/A'})")
                    logging.error(f"Error Type: {e.error_type}")
                    logging.error(f"Error Message: {str(e)}")
                    if e.details:
                        logging.error("Additional Details:")
                        for key, value in e.details.items():
                            logging.error(f"  {key}: {value}")
                    
                    session.add_url_result(actual_url, { # Use actual_url for session tracking
                        'timestamp': {'duration_seconds': (datetime.now() - start_time).total_seconds()},
                        'extraction': {'metrics': {'ocr_attempts': 0, 'ocr_successes': 0}}
                    }, False, e)
                    
                    if not args.debug and len(urls_to_process) > 1 and 'pbar' in locals():
                        pbar.update(1)
                        pbar.set_postfix({'success': len(session.successful_urls), 'failed': len(session.failed_urls)})
                        
                except KeyboardInterrupt:
                    if not args.debug and len(urls_to_process) > 1: pbar.close()
                    logging.info("\n[INTERRUPT] Scraping interrupted by user. Saving current progress...")
                    raise # Re-raise to be caught by the outer try-except KeyboardInterrupt
                except ScrapingError as e:
                    # This block will catch errors from scrape_page or pre-checks
                    logging.error(f"Failed to process URL: {actual_url} (Client: {actual_client_id or 'N/A'})")
                    logging.error(f"Error Type: {e.error_type}")
                    logging.error(f"Error Message: {str(e)}")
                    if e.details:
                        logging.error("Additional Details:")
                        for key, value in e.details.items():
                            logging.error(f"  {key}: {value}")
                    
                    session.add_url_result(actual_url, {
                        'timestamp': {'duration_seconds': (datetime.now() - start_time).total_seconds()}, # Use actual duration
                        'extraction': {'metrics': {'ocr_attempts': 0, 'ocr_successes': 0}}
                    }, False, e)
                    # Update scraping_logs to 'failed' with error message
                    db_utils.update_scraping_log_status(log_id, 'failed', error_message=str(e)[:1023])  # Limit error message length for DB
                    
                    if not args.debug and len(urls_to_process) > 1:
                        pbar.update(1)
                        pbar.set_postfix({'success': len(session.successful_urls), 'failed': len(session.failed_urls)})
                    continue # Continue to the next URL
            
            if not args.debug and len(urls_to_process) > 1:
                pbar.close()
                print(f"\nProcessing complete! Success: {len(session.successful_urls)}, Failed: {len(session.failed_urls)}")

        # Process any additional URLs that are already in 'pending' state
        # This is now redundant since we're processing URLs immediately, but keeping it
        # for backward compatibility or in case there are pending URLs from previous runs
        if args.pending_batch_size > 0:
            logging.info(f"Checking for additional 'pending' URLs from previous runs (batch size: {args.pending_batch_size}).")
            process_pending_urls_loop(
                scrape_session=session,
                run_dir=run_dir,
                scrape_mode=args.scrape_mode,
                debug_mode=args.debug,
                num_to_process=args.pending_batch_size
            )
        else:
            logging.info("Skipping Step 3 (processing pending queue) as pending-batch-size is 0.")

        # Log session summary to console
        if not args.debug:
            print("\nGenerating summary...")
        log_session_summary(session)
        
        # Write detailed session log in the run directory
        if not args.debug:
            print("Saving session log...")
        write_session_log(session, run_dir)
        
        # Update history log
        if not args.debug:
            print("Updating history...")
        update_history_log(session)
        
        # Write run summary
        if not args.debug:
            print("Saving run summary...")
        write_run_summary(session, run_dir)
        
        if not args.debug:
            print(f"\n✅ All done! Results saved to: {run_dir}")
            
    except KeyboardInterrupt:
        logging.info("\n[INTERRUPT] Scraping interrupted by user. Finalizing logs...")
        # Session summary and logs will be written in the finally block
    except Exception as e:
        logging.critical(f"An unhandled exception occurred in main: {e}", exc_info=True)
        # Session summary and logs will be written in the finally block
    finally:
        # Ensure logs are written even if an error occurs or process is interrupted
        if 'session' in locals() and 'run_dir' in locals():
            logging.info("Finalizing session logs...")
            log_session_summary(session)
            write_session_log(session, run_dir)
            update_history_log(session)
            write_run_summary(session, run_dir)
            logging.info(f"Session logs finalized in: {run_dir}")
        else:
            logging.error("Could not finalize session logs as session or run_dir was not initialized.")

        # Ensure all Playwright browsers are closed
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                # Try to close any active browser contexts
                try:
                    # Get the default browser if it exists
                    browser = getattr(p, '_default_browser', None)
                    if browser and browser.is_connected():
                        browser.close()
                except Exception as e:
                    logging.debug(f"No active browser to close: {str(e)}")
        except Exception as e:
            logging.warning(f"Error during browser cleanup: {str(e)}")
        finally:
            # Force cleanup of any remaining Playwright processes
            try:
                import psutil
                for proc in psutil.process_iter(['pid', 'name']):
                    try:
                        if 'playwright' in proc.info['name'].lower():
                            proc.kill()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
            except Exception as e:
                logging.warning(f"Error during process cleanup: {str(e)}")
            
            # Inspect handlers and shutdown logging
            print(f"[DEBUG PRINT] Final handlers before shutdown: {logging.getLogger().handlers}", flush=True)
            logging.shutdown()
            print("[DEBUG PRINT] logging.shutdown() called.", flush=True)

if __name__ == "__main__":
    main()