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
    # process_image_for_ocr, # This seems unused directly in scraper.py, ocr_image is used
    validate_url,
    # create_scraper_directories, # Directories are created within scrape_page now
    normalize_hostname,
    construct_absolute_url
)
from .ocr import ocr_image # Directly import ocr_image
# from . import config # Redundant import
from .exceptions import (
    ScrapingError, InvalidURLError, ConnectionError, ParsingError, OCRError,
    ServerError, ServiceUnavailableError, RateLimitError
)
from .rate_limiter import get_rate_limiter
from .retry import retry_with_backoff

def clean_text(text: str) -> str:
    """Clean and normalize text content."""
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'\n\s*\n', '\n', text)
    return text.strip()

def get_hostname(url: str) -> str: # This function seems unused in this file now
    """Extract hostname from URL and convert to a safe filename."""
    return normalize_hostname(url)

def save_ocr_results(ocr_dir: Path, ocr_results: List[Dict[str, Any]], url: str, hostname: str) -> Path:
    """Save OCR results with metadata."""
    logging.info(f"Saving OCR results for {url} ({len(ocr_results)} images)")
    
    ocr_dir.mkdir(parents=True, exist_ok=True)
    
    if not ocr_results:
        logging.warning(f"No OCR results to save for {url}")
        # create_metadata is from utils.py
        summary_data = create_metadata(url, hostname) 
        summary_data.update({
            'total_ocr_text': '', 'total_ocr_text_length': 0, 'total_ocr_word_count': 0,
            'image_count': 0, 'successful_ocr_count': 0, 'success_rate': 0,
            'image_summaries': []
        })
        summary_path = ocr_dir / 'summary.json'
        try:
            # print(f"DEBUG save_ocr_results: Dumping empty summary: {summary_data}")
            with open(summary_path, 'w', encoding='utf-8') as f:
                json.dump(summary_data, f, indent=2, ensure_ascii=False)
            logging.info(f"Saved empty OCR summary to {summary_path}")
            return summary_path
        except Exception as e:
            logging.error(f"Failed to save empty OCR summary to {summary_path}: {e}")
            raise OCRError(f"Failed to save empty OCR summary: {e}") from e
    
    for idx, result_item in enumerate(ocr_results): # Renamed result to result_item
        # print(f"DEBUG save_ocr_results: Processing result item {idx}: {result_item}")
        image_url_from_result = result_item.get('image_url', f"unknown_image_{idx}")
        ocr_filename = f"ocr_{idx+1:03d}_{get_safe_filename(image_url_from_result)}.json"
        ocr_path = ocr_dir / ocr_filename
        
        individual_ocr_data = create_metadata(url, hostname) 
        individual_ocr_data.update({
            'image_url': result_item.get('image_url', ''),
            'image_path': str(result_item.get('image_path', '')),
            'ocr_text': result_item.get('text', ''),
            'ocr_text_length': result_item.get('char_count', 0),
            'ocr_text_word_count': result_item.get('word_count', 0),
            'ocr_failed': result_item.get('ocr_failed', False)
        })
        
        try:
            # print(f"DEBUG save_ocr_results: Dumping individual ocr_data {idx}: {individual_ocr_data}")
            with open(ocr_path, 'w', encoding='utf-8') as f:
                json.dump(individual_ocr_data, f, indent=2, ensure_ascii=False)
            logging.info(f"Saved OCR result {idx+1}/{len(ocr_results)} to {ocr_path}")
        except Exception as e:
            logging.error(f"Failed to save OCR result {idx+1} for {image_url_from_result} to {ocr_path}: {e}")
            # Continue to save other results, but the overall operation might be considered failed by caller
    
    # Create and save the summary of all OCR results
    # ocr_results here is the full list of dicts from scrape_page
    overall_summary_data = create_metadata(url, hostname, ocr_results=ocr_results)
    summary_path = ocr_dir / 'summary.json'
    try:
        # print(f"DEBUG save_ocr_results: Dumping final summary: {overall_summary_data}")
        with open(summary_path, 'w', encoding='utf-8') as f:
            json.dump(overall_summary_data, f, indent=2, ensure_ascii=False)
        logging.info(f"Saved OCR summary to {summary_path}")
        logging.info(f"Total OCR text length: {overall_summary_data.get('total_ocr_text_length', 0)} characters")
        logging.info(f"Total OCR word count: {overall_summary_data.get('total_ocr_word_count', 0)} words")
    except Exception as e:
        logging.error(f"Failed to save OCR summary to {summary_path}: {e}")
        raise OCRError(f"Failed to save OCR summary: {e}") from e
    
    return summary_path

