import os
import hashlib
import requests
import time
import logging
import re # Added import re
from urllib.parse import urlparse, urljoin, urlunparse 
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Any, Tuple, List, Union
from concurrent.futures import ThreadPoolExecutor, as_completed
from . import config
from .rate_limiter import get_rate_limiter
from .ocr import ocr_image 

from .exceptions import ConnectionError, ServerError, ServiceUnavailableError, RateLimitError


def construct_absolute_url(url: str, base_url: str) -> Optional[str]:
    """Construct an absolute URL from a potentially relative URL and a base URL."""
    if not url:
        return None
    try:
        parsed_url = urlparse(url)
        if parsed_url.scheme and parsed_url.netloc:
            return url
        return urljoin(base_url, url)
    except Exception as e:
        logging.error(f"Error constructing absolute URL for '{url}' with base '{base_url}': {e}")
        return None

def validate_url(url: str) -> Tuple[bool, str]:
    """Validate a URL for scraping."""
    if not url or not isinstance(url, str):
        return False, "URL must be a non-empty string"
    url = url.strip()
    try:
        parsed = urlparse(url)
        if not parsed.scheme: return False, "URL must include a scheme (e.g., 'http://' or 'https://')"
        if not parsed.netloc: return False, "URL must include a domain name"
        if parsed.scheme not in ['http', 'https']: return False, f"Unsupported URL scheme: '{parsed.scheme}'"
        if ' ' in url: return False, "URL contains spaces. Please remove spaces or use URL encoding"
        if not '.' in parsed.netloc: return False, f"Invalid domain format: '{parsed.netloc}'"
        if len(parsed.netloc) < 3: return False, f"Domain name too short: '{parsed.netloc}'"
        if len(url) > 2048: return False, f"URL exceeds maximum length of 2048 characters (current length: {len(url)})"
        
        invalid_chars = set('<>{}|\\^~[]`')
        if any(char in parsed.netloc for char in invalid_chars): return False, "URL contains invalid characters in domain name"
        
        if parsed.path:
            if ' ' in parsed.path: return False, "URL path contains spaces. Please use URL encoding (e.g., %20)"
            if any(char in parsed.path for char in invalid_chars): return False, "URL path contains invalid characters"
            if '//' in parsed.path: return False, "URL path contains consecutive slashes"
            if len(parsed.path) > 2048: return False, f"URL path exceeds maximum length (current length: {len(parsed.path)})"
        
        if parsed.query:
            if ' ' in parsed.query: return False, "URL query contains spaces. Please use URL encoding (e.g., %20)"
            if any(char in parsed.query for char in invalid_chars): return False, "URL query contains invalid characters"
            if len(parsed.query) > 2048: return False, f"URL query exceeds maximum length (current length: {len(parsed.query)})"
        
        return True, ""
    except Exception as e:
        return False, f"Failed to parse URL: {str(e)}"

def download_and_process_image(
    full_url: str, img_path: Path, ocr_retry_count: int = 3, ocr_retry_delay: float = 1.0
) -> Optional[Dict[str, Any]]:
    """Download an image and process it with OCR. Returns OCRResult compatible dict or None."""
    filename = img_path.name
    if download_image(full_url, img_path): 
        logging.info(f"[OK] Saved image '{filename}' from {full_url}")
        for attempt in range(ocr_retry_count):
            try:
                ocr_result_dict = ocr_image(str(img_path)) 
                logging.info(f"[TEXT] OCR successful for '{filename}' - Text preview: {ocr_result_dict['text'][:100]}")
                return {
                    'image_url': full_url,
                    'image_path': str(img_path), 
                    'text': ocr_result_dict['text'],
                    'char_count': ocr_result_dict['char_count'],
                    'word_count': ocr_result_dict['word_count'],
                    'ocr_failed': False 
                }
            except Exception as e:
                if attempt < ocr_retry_count - 1:
                    logging.warning(f"OCR attempt {attempt + 1} failed for '{filename}' ({full_url}): {str(e)}")
                    time.sleep(ocr_retry_delay)
                else:
                    logging.error(f"All OCR attempts failed for '{filename}' ({full_url}): {str(e)}")
                    return { 
                        'image_url': full_url, 'image_path': str(img_path), 
                        'text': "", 'char_count': 0, 'word_count': 0, 'ocr_failed': True
                    }
    else:
        logging.error(f"[ERROR] Failed to download '{filename}' from {full_url}")
        return {
            'image_url': full_url, 'image_path': str(img_path), 
            'text': "", 'char_count': 0, 'word_count': 0, 'ocr_failed': True, 'download_failed': True
        }
    return None 

