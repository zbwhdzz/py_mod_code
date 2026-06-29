from __future__ import annotations
import hashlib
import re
import html
import random
import asyncio
import threading
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional
from functools import wraps
from enum import Enum

def _enum_val(v: Any) -> str:
    return v.value if isinstance(v, Enum) else v

def _safe_path(url: str, suffix: str = "", base_dir: str = ".") -> Path:
    safe = re.sub(r"[^\w\u4e00-\u9fff-]", "_", url)
    if len(safe) > 100:
        safe = safe[:100] + hashlib.md5(url.encode()).hexdigest()[:8]
    return Path(base_dir) / f"{safe}{suffix}"

def safe_html_escape(text: str) -> str:
    return html.escape(text, quote=True)

_background_loop = None
_background_thread = None
_background_lock = threading.Lock()

def run_async(coro: Coroutine) -> Any:
    global _background_loop, _background_thread
    with _background_lock:
        if _background_loop is None or _background_loop.is_closed():
            _background_loop = asyncio.new_event_loop()
            _background_thread = threading.Thread(target=_background_loop.run_forever, daemon=True)
            _background_thread.start()
    try:
        loop = asyncio.get_running_loop()
        future = asyncio.run_coroutine_threadsafe(coro, _background_loop)
        return future.result()
    except RuntimeError:
        future = asyncio.run_coroutine_threadsafe(coro, _background_loop)
        return future.result()

def retry_on_error(max_retries=3, exceptions=(Exception,), backoff_base=2):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    wait = backoff_base ** attempt + random.uniform(0, 1)
                    logger.warning(f"{func.__name__} 第{attempt+1}次失败: {e}，{wait:.1f}s后重试")
                    await asyncio.sleep(wait)
            raise last_exception
        return wrapper
    return decorator