@retry_with_backoff(
    max_retries=config.SCRAPE_MAX_RETRIES,
    initial_delay=config.SCRAPE_INITIAL_DELAY,
    max_delay=config.SCRAPE_MAX_DELAY,
    backoff_factor=config.SCRAPE_BACKOFF_FACTOR,
    jitter=config.SCRAPE_RETRY_JITTER,
    retry_on_exceptions=[ConnectionError, ServerError, ServiceUnavailableError, RateLimitError] # ServerError is fine here as it's specific
)
def scrape_page(url: str, scrape_mode: str = 'both', use_rate_limiter: bool = True) -> Dict[str, Any]:
    """Scrape a webpage and extract text, images, and perform OCR."""
    try:
        if use_rate_limiter:
            hostname_from_url = urlparse(url).netloc # Renamed to avoid conflict
            rate_limiter = get_rate_limiter(hostname_from_url if hostname_from_url else "default")
            logging.info(f"[RATE LIMIT] Waiting for rate limiter slot for {url}")
            rate_limiter.wait()
            
        logging.info(f"[OK] Scraping {url} in mode: {scrape_mode}")
        
        is_valid, error_message = validate_url(url)
        if not is_valid:
            raise InvalidURLError(error_message, details={'url': url}) # Pass details
        
        hostname = normalize_hostname(url) # This is the correct hostname for path generation
        run_dir = config.get_run_directory()
        
        # Get output paths using the main URL's hostname
        paths = {
            'base_dir': run_dir, # base_dir is run_dir
            'images_dir': run_dir / 'images' / hostname,
            'pages_dir': run_dir / 'pages' / hostname,
            'ocr_dir': run_dir / 'pages' / hostname / 'ocr'
        }
        
        # Create directories if they don't exist
        for dir_path_key, dir_path_val in paths.items(): # Iterate over items for clarity
            if isinstance(dir_path_val, Path): # Ensure it's a Path object
                dir_path_val.mkdir(parents=True, exist_ok=True)
                # logging.debug(f"Ensured directory: {dir_path_val}") # Too verbose for normal runs
        
        metrics = {
            'browser_init': 0.0, 'page_load': 0.0, 'content_extraction': 0.0,
            'image_processing': {'count': 0, 'successful': 0, 'failed': 0, 'total': 0.0},
            'file_saving': 0.0, 'total_time': 0.0
        }
        
        start_time = time.time()
        browser_init_start = time.time()
        
        html_content = "" # Ensure defined in this scope
        cleaned_text = "" # Ensure defined
        ocr_results: List[Dict[str, Any]] = []
        failed_images: List[str] = []
        text_data_for_json: Dict[str, Any] = {} # Ensure defined
        page_path: Optional[Path] = None # Initialize to allow assignment in try
        text_path: Optional[Path] = None
        raw_text_path: Optional[Path] = None
        ocr_summary_path: Optional[Path] = None


        with sync_playwright() as p:
            browser = None 
            try:
                browser = p.chromium.launch(headless=True)
                metrics['browser_init'] = time.time() - browser_init_start
                context = browser.new_context()
                page = context.new_page()
                # Use a timeout from config, e.g. config.PAGE_TIMEOUT_MS or a new one
                page_timeout = getattr(config, 'SCRAPER_PAGE_TIMEOUT_MS', 30000) # Default 30s
                page.set_default_timeout(page_timeout) 

                page_load_start = time.time()
                try:
                    response = page.goto(url, wait_until='domcontentloaded')
                    if not response: raise RuntimeError(f"Failed to load {url}: No response received")
                    if not response.ok:
                        status_code = response.status
                        status_text = response.status_text
                        error_msg_detail = f"Failed to load {url}: HTTP {status_code} - {status_text}"
                        if status_code == 503: raise ServiceUnavailableError(f"Service Unavailable: {error_msg_detail}", details={'url': url, 'status_code': status_code, 'status_text': status_text})
                        elif status_code == 429: raise RateLimitError(f"Rate Limited: {error_msg_detail}", details={'url': url, 'status_code': status_code, 'status_text': status_text})
                        elif 500 <= status_code < 600: raise ServerError(f"Server Error: {error_msg_detail}", status_code, details={'url': url, 'status_code': status_code, 'status_text': status_text})
                        else: raise RuntimeError(error_msg_detail)
                    
                    page.wait_for_load_state('domcontentloaded')
                    try:
                        page.wait_for_load_state('networkidle', timeout=5000)
                    except TimeoutError: logging.warning(f"Timeout waiting for network idle on {url}, continuing anyway")
                    except Exception as e_idle: logging.warning(f"Error waiting for network idle: {str(e_idle)}, continuing anyway")
                except TimeoutError: raise RuntimeError(f"Timeout while loading {url}")
                except PlaywrightError as e_pw: raise RuntimeError(f"Playwright error while loading {url}: {str(e_pw)}")
                finally: metrics['page_load'] = time.time() - page_load_start

                content_extraction_start = time.time()
                if scrape_mode in ['text', 'both']:
                    try:
                        html_content = page.content()
                        # Fallback for body if not present or empty
                        body_element = page.query_selector('body')
                        visible_text = body_element.inner_text() if body_element else ""
                        cleaned_text = clean_text(visible_text)
                    except Exception as e_content: raise RuntimeError(f"Failed to extract page content: {str(e_content)}")

                if scrape_mode in ['ocr', 'both']:
                    image_processing_start = time.time()
                    images_on_page = page.query_selector_all('img')
                    metrics['image_processing']['count'] = len(images_on_page)
                    
                    for img_element in images_on_page:
                        src_attr: Optional[str] = None
                        absolute_img_url_for_loop: Optional[str] = None
                        try:
                            src_attr = img_element.get_attribute('src')
                            if not src_attr: continue
                            
                            absolute_img_url_for_loop = construct_absolute_url(src_attr, url)
                            if not absolute_img_url_for_loop:
                                logging.warning(f"Could not construct absolute URL for image: {src_attr} on page {url}")
                                failed_images.append(src_attr or "unknown_src_on_failed_construct")
                                metrics['image_processing']['failed'] += 1
                                continue

                            img_filename = get_safe_filename(absolute_img_url_for_loop)
                            img_file_path = paths['images_dir'] / img_filename
                                
                            saved_img_path = download_image(absolute_img_url_for_loop, img_file_path)
                            
                            if not saved_img_path:
                                logging.warning(f"Failed to download image: {absolute_img_url_for_loop}")
                                failed_images.append(absolute_img_url_for_loop)
                                metrics['image_processing']['failed'] += 1
                                continue
                                
                            ocr_output_dict = ocr_image(saved_img_path)
                            
                            current_ocr_status = ocr_output_dict.get('ocr_status', 'error_processing') # Get the new status
                            ocr_item = {
                                'image_path': str(saved_img_path),
                                'text': ocr_output_dict['text'],
                                'char_count': ocr_output_dict['char_count'],
                                'word_count': ocr_output_dict['word_count'],
                                'image_url': absolute_img_url_for_loop,
                                'ocr_status': current_ocr_status, # Store the detailed status
                                'ocr_failed': current_ocr_status != 'success' # ocr_failed is true if status is not 'success'
                            }
                            ocr_results.append(ocr_item)

                            if current_ocr_status == 'success':
                                metrics['image_processing']['successful'] += 1
                            else:
                                # Log based on the specific status
                                if current_ocr_status == 'no_text_found':
                                    logging.warning(f"OCR processed but found no text for image: {absolute_img_url_for_loop} (saved at {saved_img_path}) - Status: {current_ocr_status}")
                                elif current_ocr_status.startswith('error_'):
                                    logging.warning(f"OCR error for image: {absolute_img_url_for_loop} (saved at {saved_img_path}) - Status: {current_ocr_status}")
                                else: # Should not happen if ocr_status is always set
                                    logging.warning(f"OCR did not succeed for image: {absolute_img_url_for_loop} (saved at {saved_img_path}) - Status: {current_ocr_status}")
                        except Exception as e_img_proc:
                            failed_img_id = absolute_img_url_for_loop if absolute_img_url_for_loop else (src_attr if src_attr else "unknown_image_src_in_exception")
                            logging.error(f"Failed to process image {failed_img_id}: {str(e_img_proc)}")
                            failed_images.append(failed_img_id)
                            metrics['image_processing']['failed'] += 1
                                            
                    metrics['image_processing']['total'] = time.time() - image_processing_start
                
                metrics['content_extraction'] = time.time() - content_extraction_start
                page.close()
                context.close()
            except Exception as e_browser_setup:
                metrics['browser_init'] = time.time() - browser_init_start
                if browser and browser.is_connected(): browser.close()
                raise RuntimeError(f"Failed to initialize or use browser: {str(e_browser_setup)}") from e_browser_setup
            finally:
                if browser and browser.is_connected(): browser.close()

        file_saving_start = time.time()
        page_path = paths['pages_dir'] / "page.html"
        text_path = paths['pages_dir'] / "text.json"
        raw_text_path = paths['pages_dir'] / "text.txt"
        
        try:
            with open(page_path, 'w', encoding='utf-8') as f: f.write(html_content)
            
            text_data_for_json = create_metadata(url, hostname, text=cleaned_text)
            text_data_for_json['ocr_results_count'] = len(ocr_results)
            text_data_for_json['images_dir'] = str(paths['images_dir'])
            text_data_for_json['failed_images_download'] = failed_images

            with open(text_path, 'w', encoding='utf-8') as f: json.dump(text_data_for_json, f, indent=2, ensure_ascii=False)
            with open(raw_text_path, 'w', encoding='utf-8') as f: f.write(cleaned_text)

            if scrape_mode in ['ocr', 'both']:
                ocr_summary_path = save_ocr_results(paths['ocr_dir'], ocr_results, url, hostname)
        
        except Exception as e_save:
            logging.error(f"Error during file saving phase for {url}: {str(e_save)}", exc_info=True)
            raise RuntimeError(f"Failed to save files for {url}: {str(e_save)}") from e_save
        
        metrics['file_saving'] = time.time() - file_saving_start
        metrics['total_time'] = time.time() - start_time

        logging.info(f"\n[STATS] Performance Metrics for {url}:")
        logging.info(f"Total scraping time: {metrics['total_time']:.2f}s")
        # ... (other metric logs like browser_init, page_load etc. can be added here if needed for verbosity)

        return {
            'html': html_content, 'text': cleaned_text, 'text_data': text_data_for_json,
            'images': ocr_results, 'failed_images': failed_images,
            'page_path': str(page_path), 'text_path': str(text_path), 'raw_text_path': str(raw_text_path),
            'ocr_dir': str(paths['ocr_dir']) if scrape_mode in ['ocr', 'both'] else None,
            'ocr_summary_path': str(ocr_summary_path) if ocr_summary_path else None,
            'images_dir': str(paths['images_dir']),
            'metrics': metrics
        }

    except InvalidURLError as e_url: 
        logging.error(f"[ERROR] Invalid URL for scraping: {url} - {str(e_url)}")
        raise 
    except ServiceUnavailableError as e_serv_unavail: 
        logging.error(f"[ERROR] Service Unavailable for {url} (HTTP 503): {str(e_serv_unavail)}")
        raise
    except RateLimitError as e_rate_limit: 
        logging.error(f"[ERROR] Rate Limited for {url} (HTTP 429): {str(e_rate_limit)}")
        raise
    except ServerError as e_serv: 
        logging.error(f"[ERROR] Server error for {url} (HTTP {e_serv.status_code if hasattr(e_serv, 'status_code') else 'N/A'}): {str(e_serv)}")
        raise
    except ConnectionError as e_conn: 
        logging.error(f"[ERROR] Connection error for {url}: {str(e_conn)}")
        raise
    except ParsingError as e_parse: 
        logging.error(f"[ERROR] Parsing error for {url}: {str(e_parse)}")
        raise
    except OCRError as e_ocr: 
        logging.error(f"[ERROR] OCR error for {url}: {str(e_ocr)}")
        raise
    except RuntimeError as e_rt: 
        logging.error(f"[ERROR] Runtime error during scraping {url}: {str(e_rt)}", exc_info=config.SCRAPER_DEBUG_MODE)
        if isinstance(e_rt.__cause__, KeyError):
             logging.error(f"Root cause of file saving error was KeyError: {str(e_rt.__cause__)}")
             raise ParsingError(f"Data structure error during file saving for {url}: {str(e_rt.__cause__)}", details={'original_runtime_error': str(e_rt)}) from e_rt.__cause__
        else:
             raise ScrapingError(f"Runtime error during scraping {url}: {str(e_rt)}", details={'url': url}) from e_rt
    except Exception as e_unexp: 
        logging.error(f"[ERROR] Unexpected error during scraping {url}: {str(e_unexp)}", exc_info=config.SCRAPER_DEBUG_MODE)
        raise ScrapingError(f"Unexpected error for {url}: {str(e_unexp)}", details={'url': url}) from e_unexp
