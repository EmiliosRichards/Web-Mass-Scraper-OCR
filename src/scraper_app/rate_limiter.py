import time
import logging
import os
from threading import Lock
from typing import Optional, Dict
from . import config

def _get_env_float(key: str, default: float) -> float:
    """Get a float value from environment variables with a default fallback.
    
    Args:
        key: Environment variable name
        default: Default value if environment variable is not set or invalid
        
    Returns:
        float: The configured value or default
    """
    try:
        value = os.environ.get(key)
        if value is not None:
            return float(value)
    except ValueError:
        logging.warning(f"Invalid value for {key}, using default: {default}")
    return default

class RateLimiter:
    """Rate limiter implementation using token bucket algorithm.
    
    This class implements a token bucket algorithm for rate limiting requests.
    It allows for a maximum number of requests per second with a configurable burst capacity.
    
    Configuration can be set via environment variables:
    - RATE_LIMIT_REQUESTS_PER_SECOND: Maximum requests per second
    - RATE_LIMIT_BURST_SIZE: Maximum burst capacity
    
    For named resources, use:
    - RATE_LIMIT_{RESOURCE_NAME}_REQUESTS_PER_SECOND
    - RATE_LIMIT_{RESOURCE_NAME}_BURST_SIZE
    """
    
    def __init__(self, 
                 requests_per_second: Optional[float] = None,
                 burst_size: Optional[int] = None,
                 resource_name: Optional[str] = None):
        """Initialize the rate limiter.
        
        Args:
            requests_per_second: Maximum number of requests allowed per second.
                               If None, uses environment variable or falls back to config
            burst_size: Maximum number of requests allowed in burst.
                       If None, uses environment variable or falls back to config
            resource_name: Optional name for this rate limiter instance.
                          If provided, allows for resource-specific configuration
        """
        self.resource_name = resource_name
        
        # Get resource-specific environment variable names if resource_name is provided
        rate_env = f'RATE_LIMIT_{resource_name.upper()}_REQUESTS_PER_SECOND' if resource_name else 'RATE_LIMIT_REQUESTS_PER_SECOND'
        burst_env = f'RATE_LIMIT_{resource_name.upper()}_BURST_SIZE' if resource_name else 'RATE_LIMIT_BURST_SIZE'
        
        self.rate: float = requests_per_second if requests_per_second is not None else \
            _get_env_float(rate_env, config.MAX_REQUESTS_PER_SECOND)
        
        burst = burst_size if burst_size is not None else \
            int(_get_env_float(burst_env, config.RATE_LIMIT_BURST))
        
        self.capacity: float = float(burst)
        self.tokens: float = float(burst)  # Start with full bucket
        self.last_update: float = time.time()
        self.lock: Lock = Lock()
        
        resource_info = f" for resource '{resource_name}'" if resource_name else ""
        logging.info(
            f"Initialized rate limiter{resource_info}: {self.rate} req/s with burst of {self.capacity} "
            f"(configured via {'environment variables' if os.environ.get(rate_env) or os.environ.get(burst_env) else 'default config'})"
        )
    
    def _update_tokens(self) -> None:
        """Update the token count based on elapsed time."""
        now: float = time.time()
        time_passed: float = now - self.last_update
        self.last_update = now
        
        # Add new tokens based on time passed
        new_tokens: float = time_passed * self.rate
        self.tokens = min(self.capacity, self.tokens + new_tokens)
    
    def acquire(self, timeout: Optional[float] = None) -> bool:
        """Acquire a token for making a request.
        
        Args:
            timeout: Maximum time to wait for a token in seconds.
                    If None, wait indefinitely.
        
        Returns:
            bool: True if a token was acquired, False if timeout occurred
        """
        start_time: float = time.time()
        sleep_time: float = 0.1  # Initial sleep time in seconds
        max_sleep_time: float = 1.0  # Maximum sleep time in seconds
        
        while True:
            with self.lock:
                self._update_tokens()
                
                if self.tokens >= 1:
                    self.tokens -= 1
                    logging.debug(f"Token acquired. Remaining tokens: {self.tokens:.2f}")
                    return True
                
                if timeout is not None:
                    elapsed: float = time.time() - start_time
                    if elapsed >= timeout:
                        logging.warning(
                            f"Token acquisition timed out after {elapsed:.2f}s. "
                            f"Current tokens: {self.tokens:.2f}, "
                            f"Rate: {self.rate} req/s"
                        )
                        return False
                
                logging.debug(
                    f"Waiting for token. Current tokens: {self.tokens:.2f}, "
                    f"Rate: {self.rate} req/s, "
                    f"Sleep time: {sleep_time:.2f}s"
                )
            
            # Sleep with exponential backoff
            time.sleep(sleep_time)
            sleep_time = min(sleep_time * 1.5, max_sleep_time)  # Increase sleep time up to max
    
    def wait(self) -> None:
        """Wait until a token is available.
        
        This is a convenience method that calls acquire() with no timeout.
        """
        self.acquire()
    
    def reset(self) -> None:
        """Reset the token bucket to its initial capacity.
        
        This method resets the token count to the maximum burst capacity and
        updates the last update timestamp. Useful for resetting the rate limiter
        after a period of inactivity or when starting a new batch of requests.
        
        Note: This method is thread-safe and will acquire the lock before resetting.
        """
        with self.lock:
            self.tokens = self.capacity
            self.last_update = time.time()
            logging.info(
                f"Rate limiter reset: tokens={self.tokens:.2f}, "
                f"capacity={self.capacity:.2f}, "
                f"rate={self.rate} req/s"
            )

# Global rate limiter instances
_rate_limiters: Dict[str, RateLimiter] = {}

def get_rate_limiter(resource_name: Optional[str] = None) -> RateLimiter:
    """Get a rate limiter instance for the specified resource.
    
    Args:
        resource_name: Optional name of the resource to get a rate limiter for.
                      If None, returns the default rate limiter.
    
    Returns:
        RateLimiter: The rate limiter instance for the specified resource
    """
    global _rate_limiters
    
    if resource_name is None:
        resource_name = "default"
    
    if resource_name not in _rate_limiters:
        _rate_limiters[resource_name] = RateLimiter(resource_name=resource_name)
    
    return _rate_limiters[resource_name] 