def process_single_image(
    img_url: str, base_url: str, images_dir: Path, 
    ocr_retry_count: int = 3, ocr_retry_delay: float = 1.0
) -> Optional[Dict[str, Any]]:
    try:
        full_url = construct_absolute_url(img_url, base_url)
        if not full_url:
            logging.warning(f"Skipping image with invalid or unconstructable URL from src: {img_url}")
            return None
            
        parsed = urlparse(full_url)
        if not parsed.scheme or not parsed.netloc:
            logging.warning(f"Skipping image with invalid scheme/netloc: {full_url}")
            return None
            
        filename = get_safe_filename(full_url)
        img_path = images_dir / filename
        
        return download_and_process_image(
            full_url=full_url, img_path=img_path,
            ocr_retry_count=ocr_retry_count, ocr_retry_delay=ocr_retry_delay
        )
    except Exception as e:
        logging.error(f"[ERROR] Error processing image URL {img_url}: {e}")
        return None

def process_images_concurrently(
    img_urls: List[str], base_url: str, images_dir: Path, max_workers: int = 5,
    ocr_retry_count: int = 3, ocr_retry_delay: float = 1.0
) -> List[Dict[str, Any]]:
    successful_results = []
    logging.info(f"Starting concurrent processing of {len(img_urls)} images with {max_workers} workers")
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_url = {
            executor.submit(
                process_single_image, img_url, base_url, images_dir,
                ocr_retry_count, ocr_retry_delay
            ): img_url for img_url in img_urls
        }
        for future in as_completed(future_to_url):
            original_img_src = future_to_url[future]
            try:
                result = future.result()
                if result: 
                    successful_results.append(result)
            except Exception as e:
                logging.error(f"[ERROR] Exception for {original_img_src} in ThreadPool: {str(e)}")
    
    logging.info(f"Completed concurrent processing. Successful results: {len(successful_results)}/{len(img_urls)}")
    return successful_results

def handle_download_error(error: Exception, url: str, attempt: int, max_retries: int, retry_delay: float, raise_on_failure: bool) -> bool:
    """Handles download errors with logging. Returns True if retry should occur."""
    logging.warning(f"Attempt {attempt + 1}/{max_retries} failed for {url}: {type(error).__name__} - {str(error)}")
    if attempt < max_retries - 1:
        logging.info(f"Retrying in {retry_delay:.2f} seconds...")
        time.sleep(retry_delay)
        return True
    else:
        logging.error(f"All {max_retries} attempts failed for {url}. Last error: {type(error).__name__} - {str(error)}")
        if raise_on_failure:
            raise RuntimeError(f"Failed to download {url} after {max_retries} attempts.") from error
        return False

def handle_data_url(data_url: str, path: Path) -> Optional[Path]:
    """Handle downloading of data URLs (base64 encoded images)."""
    from .logging_utils import info, error 
    import base64
    
    mime_type = "unknown" 
    data_size_approx = 0  
    try:
        if not data_url.startswith('data:'): return None
        header, encoded = data_url.split(',', 1)
        mime_type = header.split(';')[0].split(':')[1]
        data_size_approx = len(encoded) * 3 // 4
        
        info(f"Processing data URL image: type={mime_type}, size_approx={data_size_approx} bytes",
             category='NETWORK', context={'url_stub': f'data:{mime_type};base64,[{data_size_approx}b]'})
        
        image_data = base64.b64decode(encoded)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(path), 'wb') as f: f.write(image_data)
        info(f"Successfully saved data URL image to {path}", category='FILE', 
             context={'path': str(path), 'url_stub': f'data:{mime_type};base64,[{data_size_approx}b]'})
        return path
    except Exception as e:
        error(f"Failed to process data URL (type: {mime_type}, size_approx: {data_size_approx}b): {str(e)}",
              category='NETWORK', context={'url_stub': f'data:{mime_type};base64,[{data_size_approx}b]'})
        return None

