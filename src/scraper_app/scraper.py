import os
import traceback
import sys
import json
import re
import logging
from datetime import datetime
from urllib.parse import urljoin, urlparse
from playwright.sync_api import sync_playwright, TimeoutError, Error as PlaywrightError
import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Any
import time
import requests
from . import config

from .utils import (
    download_image,
    get_safe_filename,
    create_metadata,
    process_image_for_ocr,
    validate_url,
    create_scraper_directories,
    normalize_hostname,
    construct_absolute_url # Added
)
from .ocr import ocr_image
from . import config
from .exceptions import (
    ScrapingError, InvalidURLError, ConnectionError, ParsingError, OCRError,
    ServerError, ServiceUnavailableError, RateLimitError
)
from .rate_limiter import get_rate_limiter
from .retry import retry_with_backoff

def clean_text(text: str) -> str:
    """Clean and normalize text content."""
    # Remove extra whitespace
    text = re.sub(r'\s+', ' ', text)
    # Remove empty lines
    text = re.sub(r'\n\s*\n', '\n', text)
    # Strip leading/trailing whitespace
    text = text.strip()
    return text

def get_hostname(url: str) -> str:
    """Extract hostname from URL and convert to a safe filename."""
    return normalize_hostname(url)

def save_ocr_results(ocr_dir: Path, ocr_results: List[Dict[str, Any]], url: str, hostname: str) -> Path:
    """Save OCR results with metadata."""
    logging.info(f"Saving OCR results for {url} ({len(ocr_results)} images)")
    
    # Create OCR directory if it doesn't exist
    ocr_dir.mkdir(parents=True, exist_ok=True)
    
    # Handle empty ocr_results case
    if not ocr_results:
        logging.warning(f"No OCR results to save for {url}")
        summary = create_metadata(url, hostname)
        summary.update({
            'total_ocr_text': '',
            'total_ocr_text_length': 0,
            'total_ocr_word_count': 0,
            'image_count': 0,
            'successful_ocr_count': 0,
            'success_rate': 0,
            'image_summaries': []
        })
        
        summary_path = ocr_dir / 'summary.json'
        try:
            with open(summary_path, 'w', encoding='utf-8') as f:
                json.dump(summary, f, indent=2, ensure_ascii=False)
            logging.info(f"Saved empty OCR summary to {summary_path}")
            return summary_path
        except Exception as e:
            logging.error(f"Failed to save empty OCR summary to {summary_path}: {e}")
            raise OCRError(f"Failed to save empty OCR summary: {e}")
    
    # Save individual OCR results
    for idx, result in enumerate(ocr_results):
        # Create a unique filename for each OCR result
        image_url = result.get('image_url', f"image_{idx}")
        ocr_filename = f"ocr_{idx+1:03d}_{get_safe_filename(image_url)}.json"
        ocr_path = ocr_dir / ocr_filename
        
        # Add metadata to the OCR result
        ocr_data = create_metadata(url, hostname) # This provides base metadata
        # Update with specific OCR details from the 'result' dictionary
        # 'result' items are from the ocr_results list, structured in scrape_page
        ocr_data.update({
            'image_url': result.get('image_url', ''),
            'image_path': str(result.get('image_path', '')),
            'ocr_text': result.get('text', ''),  # Directly access 'text' which holds the string
            'ocr_text_length': result.get('char_count', 0), # Directly access 'char_count'
            'ocr_text_word_count': result.get('word_count', 0), # Directly access 'word_count'
            'ocr_failed': result.get('ocr_failed', False) # Include the ocr_failed flag
        })
        
        try:
            with open(ocr_path, 'w', encoding='utf-8') as f:
                json.dump(ocr_data, f, indent=2, ensure_ascii=False)
            logging.info(f"Saved OCR result {idx+1}/{len(ocr_results)} to {ocr_path}")
        except Exception as e:
            logging.error(f"Failed to save OCR result {idx+1} to {ocr_path}: {e}")
    
    # Save summary of all OCR results
    summary = create_metadata(url, hostname, ocr_results=ocr_results)
    
    summary_path = ocr_dir / 'summary.json'
    try:
        with open(summary_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        logging.info(f"Saved OCR summary to {summary_path}")
        logging.info(f"Total OCR text length: {summary.get('total_ocr_text_length', 0)} characters")
        logging.info(f"Total OCR word count: {summary.get('total_ocr_word_count', 0)} words")
    except Exception as e:
        logging.error(f"Failed to save OCR summary to {summary_path}: {e}")
        raise OCRError(f"Failed to save OCR summary: {e}")
    
    return summary_path

@retry_with_backoff(
    max_retries=config.SCRAPE_MAX_RETRIES,
    initial_delay=config.SCRAPE_INITIAL_DELAY,
    max_delay=config.SCRAPE_MAX_DELAY,
    backoff_factor=config.SCRAPE_BACKOFF_FACTOR,
    jitter=config.SCRAPE_RETRY_JITTER,
    retry_on_exceptions=[ConnectionError, ServerError, ServiceUnavailableError, RateLimitError]
)
def scrape_page(url: str, scrape_mode: str = 'both', use_rate_limiter: bool = True) -> Dict[str, Any]:
    """Scrape a webpage and extract text, images, and perform OCR.
    
    Args:
        url: The URL to scrape
        scrape_mode: What to scrape - 'text', 'ocr', or 'both'
        use_rate_limiter: Whether to use rate limiting for this request
        
    Returns:
        Dict containing scraping results and metadata
        
    Raises:
        InvalidURLError: If URL is invalid
        ConnectionError: If connection fails
        ParsingError: If there are issues parsing the page content
        OCRError: If there are issues with OCR processing
        ServerError: If the server returns a 5xx error
        ServiceUnavailableError: If the server returns a 503 error
        RateLimitError: If the server indicates rate limiting (429)
        ScrapingError: For other unexpected errors
    """
    try:
        # Apply rate limiting if enabled
        if use_rate_limiter:
            # Get hostname for domain-specific rate limiting
            hostname = urlparse(url).netloc
            rate_limiter = get_rate_limiter(hostname if hostname else "default")
            logging.info(f"[RATE LIMIT] Waiting for rate limiter slot for {url}")
            rate_limiter.wait()
            
        logging.info(f"[OK] Scraping {url} in mode: {scrape_mode}")
        
        # Validate URL format
        is_valid, error_message = validate_url(url)
        if not is_valid:
            logging.error(f"[ERROR] Invalid URL: {error_message}")
            raise ValueError(error_message)
        
        # Get hostname and run directory
        hostname = normalize_hostname(url)
        run_dir = config.get_run_directory()
        
        # Get output paths
        paths = {
            'base_dir': run_dir,
            'images_dir': run_dir / 'images' / hostname,
            'pages_dir': run_dir / 'pages' / hostname,
            'ocr_dir': run_dir / 'pages' / hostname / 'ocr'
        }
        
        # Create directories if they don't exist
        for dir_path in paths.values():
            dir_path.mkdir(parents=True, exist_ok=True)
            logging.debug(f"Created directory: {dir_path}")
        
        # Initialize metrics
        metrics = {
            'browser_init': 0.0,
            'page_load': 0.0,
            'content_extraction': 0.0,
            'image_processing': {
                'count': 0,
                'successful': 0,
                'failed': 0,
                'total': 0.0
            },
            'file_saving': 0.0,
            'total_time': 0.0
        }
        
        start_time = time.time()
        
        # Initialize browser and load page
        browser_init_start = time.time()
        with sync_playwright() as p:
            try:
                with p.chromium.launch(headless=True) as browser:
                    with browser.new_context() as context:
                        with context.new_page() as page:
                            # Set a shorter default timeout for all operations
                            page.set_default_timeout(15000)  # 15 seconds
                            
                            # Load page
                            page_load_start = time.time()
                            try:
                                response = page.goto(url, wait_until='domcontentloaded')  # Don't wait for network idle
                                if not response:
                                    raise RuntimeError(f"Failed to load {url}: No response received")
                                if not response.ok:
                                    status_code = response.status
                                    status_text = response.status_text
                                    error_msg = f"Failed to load {url}: HTTP {status_code} - {status_text}"
                                    
                                    # Handle specific HTTP status codes
                                    if status_code == 503:
                                        raise ServiceUnavailableError(f"Service Unavailable: {error_msg}",
                                                                     {'url': url, 'status_text': status_text})
                                    elif status_code == 429:
                                        raise RateLimitError(f"Rate Limited: {error_msg}",
                                                           {'url': url, 'status_text': status_text})
                                    elif 500 <= status_code < 600:
                                        raise ServerError(f"Server Error: {error_msg}", status_code,
                                                        {'url': url, 'status_text': status_text})
                                    else:
                                        raise RuntimeError(error_msg)
                                
                                # Wait for the page to be interactive
                                page.wait_for_load_state('domcontentloaded')
                                
                                # Try to wait for network idle with a shorter timeout
                                try:
                                    page.wait_for_load_state('networkidle', timeout=5000)  # 5 second timeout
                                except TimeoutError:
                                    logging.warning(f"Timeout waiting for network idle on {url}, continuing anyway")
                                except Exception as e:
                                    logging.warning(f"Error waiting for network idle: {str(e)}, continuing anyway")
                                
                            except TimeoutError:
                                raise RuntimeError(f"Timeout while loading {url} after 15 seconds")
                            except PlaywrightError as e:
                                raise RuntimeError(f"Playwright error while loading {url}: {str(e)}")

                            metrics['page_load'] = time.time() - page_load_start

                            # Extract content based on scrape mode
                            content_extraction_start = time.time()
                            html_content = None
                            visible_text = None
                            cleaned_text = None
                            ocr_results = []
                            failed_images = []

                            if scrape_mode in ['text', 'both']:
                                try:
                                    html_content = page.content()
                                    visible_text = page.inner_text('body')
                                    cleaned_text = clean_text(visible_text)
                                except Exception as e:
                                    raise RuntimeError(f"Failed to extract page content: {str(e)}")

                            if scrape_mode in ['ocr', 'both']:
                                try:
                                    # Process images
                                    image_processing_start = time.time()
                                    images = page.query_selector_all('img')
                                    metrics['image_processing']['count'] = len(images)
                                    
                                    for img in images:
                                        try:
                                            src = img.get_attribute('src')
                                            if not src:
                                                continue
                                            
                                            # Construct absolute URL for the image if it's relative
                                            # Use 'url' (the main page URL) as the base for constructing absolute image URLs
                                            absolute_img_url = construct_absolute_url(src, url)
                                            if not absolute_img_url:
                                                logging.warning(f"Could not construct absolute URL for image: {src} on page {url}")
                                                continue

                                            # Create a safe filename for the image
                                            img_filename = get_safe_filename(absolute_img_url)
                                            img_file_path = paths['images_dir'] / img_filename
                                                
                                            # Download and process image
                                            # download_image now returns Optional[Path]
                                            saved_img_path = download_image(absolute_img_url, img_file_path)
                                            
                                            if not saved_img_path:
                                                logging.warning(f"Failed to download image: {absolute_img_url}")
                                                failed_images.append(absolute_img_url) # Log the URL that failed
                                                metrics['image_processing']['failed'] += 1
                                                continue
                                                
                                            # Perform OCR
                                            ocr_output_dict = ocr_image(saved_img_path) # ocr_output_dict is the OCRResult TypedDict

                                            if ocr_output_dict and ocr_output_dict.get('text'): # Check if OCR was successful and text exists
                                                ocr_results.append({
                                                    'image_path': str(saved_img_path),
                                                    'text': ocr_output_dict['text'], # Store the actual text string
                                                    'char_count': ocr_output_dict['char_count'],
                                                    'word_count': ocr_output_dict['word_count'],
                                                    'image_url': absolute_img_url
                                                })
                                                metrics['image_processing']['successful'] += 1
                                            else:
                                                # Handle OCR failure or empty text
                                                logging.warning(f"OCR failed or returned empty text for image: {absolute_img_url} (saved at {saved_img_path})")
                                                # Still append a record for summarization, marking OCR as failed
                                                ocr_results.append({
                                                    'image_path': str(saved_img_path),
                                                    'text': "", # Empty text
                                                    'char_count': 0,
                                                    'word_count': 0,
                                                    'image_url': absolute_img_url,
                                                    'ocr_failed': True
                                                })
                                                # Decide if this counts as a 'failed' image processing step in metrics
                                                # For now, we count download success, OCR success is separate.
                                                # If OCR failure should also mark image_processing as failed:
                                                # metrics['image_processing']['failed'] += 1
                                                # failed_images.append(str(saved_img_path))

                                        except Exception as e:
                                            logging.error(f"Failed to process image {absolute_img_url if 'absolute_img_url' in locals() else src}: {str(e)}")
                                            failed_img_identifier = absolute_img_url if 'absolute_img_url' in locals() else src
                                            failed_images.append(failed_img_identifier)
                                            metrics['image_processing']['failed'] += 1
                                            
                                    metrics['image_processing']['total'] = time.time() - image_processing_start
                                except Exception as e:
                                    raise RuntimeError(f"Failed to process images: {str(e)}")

                            metrics['content_extraction'] = time.time() - content_extraction_start

                            # Save files
                            file_saving_start = time.time()
                            try:
                                # Save page content
                                page_path = paths['pages_dir'] / "page.html"
                                with open(page_path, 'w', encoding='utf-8') as f:
                                    f.write(html_content)

                                # Save visible text with metadata
                                text_data = create_metadata(url, hostname, text=cleaned_text)
                                text_data['ocr_results_count'] = len(ocr_results)
                                text_data['images_dir'] = str(paths['images_dir'])
                                text_data['failed_images'] = failed_images

                                # Save as JSON
                                text_path = paths['pages_dir'] / "text.json"
                                with open(text_path, 'w', encoding='utf-8') as f:
                                    json.dump(text_data, f, indent=2, ensure_ascii=False)

                                # Save raw text
                                raw_text_path = paths['pages_dir'] / "text.txt"
                                with open(raw_text_path, 'w', encoding='utf-8') as f:
                                    f.write(cleaned_text)

                                # Save OCR results
                                ocr_summary_path = save_ocr_results(paths['ocr_dir'], ocr_results, url, hostname)
                            except Exception as e:
                                raise RuntimeError(f"Failed to save files: {str(e)}")
                            metrics['file_saving'] = time.time() - file_saving_start

                            # Calculate total time
                            metrics['total_time'] = time.time() - start_time

                            # Log performance metrics
                            logging.info("\n[STATS] Performance Metrics:")
                            logging.info(f"Total scraping time: {metrics['total_time']:.2f}s")
                            logging.info(f"Browser initialization: {metrics['browser_init']:.2f}s")
                            logging.info(f"Page loading: {metrics['page_load']:.2f}s")
                            logging.info(f"Content extraction: {metrics['content_extraction']:.2f}s")
                            logging.info(f"Image processing: {metrics['image_processing']['total']:.2f}s")
                            logging.info(f"  • Total images: {metrics['image_processing']['count']}")
                            logging.info(f"  • Successful: {metrics['image_processing']['successful']}")
                            logging.info(f"  • Failed: {metrics['image_processing']['failed']}")
                            logging.info(f"File saving: {metrics['file_saving']:.2f}s")

                            return {
                                'html': html_content,
                                'text': cleaned_text,
                                'text_data': text_data,
                                'images': ocr_results,
                                'failed_images': failed_images,
                                'page_path': page_path,
                                'text_path': text_path,
                                'raw_text_path': raw_text_path,
                                'ocr_dir': paths['ocr_dir'],
                                'ocr_summary_path': ocr_summary_path,
                                'images_dir': paths['images_dir'],
                                'metrics': metrics
                            }

            except Exception as e:
                raise RuntimeError(f"Failed to initialize browser: {str(e)}")
        metrics['browser_init'] = time.time() - browser_init_start

    except ValueError as e:
        logging.error(f"[ERROR] Invalid URL: {str(e)}")
        raise InvalidURLError(str(e))
    except ServiceUnavailableError as e:
        logging.error(f"[ERROR] Service Unavailable (HTTP 503): {str(e)}")
        logging.error(f"This is typically a temporary issue. The server is currently unavailable.")
        raise
    except RateLimitError as e:
        logging.error(f"[ERROR] Rate Limited (HTTP 429): {str(e)}")
        logging.error(f"The server is enforcing rate limits. Consider reducing request frequency.")
        raise
    except ServerError as e:
        logging.error(f"[ERROR] Server Error (HTTP {e.status_code}): {str(e)}")
        logging.error(f"This is a server-side error that may be temporary.")
        raise
    except RuntimeError as e:
        logging.error(f"[ERROR] Scraping error: {str(e)}")
        if "Timeout" in str(e):
            raise ConnectionError(f"Timeout while scraping: {str(e)}")
        else:
            raise ParsingError(f"Error while scraping: {str(e)}")
    except PlaywrightError as e:
        logging.error(f"[ERROR] Playwright error: {str(e)}")
        if "net::ERR_CONNECTION_REFUSED" in str(e):
            raise ConnectionError(f"Connection refused: {str(e)}", details={"error_type": "connection_refused"})
        elif "net::ERR_NAME_NOT_RESOLVED" in str(e):
            raise ConnectionError(f"DNS resolution failed: {str(e)}", details={"error_type": "dns_failure"})
        elif "net::ERR_CONNECTION_TIMED_OUT" in str(e):
            raise ConnectionError(f"Connection timed out: {str(e)}", details={"error_type": "timeout"})
        else:
            raise ConnectionError(f"Browser error: {str(e)}")
    except requests.exceptions.RequestException as e:
        logging.error(f"[ERROR] Network request error: {str(e)}")
        if isinstance(e, requests.exceptions.Timeout):
            raise ConnectionError(f"Request timed out: {str(e)}", details={"error_type": "timeout"})
        elif isinstance(e, requests.exceptions.ConnectionError):
            raise ConnectionError(f"Connection error: {str(e)}", details={"error_type": "connection_error"})
        else:
            raise ConnectionError(f"Request error: {str(e)}")
    except Exception as e:
        logging.error(f"[ERROR] Unexpected error: {str(e)}")
        logging.error(traceback.format_exc())
        raise ScrapingError(f"Unexpected error: {str(e)}")
