import time
import logging
import random
from functools import wraps
from typing import Callable, TypeVar, Any, Optional, Type, Dict, List, Union, Tuple

from .exceptions import (
    ScrapingError, ConnectionError, ServerError, 
    ServiceUnavailableError, RateLimitError
)

# Type variable for generic function return type
T = TypeVar('T')

def retry_with_backoff(
    max_retries: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 60.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    retry_on_exceptions: Optional[List[Type[Exception]]] = None
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Decorator that retries a function with exponential backoff when specified exceptions occur.
    
    Args:
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay between retries in seconds
        max_delay: Maximum delay between retries in seconds
        backoff_factor: Factor by which the delay increases with each retry
        jitter: Whether to add random jitter to the delay to prevent thundering herd
        retry_on_exceptions: List of exception types to retry on. If None, retries on
                            ConnectionError, ServerError, ServiceUnavailableError, and RateLimitError
    
    Returns:
        Decorated function that will be retried with exponential backoff
    """
    if retry_on_exceptions is None:
        retry_on_exceptions = [
            ConnectionError,
            ServerError, 
            ServiceUnavailableError,
            RateLimitError
        ]
    
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            last_exception = None
            delay = initial_delay
            
            # Try the function up to max_retries + 1 times (initial attempt + retries)
            for attempt in range(max_retries + 1):
                try:
                    # If this is a retry attempt, log it
                    if attempt > 0:
                        logging.info(
                            f"Retry attempt {attempt}/{max_retries} for {func.__name__} "
                            f"after {delay:.2f}s delay"
                        )
                    
                    # Call the function
                    return func(*args, **kwargs)
                    
                except tuple(retry_on_exceptions) as e:
                    last_exception = e
                    
                    # If this was the last attempt, re-raise the exception
                    if attempt >= max_retries:
                        logging.error(
                            f"All {max_retries} retry attempts failed for {func.__name__}. "
                            f"Last error: {str(e)}"
                        )
                        raise
                    
                    # Calculate next delay with exponential backoff
                    delay = min(delay * backoff_factor, max_delay)
                    
                    # Add jitter if enabled (±25% of delay)
                    if jitter:
                        delay = delay * (0.75 + random.random() * 0.5)
                    
                    # Log the exception and retry plan
                    logging.warning(
                        f"Exception in {func.__name__} (attempt {attempt+1}/{max_retries+1}): "
                        f"{type(e).__name__}: {str(e)}. Retrying in {delay:.2f}s..."
                    )
                    
                    # Special handling for specific exceptions
                    if isinstance(e, RateLimitError):
                        logging.warning(
                            f"Rate limit detected. Consider increasing backoff or reducing request frequency."
                        )
                        # For rate limits, we might want to increase the delay more aggressively
                        delay = min(delay * 2, max_delay)
                    elif isinstance(e, ServiceUnavailableError):
                        logging.warning(
                            f"Service unavailable (HTTP 503). Server may be temporarily down or overloaded."
                        )
                    
                    # Wait before retrying
                    time.sleep(delay)
                    
                except Exception as e:
                    # For non-retryable exceptions, log and re-raise immediately
                    logging.error(f"Non-retryable exception in {func.__name__}: {type(e).__name__}: {str(e)}")
                    raise
            
            # This should never be reached due to the raise in the last retry attempt
            # But just in case, re-raise the last exception
            if last_exception:
                raise last_exception
            
            # This should never be reached
            raise RuntimeError(f"Unexpected error in retry logic for {func.__name__}")
        
        return wrapper
    
    return decorator