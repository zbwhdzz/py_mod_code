import json
import time
import asyncio
import logging
from pathlib import Path
from typing import List, Dict
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

class SubscriptionManager:
    def __init__(self, config_file: Path):
        self.config_file = config_file
        self.lock = asyncio.Lock()
        self.subscriptions = self._load()

    def _load(self) -> List[Dict]:
        if not self.config_file.exists():
            return []
        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []

    def _save(self):
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self.subscriptions, f, indent=2, ensure_ascii=False)
        except OSError as e:
            logger.warning(f"保存订阅失败: {e}")

    async def add(self, url: str, name: str = None):
        async with self.lock:
            name = name or urlparse(url).path.strip("/").split("/")[-1] or url
            self.subscriptions.append({"url": url, "name": name, "last_check": 0})
            self._save()

    async def remove(self, index: int):
        async with self.lock:
            if 0 <= index < len(self.subscriptions):
                self.subscriptions.pop(index)
                self._save()

    async def list_all(self) -> List[Dict]:
        async with self.lock:
            return self.subscriptions.copy()

    async def update_last_check(self, index: int):
        async with self.lock:
            if 0 <= index < len(self.subscriptions):
                self.subscriptions[index]["last_check"] = time.time()
                self._save()