def download_image(url: str, path: Path, raise_on_failure: bool = False) -> Optional[Path]:
    """Download an image with retry logic and rate limiting."""
    from .logging_utils import info, warning, error 

    if url.startswith('data:'):
        return handle_data_url(url, path)
    
    rate_limiter = get_rate_limiter(normalize_hostname(url)) 
    
    for attempt in range(config.IMAGE_RETRY_COUNT):
        try:
            rate_limiter.wait()
            info(f"Downloading image (attempt {attempt+1}) from {url}", category='NETWORK', context={'url': url})
            res = requests.get(url, timeout=config.IMAGE_DOWNLOAD_TIMEOUT, stream=True) 
            res.raise_for_status() 

            path.parent.mkdir(parents=True, exist_ok=True)
            with open(str(path), 'wb') as f:
                for chunk in res.iter_content(chunk_size=8192): 
                    f.write(chunk)
            info(f"Successfully downloaded image to {path}", category='FILE', context={'url': url, 'path': str(path)})
            return path
        except requests.exceptions.HTTPError as e: 
            error_msg = f"HTTP error {e.response.status_code} while downloading {url}: {str(e)}"
            warning(error_msg, category='NETWORK', context={'url': url, 'status_code': e.response.status_code})
            if not handle_download_error(e, url, attempt, config.IMAGE_RETRY_COUNT, config.IMAGE_RETRY_DELAY, raise_on_failure):
                return None 
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            error_msg = f"Network error (attempt {attempt+1}) for {url}: {str(e)}"
            warning(error_msg, category='NETWORK', context={'url': url})
            if not handle_download_error(e, url, attempt, config.IMAGE_RETRY_COUNT, config.IMAGE_RETRY_DELAY, raise_on_failure):
                return None
        except Exception as e: 
            error_msg = f"Unexpected error (attempt {attempt+1}) downloading {url}: {str(e)}"
            error(error_msg, category='NETWORK', context={'url': url})
            if not handle_download_error(e, url, attempt, config.IMAGE_RETRY_COUNT, config.IMAGE_RETRY_DELAY, raise_on_failure):
                return None
    return None 

def get_safe_filename(url: str) -> str:
    """Convert a URL to a safe filename, including a hash of the query."""
    try:
        parsed_url = urlparse(url)
        path_part = Path(parsed_url.path)
        filename = path_part.name
        
        if not filename:
            filename = hashlib.md5(parsed_url.path.encode('utf-8')).hexdigest()[:8]

        name, ext = os.path.splitext(filename)
        safe_name = re.sub(r'[^\w\.-]', '_', name)
        safe_ext = re.sub(r'[^\w\.]', '_', ext)

        if parsed_url.query:
            query_hash = hashlib.md5(parsed_url.query.encode('utf-8')).hexdigest()[:8]
            safe_name = f"{safe_name}_{query_hash}"
            
        if not safe_ext and '.' not in safe_name: 
            if path_part.suffix:
                 safe_ext = re.sub(r'[^\w\.]', '_', path_part.suffix)
            else:
                 safe_ext = config.DEFAULT_IMAGE_EXTENSION 
        
        final_filename = safe_name + safe_ext
        
        max_len = 100
        if len(final_filename) > max_len:
            name_part, ext_part = os.path.splitext(final_filename)
            allowed_name_len = max_len - len(ext_part)
            final_filename = name_part[:allowed_name_len] + ext_part
            
        return final_filename if final_filename else "unknown_image"
    except Exception as e:
        logging.error(f"Error creating safe filename for {url}: {e}")
        return hashlib.md5(url.encode('utf-8')).hexdigest() + config.DEFAULT_IMAGE_EXTENSION

def create_text_metadata(text: str) -> Dict[str, Any]:
    """Creates metadata for extracted text."""
    return {
        'text_length': len(text),
        'word_count': len(text.split()),
        'paragraph_count': len([p for p in text.split('\n') if p.strip()])
    }

