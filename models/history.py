import json
import time
import asyncio
import logging
from pathlib import Path
from typing import List, Dict
from .novel import Novel
from .result import DownloadResult

logger = logging.getLogger(__name__)

class HistoryManager:
    DEFAULT_HISTORY_FILE = Path.home() / ".novel_spider_history.json"
    MAX_ENTRIES = 5
    _lock = asyncio.Lock()

    @classmethod
    async def _load(cls) -> List[Dict]:
        if not cls.DEFAULT_HISTORY_FILE.exists():
            return []
        try:
            with open(cls.DEFAULT_HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"加载历史记录失败: {e}")
            return []

    @classmethod
    async def _save(cls, history: List[Dict]):
        try:
            cls.DEFAULT_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(cls.DEFAULT_HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存历史记录失败: {e}")

    @classmethod
    async def add_record(cls, novel: Novel, results: List[DownloadResult]):
        async with cls._lock:
            history = await cls._load()
            sorted_results = sorted(results, key=lambda r: r.chapter.index)
            last_chapter = sorted_results[-1].chapter if sorted_results else None
            entry = {
                "title": novel.title,
                "url": novel.base_url,
                "last_chapter": last_chapter.title if last_chapter else "无",
                "timestamp": time.time(),
                "total_chapters": len(novel.chapters),
                "downloaded": sum(1 for r in results if r.success),
                "failed": sum(1 for r in results if not r.success),
            }
            history.insert(0, entry)
            history = history[: cls.MAX_ENTRIES]
            await cls._save(history)

    @classmethod
    async def get_history(cls) -> List[Dict]:
        return await cls._load()

    @classmethod
    async def clear_history(cls):
        async with cls._lock:
            if cls.DEFAULT_HISTORY_FILE.exists():
                cls.DEFAULT_HISTORY_FILE.unlink()