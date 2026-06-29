import asyncio
import logging
from typing import Dict, List, Optional
from collections import defaultdict
from urllib.parse import urlparse
from config.models import DownloadConfig
from config.enums import AntiCrawlLevel

logger = logging.getLogger(__name__)

class AntiCrawlManager:
    ESCALATION_CHAIN = [
        AntiCrawlLevel.NORMAL,
        AntiCrawlLevel.DELAY,
        AntiCrawlLevel.UA_ROTATE,
        AntiCrawlLevel.PROXY,
        AntiCrawlLevel.TLS_ROTATE,
        AntiCrawlLevel.BROWSER,
        AntiCrawlLevel.CAPTCHA,
    ]
    
    def __init__(self, config: DownloadConfig):
        self.config = config
        self._failure_counts: Dict[str, int] = defaultdict(int)
        self._success_counts: Dict[str, int] = defaultdict(int)
        self._current_levels: Dict[str, AntiCrawlLevel] = {}
        self._lock = asyncio.Lock()
        self._ua_pool = self._build_ua_pool()
        self._ua_index = 0
        self._tls_profiles = ["chrome120", "chrome121", "edge120", "firefox121", "safari17"]
        self._tls_index = 0
        
    def _build_ua_pool(self) -> Dict[str, List[str]]:
        return {
            "pc": [
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_2) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0",
            ],
            "android": [
                "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
                "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Mobile Safari/537.36",
            ],
            "iphone": [
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
                "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
            ]
        }
        
    async def report_failure(self, url: str, status_code: Optional[int] = None) -> AntiCrawlLevel:
        async with self._lock:
            hostname = urlparse(url).hostname or url
            self._failure_counts[hostname] += 1
            count = self._failure_counts[hostname]
            if not self.config.anti_crawl_enabled or not self.config.anti_crawl_escalation:
                return self._current_levels.get(hostname, AntiCrawlLevel.NORMAL)
            current = self._current_levels.get(hostname, AntiCrawlLevel.NORMAL)
            threshold = self.config.anti_crawl_failure_threshold
            new_level = current
            if count >= threshold:
                current_idx = self.ESCALATION_CHAIN.index(current)
                new_idx = min(current_idx + 1, len(self.ESCALATION_CHAIN) - 1)
                new_level = self.ESCALATION_CHAIN[new_idx]
                if new_level != current:
                    self._current_levels[hostname] = new_level
                    logger.warning(f"[{hostname}] 连续失败 {count} 次，升级反爬策略: {current.value} -> {new_level.value}")
                self._failure_counts[hostname] = 0
                self._success_counts[hostname] = 0
            return self._current_levels.get(hostname, AntiCrawlLevel.NORMAL)
            
    async def report_success(self, url: str):
        async with self._lock:
            hostname = urlparse(url).hostname or url
            if hostname in self._failure_counts:
                self._failure_counts[hostname] = 0
            self._success_counts[hostname] = self._success_counts.get(hostname, 0) + 1
            if self.config.anti_crawl_enabled and self.config.anti_crawl_escalation:
                current = self._current_levels.get(hostname, AntiCrawlLevel.NORMAL)
                if current != AntiCrawlLevel.NORMAL:
                    if self._success_counts[hostname] >= self.config.anti_crawl_downgrade_after:
                        idx = self.ESCALATION_CHAIN.index(current)
                        if idx > 0:
                            new_level = self.ESCALATION_CHAIN[idx-1]
                            self._current_levels[hostname] = new_level
                            logger.info(f"[{hostname}] 连续成功 {self._success_counts[hostname]} 次，降级反爬: {current.value} -> {new_level.value}")
                            self._success_counts[hostname] = 0
                    
    def get_current_level(self, url: str) -> AntiCrawlLevel:
        hostname = urlparse(url).hostname or url
        return self._current_levels.get(hostname, AntiCrawlLevel.NORMAL)
        
    def get_next_ua(self, device: str = "pc") -> str:
        pool = self._ua_pool.get(device, self._ua_pool["pc"])
        ua = pool[self._ua_index % len(pool)]
        self._ua_index += 1
        return ua
        
    def get_next_tls(self) -> str:
        profile = self._tls_profiles[self._tls_index % len(self._tls_profiles)]
        self._tls_index += 1
        return profile
        
    async def reset_level(self, url: str):
        async with self._lock:
            hostname = urlparse(url).hostname or url
            self._failure_counts.pop(hostname, None)
            self._success_counts.pop(hostname, None)
            self._current_levels.pop(hostname, None)
        
    def get_stats(self) -> Dict:
        return {
            'failure_counts': dict(self._failure_counts),
            'success_counts': dict(self._success_counts),
            'current_levels': {k: v.value for k, v in self._current_levels.items()}
        }