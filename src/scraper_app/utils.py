import os
import hashlib
import requests
import time
import logging
from urllib.parse import urlparse, urljoin, urlunparse # Add urlunparse
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Any, Tuple, List, Union
from concurrent.futures import ThreadPoolExecutor, as_completed
from . import config
from .rate_limiter import get_rate_limiter
from .ocr import ocr_image

def construct_absolute_url(url: str, base_url: str) -> Optional[str]:
    """Construct an absolute URL from a potentially relative URL and a base URL.
    
    Args:
        url (str): The URL to make absolute (can be relative or absolute).
        base_url (str): The base URL of the page from which 'url' was extracted.
        
    Returns:
        Optional[str]: The absolute URL, or None if construction fails.
    """
    if not url:
        return None
    try:
        # If URL is already absolute, return it
        parsed_url = urlparse(url)
        if parsed_url.scheme and parsed_url.netloc:
            return url
            
        # Join with base_url to make it absolute
        return urljoin(base_url, url)
    except Exception as e:
        logging.error(f"Error constructing absolute URL for '{url}' with base '{base_url}': {e}")
        return None

def validate_url(url: str) -> Tuple[bool, str]:
    """Validate a URL for scraping.
    
    Args:
        url (str): The URL to validate
        
    Returns:
        Tuple[bool, str]: (is_valid, error_message)
            - is_valid: True if URL is valid for scraping
            - error_message: Description of why URL is invalid, or empty string if valid
    """
    if not url or not isinstance(url, str):
        return False, "URL must be a non-empty string"
        
    # Remove any leading/trailing whitespace
    url = url.strip()
    
    try:
        parsed = urlparse(url)
        
        # Check for required URL components
        if not parsed.scheme:
            return False, "URL must include a scheme (e.g., 'http://' or 'https://')"
            
        if not parsed.netloc:
            return False, "URL must include a domain name"
            
        # Validate scheme
        if parsed.scheme not in ['http', 'https']:
            return False, f"Unsupported URL scheme: '{parsed.scheme}'. Only 'http://' and 'https://' are supported"
            
        # Check for common issues
        if ' ' in url:
            return False, "URL contains spaces. Please remove spaces or use URL encoding"
            
        if not '.' in parsed.netloc:
            return False, f"Invalid domain format: '{parsed.netloc}'. Domain must contain at least one dot"
            
        # Check for minimum domain length
        if len(parsed.netloc) < 3:
            return False, f"Domain name too short: '{parsed.netloc}'"
            
        # Check for maximum URL length (common browser limit is 2048)
        if len(url) > 2048:
            return False, f"URL exceeds maximum length of 2048 characters (current length: {len(url)})"
            
        # Check for invalid characters in domain
        invalid_chars = set('<>{}|\\^~[]`')
        if any(char in parsed.netloc for char in invalid_chars):
            return False, "URL contains invalid characters in domain name"
            
        # Check for invalid characters in path
        if parsed.path:
            # Check for unencoded spaces in path
            if ' ' in parsed.path:
                return False, "URL path contains spaces. Please use URL encoding (e.g., %20)"
            
            # Check for other invalid characters in path
            invalid_path_chars = set('<>{}|\\^~[]`')
            if any(char in parsed.path for char in invalid_path_chars):
                return False, "URL path contains invalid characters"
            
            # Check for consecutive slashes (except for protocol)
            if '//' in parsed.path:
                return False, "URL path contains consecutive slashes"
            
            # Check for maximum path length (common server limit is 2048)
            if len(parsed.path) > 2048:
                return False, f"URL path exceeds maximum length of 2048 characters (current length: {len(parsed.path)})"
        
        # Check for invalid characters in query
        if parsed.query:
            # Check for unencoded spaces in query
            if ' ' in parsed.query:
                return False, "URL query contains spaces. Please use URL encoding (e.g., %20)"
            
            # Check for other invalid characters in query
            invalid_query_chars = set('<>{}|\\^~[]`')
            if any(char in parsed.query for char in invalid_query_chars):
                return False, "URL query contains invalid characters"
            
            # Check for maximum query length (common server limit is 2048)
            if len(parsed.query) > 2048:
                return False, f"URL query exceeds maximum length of 2048 characters (current length: {len(parsed.query)})"
        
        return True, ""
        
    except Exception as e:
        return False, f"Failed to parse URL: {str(e)}"

def download_and_process_image(
    full_url: str,
    img_path: Path,
    ocr_retry_count: int = 3,
    ocr_retry_delay: float = 1.0
) -> Optional[Dict[str, Any]]:
    """Download an image and process it with OCR.
    
    Args:
        full_url (str): Full URL of the image to process
        img_path (Path): Path where the image should be saved
        ocr_retry_count (int, optional): Number of times to retry OCR if it fails. Defaults to 3.
        ocr_retry_delay (float, optional): Delay in seconds between OCR retry attempts. Defaults to 1.0.
        
    Returns:
        Optional[Dict[str, Any]]: Dictionary containing OCR results if successful, None if failed
    """
    filename = img_path.name
    
    # Download the image
    if download_image(full_url, img_path):
        logging.info(f"[OK] Saved image '{filename}' from {full_url}")
        
        # Try OCR with retries
        for attempt in range(ocr_retry_count):
            try:
                # Process with OCR
                ocr_result = ocr_image(str(img_path))
                logging.info(f"[TEXT] OCR successful for '{filename}' - Text preview: {ocr_result['text'][:100]}")
                
                return {
                    'image_url': full_url,
                    'image_path': img_path,
                    'ocr_text': ocr_result['text'],
                    'ocr_text_length': ocr_result['char_count'],
                    'ocr_text_word_count': ocr_result['word_count']
                }
            except Exception as e:
                if attempt < ocr_retry_count - 1:
                    logging.warning(f"OCR attempt {attempt + 1} failed for '{filename}' ({full_url}): {str(e)}")
                    logging.info(f"Retrying OCR for '{filename}' in {ocr_retry_delay} seconds...")
                    time.sleep(ocr_retry_delay)
                    continue
                else:
                    logging.error(f"All OCR attempts failed for '{filename}' ({full_url}): {str(e)}")
                    return None
    else:
        logging.error(f"[ERROR] Failed to download '{filename}' from {full_url}")
        return None

def process_single_image(
    img_url: str,
    base_url: str,
    images_dir: Path,
    ocr_retry_count: int = 3,
    ocr_retry_delay: float = 1.0
) -> Optional[Dict[str, Any]]:
    """Process a single image with OCR.
    
    Args:
        img_url (str): URL of the image to process
        base_url (str): Base URL of the page (for resolving relative URLs)
        images_dir (Path): Directory where images should be saved
        ocr_retry_count (int, optional): Number of times to retry OCR if it fails. Defaults to 3.
        ocr_retry_delay (float, optional): Delay in seconds between OCR retry attempts. Defaults to 1.0.
        
    Returns:
        Optional[Dict[str, Any]]: Dictionary containing OCR results if successful, None if failed
    """
    try:
        # Resolve relative URLs
        full_url = urljoin(base_url, img_url)
        
        # Validate the URL
        parsed = urlparse(full_url)
        if not parsed.scheme or not parsed.netloc:
            logging.warning(f"Skipping image with invalid URL: {full_url}")
            return None
            
        # Generate safe filename and path
        filename = get_safe_filename(full_url)
        img_path = images_dir / filename
        
        # Download and process the image
        return download_and_process_image(
            full_url=full_url,
            img_path=img_path,
            ocr_retry_count=ocr_retry_count,
            ocr_retry_delay=ocr_retry_delay
        )
            
    except Exception as e:
        logging.error(f"[ERROR] Error processing image URL {img_url}: {e}")
        return None

def process_images_concurrently(
    img_urls: List[str],
    base_url: str,
    images_dir: Path,
    max_workers: int = 5,
    ocr_retry_count: int = 3,
    ocr_retry_delay: float = 1.0
) -> List[Dict[str, Any]]:
    """Process multiple images concurrently with OCR.
    
    Args:
        img_urls (List[str]): List of image URLs to process
        base_url (str): Base URL of the page (for resolving relative URLs)
        images_dir (Path): Directory where images should be saved
        max_workers (int, optional): Maximum number of concurrent workers. Defaults to 5.
        ocr_retry_count (int, optional): Number of times to retry OCR if it fails. Defaults to 3.
        ocr_retry_delay (float, optional): Delay in seconds between OCR retry attempts. Defaults to 1.0.
        
    Returns:
        List[Dict[str, Any]]: List of successful OCR results
    """
    successful_results = []
    failed_urls = []
    
    logging.info(f"Starting concurrent processing of {len(img_urls)} images with {max_workers} workers")
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_url = {
            executor.submit(
                process_single_image,
                img_url,
                base_url,
                images_dir,
                ocr_retry_count,
                ocr_retry_delay
            ): img_url
            for img_url in img_urls
        }
        
        # Process completed tasks as they finish
        for future in as_completed(future_to_url):
            img_url = future_to_url[future]
            try:
                result = future.result()
                if result:
                    successful_results.append(result)
                    logging.info(f"[OK] Successfully processed {img_url}")
                else:
                    failed_urls.append(img_url)
                    logging.warning(f"[ERROR] Failed to process {img_url}")
            except Exception as e:
                failed_urls.append(img_url)
                logging.error(f"[ERROR] Error processing {img_url}: {str(e)}")
    
    # Log summary
    logging.info(f"[OK] Completed processing {len(img_urls)} images:")
    logging.info(f"[OK] Successful: {len(successful_results)}")
    logging.info(f"[ERROR] Failed: {len(failed_urls)}")
    if failed_urls:
        logging.info("Failed URLs:")
        for url in failed_urls:
            logging.info(f"  - {url}")
    
    return successful_results