def create_ocr_metadata(ocr_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Generate a summary of OCR results from a list of image OCR dictionaries.
    Each dictionary in ocr_results is expected to have 'text', 'char_count', 'word_count',
    'image_url', 'image_path', and 'ocr_failed' (boolean).
    """
    total_text_list = []
    total_char_count = 0
    total_word_count = 0
    successful_ocr_count = 0
    image_summaries = []

    for result_item in ocr_results:
        text = result_item.get('text', '')
        char_count = result_item.get('char_count', 0) 
        word_count = result_item.get('word_count', 0) 
        
        is_successful_ocr = not result_item.get('ocr_failed', True) and bool(text)
        if is_successful_ocr:
            successful_ocr_count += 1
            total_text_list.append(text)
        
        # Always sum up char_count and word_count from the ocr_image output
        total_char_count += char_count 
        total_word_count += word_count
        
        image_summaries.append({
            'image_url': result_item.get('image_url', ''),
            'image_path': str(result_item.get('image_path', '')),
            'ocr_text_length': char_count, 
            'ocr_text_word_count': word_count, 
            'ocr_success': is_successful_ocr
        })

    return {
        'total_ocr_text': "\n\n".join(total_text_list).strip(),
        'total_ocr_text_length': total_char_count, 
        'total_ocr_word_count': total_word_count, 
        'image_count': len(ocr_results),
        'successful_ocr_count': successful_ocr_count,
        'success_rate': (successful_ocr_count / len(ocr_results)) * 100 if ocr_results else 0,
        'image_summaries': image_summaries
    }

def create_metadata(url: str, hostname: str, text: Optional[str] = None, ocr_results: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Create metadata dictionary for scraped content."""
    metadata: Dict[str, Any] = {
        'url': url,
        'hostname': hostname,
        'timestamp': datetime.now().isoformat(),
    }
    if text is not None:
        metadata['text_data'] = create_text_metadata(text)
    
    if ocr_results is not None:
        ocr_summary_data = create_ocr_metadata(ocr_results)
        metadata.update(ocr_summary_data) 
        
    return metadata

def create_scraper_directories(base_dir: Path, hostname: Optional[str] = None) -> Dict[str, Path]:
    """Create the directory structure for scraper output."""
    logging.debug(f"Ensuring directories under base: {base_dir}" + (f" for host: {hostname}" if hostname else ""))
    paths: Dict[str, Path] = {
        "base": base_dir,
        "images": base_dir / config.IMAGES_SUBDIR,
        "pages": base_dir / config.PAGES_SUBDIR,
    }
    if hostname:
        paths["host_images"] = paths["images"] / hostname
        paths["host_pages"] = paths["pages"] / hostname
        paths["host_ocr"] = paths["host_pages"] / config.OCR_SUBDIR
    
    for path_obj in paths.values():
        try:
            path_obj.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logging.error(f"Failed to create directory {path_obj}: {e}")
            raise 
            
    logging.info(f"Ensured directories: {list(paths.keys())}")
    return paths

def normalize_hostname(url: str) -> str:
    """Normalize a hostname to be filesystem-safe."""
    try:
        hostname = urlparse(url).netloc
        if not hostname: 
            return "unknown_host_" + hashlib.md5(url.encode('utf-8')).hexdigest()[:8]
        safe_hostname = re.sub(r'[^\w-]', '_', hostname.replace('.', '_'))
        return safe_hostname.lower()
    except Exception as e:
        logging.warning(f"Could not normalize hostname for URL '{url}': {e}")
        return "error_normalizing_host"

def get_url_specific_safe_dirname(url: str) -> str: 
    """
    Creates a safe directory name from a URL, typically using the hostname
    and a hash of the path to ensure uniqueness for different pages from the same host.
    """
    try:
        parsed_url = urlparse(url)
        host_part = normalize_hostname(url) 

        path_query = parsed_url.path
        if parsed_url.query:
            path_query += "?" + parsed_url.query
        
        path_hash = hashlib.md5(path_query.encode('utf-8')).hexdigest()[:8]
        
        return f"{host_part}_{path_hash}"
    except Exception as e:
        logging.error(f"Error creating URL specific dirname for {url}: {e}")
        return hashlib.md5(url.encode('utf-8')).hexdigest()
