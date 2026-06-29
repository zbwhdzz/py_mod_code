import asyncio
import logging
from typing import Dict

logger = logging.getLogger(__name__)

class DynamicSemaphore:
    def __init__(self, value: int):
        if value <= 0:
            value = 1
        self._value = value
        self._max = value
        self._cond = asyncio.Condition()
        self._stats = {'acquired': 0, 'released': 0, 'max_concurrent': 0}

    async def set_max(self, new_max: int):
        if new_max <= 0:
            new_max = 1
        async with self._cond:
            delta = new_max - self._max
            self._max = new_max
            self._value += delta
            self._stats['max_concurrent'] = max(self._stats['max_concurrent'], self._max - self._value)
            if self._value > 0:
                self._cond.notify(self._value)
        logger.info(f"动态信号量最大值调整为 {new_max}，当前许可 {self._value}")

    async def acquire(self):
        async with self._cond:
            while self._value <= 0:
                await self._cond.wait()
            self._value -= 1
            self._stats['acquired'] += 1
            self._stats['max_concurrent'] = max(self._stats['max_concurrent'], self._max - self._value)

    async def release(self):
        async with self._cond:
            self._value += 1
            if self._value > self._max:
                self._value = self._max
            self._stats['released'] += 1
            self._cond.notify()

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.release()

    def get_stats(self) -> Dict:
        return {
            'max': self._max,
            'current': self._value,
            'stats': self._stats.copy()
        }