def process_image_for_ocr(
    img_url: Union[str, List[str]], 
    base_url: str, 
    images_dir: Path,
    max_workers: int = 5,
    ocr_retry_count: int = 3,
    ocr_retry_delay: float = 1.0
) -> Union[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """Process one or more images with OCR.
    
    Args:
        img_url (Union[str, List[str]]): Single image URL or list of image URLs to process
        base_url (str): Base URL of the page (for resolving relative URLs)
        images_dir (Path): Directory where images should be saved
        max_workers (int, optional): Maximum number of concurrent workers. Defaults to 5.
        ocr_retry_count (int, optional): Number of times to retry OCR if it fails. Defaults to 3.
        ocr_retry_delay (float, optional): Delay in seconds between OCR retry attempts. Defaults to 1.0.
        
    Returns:
        Union[Optional[Dict[str, Any]], List[Dict[str, Any]]]: 
            - For single URL: Dictionary containing OCR results if successful, None if failed
            - For multiple URLs: List of successful OCR results
    """
    # Handle single URL case
    if isinstance(img_url, str):
        return process_single_image(
            img_url=img_url,
            base_url=base_url,
            images_dir=images_dir,
            ocr_retry_count=ocr_retry_count,
            ocr_retry_delay=ocr_retry_delay
        )
    
    # Handle multiple URLs case
    return process_images_concurrently(
        img_urls=img_url,
        base_url=base_url,
        images_dir=images_dir,
        max_workers=max_workers,
        ocr_retry_count=ocr_retry_count,
        ocr_retry_delay=ocr_retry_delay
    )

def handle_download_error(error: Exception, url: str, attempt: int, raise_on_failure: bool) -> bool:
    """Handle download errors with retry logic and logging.
    
    Args:
        error (Exception): The exception that occurred
        url (str): The URL being downloaded
        attempt (int): Current attempt number
        raise_on_failure (bool): Whether to raise an error on final failure
        
    Returns:
        bool: True if should retry, False if should give up
        
    Raises:
        RuntimeError: If raise_on_failure is True and this was the final attempt
    """
    error_type = type(error).__name__
    error_msg = str(error)
    
    if isinstance(error, requests.exceptions.Timeout):
        logging.warning(f"Timeout while downloading image: {url}")
    elif isinstance(error, requests.exceptions.RequestException):
        logging.warning(f"Network error while downloading image: {url} - {error_msg}")
    else:
        logging.error(f"Unexpected error while downloading image: {url} - {error_msg}")
    
    if attempt < config.IMAGE_RETRY_COUNT - 1:
        logging.info(f"Retrying in {config.IMAGE_RETRY_DELAY} seconds...")
        time.sleep(config.IMAGE_RETRY_DELAY)
        return True
        
    if raise_on_failure:
        final_error = f"{error_type} while downloading image after {config.IMAGE_RETRY_COUNT} attempts: {url} - {error_msg}"
        logging.error(final_error)
        raise RuntimeError(final_error)
    return False

from .retry import retry_with_backoff
from .exceptions import ConnectionError, ServerError, ServiceUnavailableError, RateLimitError

def handle_data_url(data_url: str, path: Path) -> Optional[Path]:
    """Handle downloading of data URLs (base64 encoded images).
    
    Args:
        data_url (str): The data URL to process (e.g. data:image/png;base64,...)
        path (Path): Local path where the image should be saved
        
    Returns:
        Optional[Path]: The path to the saved image if successful, None otherwise
    """
    from .logging_utils import info, error
    import base64
    
    try:
        # Parse the data URL
        if not data_url.startswith('data:'):
            return None # Changed
            
        # Split the data URL into metadata and data
        header, encoded = data_url.split(',', 1)
        
        # Get the mime type
        mime_type = header.split(';')[0].split(':')[1]
        
        # Calculate data size for logging
        data_size = len(encoded) * 3 // 4  # Approximate size of decoded data
        
        # Log attempt with metadata only
        info(f"Processing data URL image: type={mime_type}, size={data_size} bytes",
             category='NETWORK',
             context={'url': f'data:{mime_type};base64,[{data_size} bytes]'})
        
        # Decode the base64 data
        try:
            image_data = base64.b64decode(encoded)
        except Exception as e:
            error(f"Failed to decode base64 data: {str(e)}",
                  category='NETWORK',
                  context={'url': f'data:{mime_type};base64,[{data_size} bytes]'})
            return None # Changed
            
        # Ensure the parent directory exists
        path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write the decoded data to file
        with open(str(path), 'wb') as f:
            f.write(image_data)
            
        info(f"Successfully saved data URL image to {path}",
             category='FILE',
             context={'url': f'data:{mime_type};base64,[{data_size} bytes]',
                     'path': str(path)})
        return path # Changed
        
    except Exception as e:
        error(f"Failed to process data URL: {str(e)}",
              category='NETWORK',
              context={'url': f'data:{mime_type};base64,[{data_size} bytes]'})
        return None # Changed

@retry_with_backoff(
    max_retries=3,
    initial_delay=1.0,
    max_delay=10.0,
    backoff_factor=2.0,
    jitter=True
)
def download_image(url: str, path: Path, raise_on_failure: bool = False) -> Optional[Path]:
    """Download an image with retry logic and rate limiting.
    
    Args:
        url (str): URL of the image to download
        path (Path): Local path where the image should be saved
        raise_on_failure (bool, optional): If True, raises RuntimeError on failure instead of returning False.
            Defaults to False.
        
    Returns:
        Optional[Path]: The path to the saved image if successful, None otherwise (unless raise_on_failure is True)
        
    Raises:
        RuntimeError: If raise_on_failure is True and all retry attempts fail
        ConnectionError: If there are network connectivity issues
        ServerError: If the server returns a 5xx error
    """
    from .logging_utils import info, warning, error
    
    # Handle data URLs separately
    if url.startswith('data:'):
        return handle_data_url(url, path) # handle_data_url now returns Optional[Path]
    
    rate_limiter = get_rate_limiter()
    
    try:
        # Wait for rate limiter before making request
        rate_limiter.wait()
        
        info(f"Downloading image from {url}", category='NETWORK', context={'url': url})
        res = requests.get(url, timeout=config.IMAGE_DOWNLOAD_TIMEOUT)
        
        if res.status_code == 200:
            # Ensure the parent directory exists with proper permissions
            path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(str(path), 'wb') as f:
                f.write(res.content)
            info(f"Successfully downloaded image to {path}", category='FILE', context={'url': url, 'path': str(path)})
            return path # Changed
        else:
            # Handle specific status codes
            if res.status_code == 503:
                raise ServiceUnavailableError(f"Service Unavailable while downloading image: {url}",
                                            {'url': url, 'status_code': res.status_code})
            elif res.status_code == 429:
                raise RateLimitError(f"Rate limited while downloading image: {url}",
                                   {'url': url, 'status_code': res.status_code})
            elif 500 <= res.status_code < 600:
                raise ServerError(f"Server error while downloading image: {url}", res.status_code,
                                {'url': url, 'status_code': res.status_code})
            else:
                warning(f"Failed to download image (HTTP {res.status_code}): {url}",
                      category='NETWORK', context={'url': url, 'status_code': res.status_code})
                if raise_on_failure:
                    error_msg = f"Failed to download image: {url} (HTTP {res.status_code})"
                    error(error_msg, category='NETWORK', context={'url': url, 'status_code': res.status_code})
                    raise RuntimeError(error_msg)
                return None # Changed
            
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
        error_msg = f"Network error while downloading image: {url} - {str(e)}"
        error(error_msg, category='NETWORK', context={'url': url, 'error': str(e)}, exc_info=e)
        if raise_on_failure: # Ensure consistent behavior with raise_on_failure
             raise ConnectionError(error_msg, details={'url': url, 'error_type': type(e).__name__}) from e
        return None # Changed
        
    except (ServiceUnavailableError, RateLimitError, ServerError):
        # These are custom exceptions that should be re-raised for the retry decorator to catch
        if raise_on_failure: # Ensure consistent behavior with raise_on_failure
            raise
        return None # Changed
        
    except Exception as e:
        error_msg = f"Unexpected error while downloading image: {url} - {str(e)}"
        error(error_msg, category='NETWORK', context={'url': url, 'error': str(e)}, exc_info=e)
        if raise_on_failure:
            raise RuntimeError(error_msg) from e
        return None # Changed

def get_safe_filename(url: str) -> str:
    """Convert a URL to a safe filename.
    
    Args:
        url (str): URL to convert to a filename
        
    Returns:
        str: Safe filename with extension and unique suffix
        
    Note:
        - Truncates long URLs to prevent overly long filenames
        - Replaces invalid filesystem characters with underscores
        - Ensures unique filenames using URL hash
        - Handles URLs with no extension by using default extension
    """
    try:
        # Maximum length for the base filename (before extension and hash)
        MAX_BASE_LENGTH = 100
        
        # Get the extension from the URL path
        parsed_url = urlparse(url)
        ext = os.path.splitext(parsed_url.path)[1]
        
        # Use default extension if none found
        if not ext:
            ext = config.DEFAULT_IMAGE_EXTENSION
            
        # Generate a unique hash from the full URL
        url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
        
        # Get the filename from the path, or use a default
        base_name = os.path.basename(parsed_url.path)
        if not base_name or base_name == '/':
            base_name = 'image'
            
        # Remove any existing extension from base_name
        base_name = os.path.splitext(base_name)[0]
        
        # Replace invalid filesystem characters with underscores
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            base_name = base_name.replace(char, '_')
            
        # Remove any leading/trailing spaces and dots
        base_name = base_name.strip('. ')
        
        # Truncate if too long, ensuring we don't cut in the middle of a word
        if len(base_name) > MAX_BASE_LENGTH:
            # Try to find a space to truncate at
            truncate_point = base_name[:MAX_BASE_LENGTH].rfind('_')
            if truncate_point == -1:  # No underscore found, just truncate
                truncate_point = MAX_BASE_LENGTH
            base_name = base_name[:truncate_point]
            
        # If base_name is empty after cleaning, use a default
        if not base_name:
            base_name = 'image'
            
        # Construct final filename: base_name_hash.ext
        filename = f'{base_name}_{url_hash}{ext}'
        
        logging.debug(f"Generated safe filename for {url}: {filename}")
        return filename
        
    except Exception as e:
        logging.error(f"Failed to generate safe filename for {url}: {str(e)}")
        # Fallback to a basic safe filename
        return f'image_{hashlib.md5(url.encode()).hexdigest()[:8]}{config.DEFAULT_IMAGE_EXTENSION}'

def create_text_metadata(text: str) -> Dict[str, Any]:
    """Create metadata for text content.
    
    Args:
        text (str): The text content to process
        
    Returns:
        Dict[str, Any]: Dictionary containing text-specific metadata
    """
    return {
        'text': text,
        'text_length': len(text),
        'word_count': len(text.split())
    }

def create_ocr_metadata(ocr_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Create metadata for OCR results.
    
    Args:
        ocr_results (List[Dict[str, Any]]): List of OCR results to process
        
    Returns:
        Dict[str, Any]: Dictionary containing OCR-specific metadata
    """
    return {
        'total_images': len(ocr_results),
        'total_ocr_text_length': sum(r['ocr_text_length'] for r in ocr_results),
        'total_ocr_word_count': sum(r['ocr_text_word_count'] for r in ocr_results),
        'ocr_results': [
            {
                'image_url': r['image_url'],
                'image_path': str(r['image_path']),
                'ocr_text_length': r['ocr_text_length'],
                'ocr_text_word_count': r['ocr_text_word_count']
            }
            for r in ocr_results
        ]
    }

def create_metadata(url: str, hostname: str, text: Optional[str] = None, ocr_results: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Create a metadata dictionary for text or OCR results.
    
    Args:
        url (str): The URL being processed
        hostname (str): The hostname of the URL
        text (Optional[str]): The text content to include metadata for
        ocr_results (Optional[List[Dict[str, Any]]]): List of OCR results to include metadata for
        
    Returns:
        Dict[str, Any]: Metadata dictionary with common fields and optional text/OCR fields
    """
    # Create base metadata with common fields
    metadata = {
        'url': url,
        'hostname': hostname,
        'timestamp': datetime.utcnow().isoformat()
    }
    
    # Add text metadata if provided
    if text is not None:
        metadata.update(create_text_metadata(text))
    
    # Add OCR metadata if provided
    if ocr_results is not None:
        metadata.update(create_ocr_metadata(ocr_results))
    
    # Log what type of metadata was generated
    metadata_types = []
    if text is not None:
        metadata_types.append("text")
    if ocr_results is not None:
        metadata_types.append(f"OCR ({len(ocr_results)} images)")
    logging.debug(f"Generated metadata for {url} with: {', '.join(metadata_types) if metadata_types else 'basic info only'}")
    
    return metadata

def create_scraper_directories(base_dir: Path, hostname: Optional[str] = None) -> Dict[str, Path]:
    """Create the directory structure for scraper output.
    
    Args:
        base_dir (Path): Base directory for all scraper output
        hostname (Optional[str]): Hostname to create specific directories for
        
    Returns:
        Dict[str, Path]: Dictionary containing paths to created directories:
            - 'base_dir': Root directory for all output
            - 'images_dir': Directory for downloaded images
            - 'pages_dir': Directory for page content
            - 'ocr_dir': Directory for OCR results (only if hostname provided)
            
    Raises:
        RuntimeError: If directory creation fails
    """
    try:
        # Get the current run directory
        run_dir = config.get_run_directory()
        
        # Define directory structure using the existing run directory
        directories = {
            'base_dir': run_dir,
            'images_dir': run_dir / 'images',
            'pages_dir': run_dir / 'pages'
        }
        
        # Add hostname-specific directories if hostname provided
        if hostname:
            directories.update({
                'hostname_images_dir': directories['images_dir'] / hostname,
                'hostname_pages_dir': directories['pages_dir'] / hostname,
                'ocr_dir': (directories['pages_dir'] / hostname / 'ocr')
            })
        
        # Create all directories
        for dir_path in directories.values():
            if not dir_path.exists():
                dir_path.mkdir(parents=True, exist_ok=True)
                logging.debug(f"Created directory: {dir_path}")
            else:
                logging.debug(f"Directory already exists: {dir_path}")
        
        # Log directory structure
        if hostname:
            logging.info(f"Created directory structure for {hostname}:")
            logging.info(f"• Images: {directories['hostname_images_dir']}")
            logging.info(f"• Pages: {directories['hostname_pages_dir']}")
            logging.info(f"• OCR: {directories['ocr_dir']}")
        else:
            logging.info("Created base directory structure:")
            logging.info(f"• Images: {directories['images_dir']}")
            logging.info(f"• Pages: {directories['pages_dir']}")
        
        return directories
        
    except Exception as e:
        error_msg = f"Failed to create directory structure: {str(e)}"
        logging.error(error_msg)
        raise RuntimeError(error_msg)

def normalize_hostname(url: str) -> str:
    """Normalize a hostname by removing 'www.' prefix, converting to lowercase, and making it safe for filenames.
    
    Args:
        url (str): URL or hostname to normalize
        
    Returns:
        str: Normalized hostname (lowercase, www. removed, dots replaced by underscores)
    """
    logging.debug(f"Normalizing hostname for URL: {url}")
    # If it's a full URL, extract the hostname
    if '://' in url:
        parsed = urlparse(url)
        hostname = parsed.netloc
    else:
        hostname = url
        
    # Convert to lowercase
    hostname = hostname.lower() # ADDED
        
    # Remove 'www.' prefix if present
    if hostname.startswith('www.'):
        hostname = hostname[4:]
        
    # Replace dots with underscores
    hostname = hostname.replace('.', '_')
    
    # Replace other problematic characters (though hostname should be fairly clean)
    invalid_chars = r'/\?%*:|"<>' + ''.join(map(chr, range(32))) # Control characters
    for char in invalid_chars:
        hostname = hostname.replace(char, '_')
    
    logging.debug(f"Normalized hostname: {hostname}")
    return hostname

def get_url_specific_safe_dirname(url: str) -> str:
    """
    Creates a safe and unique directory name from a URL.
    The URL is canonicalized before hashing to ensure consistency.
    """
    logging.debug(f"Generating safe directory name for URL: {url}")
    try:
        parsed = urlparse(url)

        # Canonicalize:
        # 1. Lowercase scheme and netloc (hostname)
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()

        # 2. Remove default port
        if (scheme == 'http' and parsed.port == 80) or \
           (scheme == 'https' and parsed.port == 443):
            netloc = parsed.hostname # hostname doesn't include port

        # 3. Path: ensure leading slash, normalize (e.g. /a/./b/../c -> /a/c)
        path = parsed.path
        if not path:
            path = '/'
        # Basic normalization (os.path.normpath might be too aggressive for URLs)
        path = path.replace('//', '/') # Replace double slashes
        # A more robust path normalization might be needed for complex cases

        # 4. Query parameters: sort them (optional, can be complex)
        # For now, we'll keep query params as they are, but this is a point of potential variation
        query = parsed.query
        
        # 5. Fragment: generally remove for canonical resource identification
        fragment = '' # Remove fragment

        canonical_parts = (scheme, netloc, path, parsed.params, query, fragment)
        canonical_url = urlunparse(canonical_parts)
        
        logging.debug(f"Canonical URL for dirname: {canonical_url}")

        # Use MD5 hash of the canonical URL for the directory name
        # This ensures uniqueness and avoids issues with long/problematic characters
        url_hash = hashlib.md5(canonical_url.encode('utf-8')).hexdigest()
        logging.debug(f"Generated directory name hash: {url_hash} for URL: {url}")
        return url_hash
    except Exception as e:
        logging.error(f"Error generating safe directory name for {url}: {e}", exc_info=True)
        # Fallback: hash the original URL if canonicalization fails
        return hashlib.md5(url.encode('utf-8')).hexdigest() + "_fallback"
