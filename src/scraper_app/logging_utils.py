import logging
import sys
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, Union
import time

# Define log levels with emojis for better visual distinction
LOG_LEVELS = {
    'DEBUG': '🔍',
    'INFO': 'ℹ️',
    'WARNING': '⚠️',
    'ERROR': '❌',
    'CRITICAL': '🔥',
}

# Define log categories with emojis
LOG_CATEGORIES = {
    'NETWORK': '🌐',
    'DATABASE': '💾',
    'FILE': '📄',
    'SCRAPE': '🔍',
    'OCR': '👁️',
    'RETRY': '🔄',
    'SUMMARY': '📊',
    'CONFIG': '⚙️',
    'RATE_LIMIT': '⏱️',
    'PROCESS': '⚙️',
}

class StructuredLogFormatter(logging.Formatter):
    """
    Custom formatter that adds structured information to log messages.
    Includes timestamps, log level, and optional context information.
    """
    def __init__(self, include_emojis: bool = True, include_context: bool = True):
        super().__init__()
        self.include_emojis = include_emojis
        self.include_context = include_context
    
    def format(self, record: logging.LogRecord) -> str:
        # Get the log level emoji if enabled
        level_emoji = LOG_LEVELS.get(record.levelname, '') if self.include_emojis else ''
        
        # Format the basic message with timestamp and level
        timestamp = datetime.fromtimestamp(record.created).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        
        # Extract category if available
        category = getattr(record, 'category', None)
        category_str = ''
        if category and self.include_emojis:
            category_emoji = LOG_CATEGORIES.get(category, '•')
            category_str = f"{category_emoji} [{category}] "
        elif category:
            category_str = f"[{category}] "
        
        # Format the basic message
        message = f"{timestamp} - {level_emoji} {record.levelname} - {category_str}{record.getMessage()}"
        
        # Add context information if available and enabled
        if self.include_context:
            context = getattr(record, 'context', None)
            if context and isinstance(context, dict):
                context_str = ' | '.join(f"{k}={v}" for k, v in context.items())
                message += f" | {context_str}"
            
            # Add URL context if available
            url = getattr(record, 'url', None)
            if url and 'url=' not in message:
                message += f" | url={url}"
        
        # Add exception info if available
        if record.exc_info:
            message += '\n' + self.formatException(record.exc_info)
        
        return message

def configure_logging(
    log_file: Optional[Path] = None,
    console_level: str = 'INFO',
    file_level: str = 'INFO',  # Changed default from DEBUG to INFO
    include_emojis: bool = True,
    include_context: bool = True
) -> None:
    """
    Configure logging with consistent formatting across all modules.
    
    Args:
        log_file: Path to the log file. If None, only console logging is configured.
        console_level: Minimum log level for console output.
        file_level: Minimum log level for file output.
        include_emojis: Whether to include emojis in log messages.
        include_context: Whether to include context information in log messages.
    """
    # Create root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)  # Capture all logs, filtering happens at handler level
    
    # Remove any existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Create formatters
    console_formatter = StructuredLogFormatter(include_emojis=include_emojis, include_context=include_context)
    file_formatter = StructuredLogFormatter(include_emojis=True, include_context=False)  # Don't include full context in file logs
    
    # Configure console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(getattr(logging, console_level))
    root_logger.addHandler(console_handler)
    
    # Configure file handler if log_file is provided
    if log_file:
        print(f"[DEBUG PRINT] configure_logging: Attempting to set up file logger for: {str(log_file)}", flush=True)
        # Ensure the directory exists
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            print(f"[DEBUG PRINT] configure_logging: Ensured log directory exists: {str(log_file.parent)}", flush=True)
        except Exception as e:
            print(f"[DEBUG PRINT] configure_logging: ERROR creating log directory {str(log_file.parent)}: {e}", flush=True)
            
        try:
            file_handler = logging.FileHandler(str(log_file), encoding='utf-8')
            file_handler.setFormatter(file_formatter)
            file_handler.setLevel(getattr(logging, file_level))
            root_logger.addHandler(file_handler)
            print(f"[DEBUG PRINT] configure_logging: File handler ADDED for {str(log_file)} with level {file_level}", flush=True)
        except Exception as e:
            print(f"[DEBUG PRINT] configure_logging: ERROR adding file handler for {str(log_file)}: {e}", flush=True)
    else:
        print("[DEBUG PRINT] configure_logging: log_file is None, skipping file handler setup.", flush=True)
            
    # Add a filter to reduce duplicate logs
    class DuplicateFilter(logging.Filter):
        def __init__(self):
            super().__init__()
            self.last_log = None
            self.last_time = 0
            self.timeout = 1.0  # Ignore duplicates within 1 second
            
        def filter(self, record):
            current_time = time.time()
            msg = record.getMessage()
            
            # Check if this is a duplicate message within the timeout period
            if (self.last_log == msg and 
                current_time - self.last_time < self.timeout):
                return False
                
            self.last_log = msg
            self.last_time = current_time
            return True
    
    # Add the filter to both handlers
    duplicate_filter = DuplicateFilter()
    console_handler.addFilter(duplicate_filter)
    if log_file:
        file_handler.addFilter(duplicate_filter)
    
    logging.info(f"Logging configured: console={console_level}, file={file_level if log_file else 'disabled'}", 
                extra={'category': 'CONFIG'})

def log(
    level: str,
    message: str,
    category: Optional[str] = None,
    url: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
    exc_info: Union[bool, Exception, None] = None
) -> None:
    """
    Log a message with structured context information.
    
    Args:
        level: Log level ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')
        message: The log message
        category: Optional category for the log message
        url: Optional URL related to the log message
        context: Optional dictionary of context information
        exc_info: Exception information to include
    """
    extra = {}
    if category:
        extra['category'] = category
    if url:
        extra['url'] = url
    if context:
        extra['context'] = context
    
    logger = logging.getLogger()
    log_method = getattr(logger, level.lower())
    log_method(message, extra=extra, exc_info=exc_info)

# Convenience methods for different log levels
def debug(message: str, category: Optional[str] = None, url: Optional[str] = None, 
          context: Optional[Dict[str, Any]] = None, exc_info: Union[bool, Exception, None] = None) -> None:
    log('DEBUG', message, category, url, context, exc_info)

def info(message: str, category: Optional[str] = None, url: Optional[str] = None, 
         context: Optional[Dict[str, Any]] = None, exc_info: Union[bool, Exception, None] = None) -> None:
    log('INFO', message, category, url, context, exc_info)

def warning(message: str, category: Optional[str] = None, url: Optional[str] = None, 
            context: Optional[Dict[str, Any]] = None, exc_info: Union[bool, Exception, None] = None) -> None:
    log('WARNING', message, category, url, context, exc_info)

def error(message: str, category: Optional[str] = None, url: Optional[str] = None, 
          context: Optional[Dict[str, Any]] = None, exc_info: Union[bool, Exception, None] = None) -> None:
    log('ERROR', message, category, url, context, exc_info)

def critical(message: str, category: Optional[str] = None, url: Optional[str] = None, 
             context: Optional[Dict[str, Any]] = None, exc_info: Union[bool, Exception, None] = None) -> None:
    log('CRITICAL', message, category, url, context, exc_info)

# Add the new logging module to __init__.py
def update_init_file():
    """Update the __init__.py file to include the new logging module."""
    init_file = Path(__file__).parent / "__init__.py"
    if init_file.exists():
        with open(init_file, 'r') as f:
            content = f.read()
        
        if "logging_utils" not in content:
            with open(init_file, 'a') as f:
                f.write("\nfrom . import logging_utils\n")