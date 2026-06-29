import asyncio
import time
import logging
from typing import Any, Callable, Dict, List
from collections import deque
from copy import deepcopy
from .models import DownloadConfig

logger = logging.getLogger(__name__)

class ObservableConfig:
    def __init__(self, config_obj: DownloadConfig):
        self._config = config_obj
        self._lock = asyncio.Lock()
        self._callbacks: List[Callable[[str, Any, Any], None]] = []
        self._change_history: deque = deque(maxlen=100)
        
    def add_listener(self, callback: Callable[[str, Any, Any], None]):
        async def _add():
            async with self._lock:
                self._callbacks.append(callback)
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                asyncio.create_task(_add())
            else:
                asyncio.run(_add())
        except RuntimeError:
            asyncio.run(_add())
            
    def remove_listener(self, callback: Callable):
        async def _remove():
            async with self._lock:
                if callback in self._callbacks:
                    self._callbacks.remove(callback)
        asyncio.create_task(_remove())
                
    async def _notify(self, key: str, old_val: Any, new_val: Any):
        async with self._lock:
            callbacks = self._callbacks.copy()
            self._change_history.append({
                'time': time.time(),
                'key': key,
                'old': old_val,
                'new': new_val
            })
        for cb in callbacks:
            try:
                cb(key, old_val, new_val)
            except Exception as e:
                logger.error(f"配置监听器错误: {e}")
                
    def get(self, key: str, default=None):
        parts = key.split('.')
        obj = self._config
        for part in parts:
            if hasattr(obj, part):
                obj = getattr(obj, part)
            elif isinstance(obj, dict) and part in obj:
                obj = obj[part]
            else:
                return default
        return obj
            
    async def set(self, key: str, value: Any) -> bool:
        async with self._lock:
            parts = key.split('.')
            obj = self._config
            for part in parts[:-1]:
                if hasattr(obj, part):
                    obj = getattr(obj, part)
                elif isinstance(obj, dict) and part in obj:
                    obj = obj[part]
                else:
                    return False
            last_key = parts[-1]
            old_val = getattr(obj, last_key, None) if hasattr(obj, last_key) else obj.get(last_key) if isinstance(obj, dict) else None
            if old_val == value:
                return True
            if hasattr(obj, last_key):
                setattr(obj, last_key, value)
            elif isinstance(obj, dict):
                obj[last_key] = value
            else:
                return False
            asyncio.create_task(self._notify(key, old_val, value))
            return True
            
    async def update(self, updates: Dict[str, Any]) -> Dict[str, bool]:
        results = {}
        for key, value in updates.items():
            results[key] = await self.set(key, value)
        return results
        
    def snapshot(self) -> DownloadConfig:
        return deepcopy(self._config)
        
    async def get_history(self, limit: int = 20) -> List[Dict]:
        async with self._lock:
            return list(self._change_history)[-limit:]
            
    @property
    def config(self):
        return self._config