import asyncio
import random
import time
import os
import logging
from typing import Optional, List, Dict, Set, Union
from config.models import ProxyConfig
from config.enums import ProxyStrategy
from utils.helpers import _enum_val

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

logger = logging.getLogger(__name__)

class ProxyManager:
    def __init__(self, config: ProxyConfig):
        self.config = config
        self.proxies = []
        self.lock = asyncio.Lock()
        self.current_index = 0
        self._health_task: Optional[asyncio.Task] = None
        self._api_refresh_task: Optional[asyncio.Task] = None
        self._proxy_url_set: Set[str] = set()
        if config.single:
            self._add_proxy(config.single)
        elif config.rotation:
            if isinstance(config.rotation, str) and os.path.exists(config.rotation):
                self._load_from_file(config.rotation)
            elif isinstance(config.rotation, list):
                for p in config.rotation:
                    self._add_proxy(p)
            elif isinstance(config.rotation, str) and ',' in config.rotation:
                for p in config.rotation.split(','):
                    self._add_proxy(p.strip())
        if self.proxies and config.health_check_interval > 0:
            asyncio.create_task(self._check_all_proxies())
            self._start_health_check()
        if config.proxy_pool_api:
            asyncio.create_task(self._refresh_from_api())
            self._start_api_refresh()

    def _add_proxy(self, proxy: str):
        proxy = proxy.strip()
        if not proxy or proxy in self._proxy_url_set:
            return
        proxy_type = "socks5" if proxy.startswith("socks5://") else "http"
        self.proxies.append({
            "url": proxy,
            "type": proxy_type,
            "failures": 0,
            "valid": True,
            "last_check": 0.0,
            "success_count": 0,
            "total_count": 0
        })
        self._proxy_url_set.add(proxy)

    def _load_from_file(self, filepath: str):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip() and not line.startswith('#'):
                        self._add_proxy(line.strip())
        except OSError as e:
            logger.error(f"加载代理文件失败: {e}")

    def _start_health_check(self):
        async def health_loop():
            while True:
                await asyncio.sleep(self.config.health_check_interval)
                await self._check_all_proxies()
        self._health_task = asyncio.create_task(health_loop())

    def _start_api_refresh(self):
        async def refresh_loop():
            while True:
                await asyncio.sleep(300)
                await self._refresh_from_api()
        self._api_refresh_task = asyncio.create_task(refresh_loop())

    async def _refresh_from_api(self):
        if not self.config.proxy_pool_api:
            return
        try:
            headers = {}
            if self.config.proxy_pool_key:
                headers['Authorization'] = f'Bearer {self.config.proxy_pool_key}'
            if HAS_REQUESTS:
                loop = asyncio.get_running_loop()
                resp = await loop.run_in_executor(None, lambda: requests.get(
                    self.config.proxy_pool_api,
                    headers=headers,
                    timeout=10
                ))
                if resp.status_code == 200:
                    data = resp.json()
                    new_proxies = []
                    if isinstance(data, list):
                        new_proxies = data
                    elif isinstance(data, dict):
                        new_proxies = data.get('proxies', data.get('data', []))
                    async with self.lock:
                        added = 0
                        for p in new_proxies:
                            proxy_url = p if isinstance(p, str) else p.get('url', p.get('proxy', ''))
                            if proxy_url and proxy_url not in self._proxy_url_set:
                                self._add_proxy(proxy_url)
                                added += 1
                        if added > 0:
                            logger.info(f"从代理池API获取了 {added} 个新代理")
            elif HAS_AIOHTTP:
                async with aiohttp.ClientSession() as s:
                    async with s.get(self.config.proxy_pool_api, headers=headers, timeout=10) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            new_proxies = []
                            if isinstance(data, list):
                                new_proxies = data
                            elif isinstance(data, dict):
                                new_proxies = data.get('proxies', data.get('data', []))
                            async with self.lock:
                                added = 0
                                for p in new_proxies:
                                    proxy_url = p if isinstance(p, str) else p.get('url', p.get('proxy', ''))
                                    if proxy_url and proxy_url not in self._proxy_url_set:
                                        self._add_proxy(proxy_url)
                                        added += 1
                                if added > 0:
                                    logger.info(f"从代理池API获取了 {added} 个新代理")
        except Exception as e:
            logger.debug(f"代理池API刷新失败: {e}")

    async def _check_all_proxies(self):
        async with self.lock:
            if not self.proxies:
                return
            await asyncio.gather(*[self._check_proxy(p) for p in self.proxies])

    async def _check_proxy(self, proxy):
        if HAS_AIOHTTP:
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(self.config.check_url, proxy=proxy["url"],
                                     timeout=aiohttp.ClientTimeout(total=5)) as r:
                        proxy["valid"] = r.status == 200
                        proxy["last_check"] = time.time()
                        if proxy["valid"]:
                            proxy["failures"] = 0
                            proxy["success_count"] += 1
                        proxy["total_count"] += 1
            except Exception:
                proxy["valid"] = False
                proxy["last_check"] = time.time()
        elif HAS_REQUESTS:
            try:
                loop = asyncio.get_running_loop()
                resp = await loop.run_in_executor(
                    None,
                    lambda: requests.get(
                        self.config.check_url,
                        proxies={"http": proxy["url"], "https": proxy["url"]},
                        timeout=5
                    )
                )
                proxy["valid"] = resp.status_code == 200
                proxy["last_check"] = time.time()
                if proxy["valid"]:
                    proxy["failures"] = 0
                    proxy["success_count"] += 1
                proxy["total_count"] += 1
            except Exception:
                proxy["valid"] = False
                proxy["last_check"] = time.time()
        else:
            proxy["valid"] = False

    async def get_proxy(self) -> Optional[Dict[str, str]]:
        async with self.lock:
            valid = [p for p in self.proxies if p["valid"]]
            if not valid:
                valid = self.proxies
            if not valid:
                return None
            strategy = _enum_val(self.config.strategy)
            if strategy == "round_robin":
                proxy = valid[self.current_index % len(valid)]
                self.current_index += 1
            elif strategy == "least_used":
                proxy = min(valid, key=lambda p: p["failures"])
            else:
                proxy = random.choice(valid)
            return {"http": proxy["url"], "https": proxy["url"]}

    async def report_failure(self, proxy_url: str):
        async with self.lock:
            for p in self.proxies:
                if p["url"] == proxy_url:
                    p["failures"] += 1
                    if p["failures"] >= self.config.max_failures:
                        p["valid"] = False
                        logger.warning(f"代理 {proxy_url[:30]}... 已标记为无效 (失败 {p['failures']} 次)")
                    break

    async def report_success(self, proxy_url: str):
        async with self.lock:
            for p in self.proxies:
                if p["url"] == proxy_url:
                    p["failures"] = 0
                    p["success_count"] += 1
                    break

    def get_stats(self) -> Dict:
        total = len(self.proxies)
        valid = sum(1 for p in self.proxies if p["valid"])
        return {
            "total": total,
            "valid": valid,
            "invalid": total - valid,
            "proxies": [
                {
                    "url": p["url"][:30] + "..." if len(p["url"]) > 30 else p["url"],
                    "valid": p["valid"],
                    "failures": p["failures"],
                    "success_rate": f"{p['success_count'] / max(p['total_count'], 1) * 100:.1f}%"
                }
                for p in self.proxies
            ]
        }

    async def close(self):
        for task in [self._health_task, self._api_refresh_task]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass