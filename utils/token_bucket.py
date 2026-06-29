import asyncio
import time
import logging
from typing import Dict

logger = logging.getLogger(__name__)

class DynamicTokenBucket:
    def __init__(self, rate: float, capacity: float = None):
        self._rate = rate
        self._capacity = capacity or rate
        self._tokens = self._capacity
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()
        self._stats = {'total_consumed': 0, 'total_waited': 0.0}
        
    @property
    def rate(self) -> float:
        return self._rate
        
    @rate.setter
    def rate(self, value: float):
        if value <= 0:
            raise ValueError("Rate 必须大于0")
        self._rate = value
        logger.info(f"TokenBucket 速率已调整为: {value}/s")
        
    async def consume(self, tokens: float = 1) -> float:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._last_refill = now
            if self._tokens >= tokens:
                self._tokens -= tokens
                self._stats['total_consumed'] += tokens
                return 0.0
            deficit = tokens - self._tokens
            wait_time = deficit / self._rate
            self._tokens = 0.0
            self._stats['total_waited'] += wait_time
            return wait_time
            
    def get_stats(self) -> Dict:
        return dict(self._stats)