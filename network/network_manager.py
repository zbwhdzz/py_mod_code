from __future__ import annotations
import asyncio
import logging
import random
import time
import re
import socket
import json
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse, urlunparse
from config.models import DownloadConfig
from config.observable import ObservableConfig
from config.enums import AntiCrawlLevel, RefererPolicy, DeviceType
from utils.helpers import _enum_val, _safe_path
from utils.ssrf import is_private_ip
from utils.semaphore import DynamicSemaphore
from utils.token_bucket import DynamicTokenBucket
from .proxy_manager import ProxyManager
from .browser_manager import BrowserManager
from .captcha_solver import CaptchaSolver
from .anti_crawl import AntiCrawlManager
from .site_rules import SiteRuleManager

try:
    import aiohttp
    from aiohttp import ClientSession, TCPConnector, ClientTimeout
except ImportError:
    aiohttp = None
    
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    from curl_cffi.requests import AsyncSession as CurlAsyncSession
    from curl_cffi import CurlError
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False
    CurlError = Exception

try:
    import chardet
    HAS_CHARDET = True
except ImportError:
    HAS_CHARDET = False

logger = logging.getLogger(__name__)

class NetworkManager:
    def __init__(self, config: DownloadConfig):
        self.config = config
        self.observable_config: Optional[ObservableConfig] = None
        self.session = None
        self.semaphore: Optional[DynamicSemaphore] = None
        self._token_bucket: Optional[DynamicTokenBucket] = None
        self._use_curl = config.use_curl_cffi and HAS_CURL_CFFI
        self._curl_failed = False
        self._downgrade_lock = asyncio.Lock()
        self._no_async_lib_warned = False
        self.proxy_manager = (ProxyManager(config.proxy_config) if (config.proxy_config.single or config.proxy_config.rotation or config.proxy_config.proxy_pool_api) else None)
        self.browser_manager = (BrowserManager(config.browser_config) if (config.browser_config.enable and HAS_PLAYWRIGHT) else None)
        if self.browser_manager:
            self.browser_manager.bind_network_manager(self)
        self.anti_crawl = AntiCrawlManager(config)
        self.captcha_solver = CaptchaSolver(config.captcha_config)
        self.site_rule_manager = SiteRuleManager()
        self._allowed_domains = config.allowed_domains
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._request_log: List[Dict] = []
        self._request_log_lock = asyncio.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._pending_config_updates: Dict[str, Any] = {}
        
        if isinstance(config.rate_limit, str) and "req/min" in config.rate_limit:
            try:
                rate = int(config.rate_limit.replace("req/min", "").strip()) / 60.0
                self._token_bucket = DynamicTokenBucket(rate=rate)
            except Exception:
                pass
        if config.custom_rules_file:
            self.site_rule_manager.load_from_file(config.custom_rules_file)
        if config.site_rules:
            self.site_rule_manager.load_rules(config.site_rules)

    def bind_observable_config(self, obs_config: ObservableConfig):
        self.observable_config = obs_config
        def callback(key, old, new):
            if self._loop and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(self._on_config_change(key, old, new), self._loop)
        obs_config.add_listener(callback)
        
    async def _ensure_proxy_manager(self):
        if self.proxy_manager is None and (self.config.proxy_config.single or self.config.proxy_config.rotation or self.config.proxy_config.proxy_pool_api):
            self.proxy_manager = ProxyManager(self.config.proxy_config)
        
    async def _on_config_change(self, key: str, old_val: Any, new_val: Any):
        logger.info(f"配置变更: {key} = {old_val} -> {new_val}")
        if key == "max_workers" and self.semaphore:
            await self.semaphore.set_max(new_val)
        elif key == "rate_limit" and self._token_bucket:
            if isinstance(new_val, str) and "req/min" in new_val:
                try:
                    rate = int(new_val.replace("req/min", "").strip()) / 60.0
                    self._token_bucket.rate = rate
                except Exception as e:
                    logger.error(f"调整令牌桶速率失败: {e}")
        elif key == "random_delay_range":
            logger.info(f"随机延迟范围已更新: {new_val}")
        elif key == "retry_times":
            logger.info(f"重试次数已更新: {new_val}")
        elif key == "verify_ssl":
            logger.info(f"SSL验证已更新: {new_val}")
            await self._rebuild_session()
        elif key.startswith("proxy_config."):
            if self.proxy_manager:
                await self.proxy_manager.close()
                self.proxy_manager = None
            await self._ensure_proxy_manager()
        elif key.startswith("browser_config."):
            if self.browser_manager:
                await self.browser_manager.close()
            if self.config.browser_config.enable and HAS_PLAYWRIGHT:
                self.browser_manager = BrowserManager(self.config.browser_config)
                self.browser_manager.bind_network_manager(self)
                await self.browser_manager.start()
        elif key == "allowed_domains":
            self._allowed_domains = new_val
            logger.info(f"域名白名单已更新: {new_val}")

    async def _rebuild_session(self):
        if self.session:
            try:
                await self.session.close()
            except:
                pass
        self.session = None
        await self._init_session()

    def _check_domain_allowed(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
            host = parsed.hostname
            if not host:
                return False
            if re.match(r'^\d+\.\d+\.\d+\.\d+$', host) or ':' in host:
                if is_private_ip(host):
                    logger.warning(f"拒绝访问内网 IP {host} (SSRF 防护)")
                    return False
                if self._allowed_domains and host not in self._allowed_domains:
                    logger.warning(f"拒绝访问 IP {host} (不在白名单)")
                    return False
                return True
            if self._allowed_domains and host not in self._allowed_domains:
                logger.warning(f"域名不在白名单中: {host}")
                return False
            try:
                resolved_ip = socket.gethostbyname(host)
                if resolved_ip == '0.0.0.0':
                    logger.warning(f"域名 {host} 解析到无效 IP {resolved_ip}，禁止访问")
                    return False
                if is_private_ip(resolved_ip):
                    logger.warning(f"域名 {host} 解析到内网 IP {resolved_ip}，已拦截")
                    return False
            except socket.gaierror:
                logger.warning(f"无法解析域名: {host}")
                return False
            return True
        except Exception:
            return False

    async def __aenter__(self):
        await self._init_session()
        self.semaphore = DynamicSemaphore(self.config.max_workers)
        self._loop = asyncio.get_running_loop()
        if self.config.cookie_file:
            await self._load_cookies(self.config.cookie_file)
        for key, val in self._pending_config_updates.items():
            await self._on_config_change(key, None, val)
        self._pending_config_updates.clear()
        return self

    async def _init_session(self):
        if self._use_curl and not self._curl_failed:
            try:
                logger.debug("尝试初始化 curl_cffi 会话")
                headers = self._get_base_headers()
                self.session = CurlAsyncSession(
                    timeout=self.config.timeout,
                    verify=self.config.verify_ssl,
                    impersonate=self.config.impersonate,
                    headers=headers
                )
                return
            except Exception as e:
                logger.warning(f"curl_cffi 初始化失败: {e}，回退到 aiohttp")
                self._curl_failed = True
                self._use_curl = False
        if HAS_AIOHTTP:
            connector = TCPConnector(
                limit=100, limit_per_host=10, ttl_dns_cache=300, use_dns_cache=True,
                ssl=False if not self.config.verify_ssl else None
            )
            self.session = ClientSession(
                connector=connector,
                timeout=ClientTimeout(total=self.config.timeout),
                headers=self._get_base_headers()
            )
        else:
            if not self._no_async_lib_warned:
                logger.warning("未找到异步 HTTP 库，使用同步 requests (性能较低)")
                self._no_async_lib_warned = True
            self.session = None

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session is not None:
            try:
                await self.session.close()
            except:
                pass
            self.session = None
        if self.browser_manager:
            await self.browser_manager.close()
        if self.proxy_manager:
            await self.proxy_manager.close()

    async def _load_cookies(self, cookie_file: str):
        try:
            with open(cookie_file, "r", encoding="utf-8") as f:
                cookies = json.load(f)
            if not isinstance(cookies, list):
                logger.warning("Cookie文件格式不正确")
                return
            loaded = 0
            for cookie in cookies:
                if isinstance(cookie, dict) and all(k in cookie for k in ("domain","name","value")):
                    domain = cookie["domain"].lstrip(".")
                    if self._use_curl:
                        self.session.cookies.set(cookie["name"], cookie["value"], domain=domain)
                    elif HAS_AIOHTTP:
                        self.session.cookie_jar.update_cookies(
                            {cookie["name"]: cookie["value"]},
                            origin=f"https://{domain}"
                        )
                    loaded += 1
            logger.info(f"已加载 {loaded}/{len(cookies)} 个 cookies")
        except OSError as e:
            logger.warning(f"加载 cookies 失败: {e}")

    def _get_base_headers(self) -> Dict[str, str]:
        device = _enum_val(self.config.device)
        ua = self.anti_crawl.get_next_ua(device)
        headers = {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Cache-Control": "max-age=0",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1"
        }
        if isinstance(self.config.custom_headers, dict):
            headers.update(self.config.custom_headers)
        return headers

    def _apply_referer_policy(self, url: str, current_referer: Optional[str] = None) -> Optional[str]:
        policy = _enum_val(self.config.referer_policy)
        if policy == "none":
            return None
        if policy == "home":
            parsed = urlparse(url)
            return f"{parsed.scheme}://{parsed.netloc}/"
        return current_referer or url

    def _add_random_param(self, url: str) -> str:
        if not self.config.add_random_param:
            return url
        parsed = urlparse(url)
        q = (parsed.query + "&" if parsed.query else "") + f"_={int(time.time() * 1000)}"
        return urlunparse(parsed._replace(query=q))

    def _detect_encoding(self, content: bytes, declared: Optional[str]) -> str:
        if self.config.force_encoding:
            return self.config.force_encoding
        if declared and declared.lower() not in ("none", ""):
            try:
                content.decode(declared)
                return declared
            except Exception:
                pass
        if HAS_CHARDET:
            try:
                r = chardet.detect(content)
                if r and r.get("encoding"):
                    content.decode(r["encoding"])
                    return r["encoding"]
            except Exception:
                pass
        for enc in ["gb18030", "gbk", "gb2312", "big5", "utf-8"]:
            try:
                content.decode(enc)
                return enc
            except Exception:
                continue
        return "utf-8"

    async def fetch(self, url: str, referer: Optional[str] = None, use_browser: bool = False,
                    wait_for_selector: str = None, chapter = None) -> Optional[str]:
        if not self._check_domain_allowed(url):
            return None
        await self._pause_event.wait()
        rule = self.site_rule_manager.get_rule(url)
        if rule and rule.need_render:
            use_browser = True
            wait_for_selector = rule.render_wait or wait_for_selector
        anti_level = self.anti_crawl.get_current_level(url)
        if anti_level in (AntiCrawlLevel.BROWSER, AntiCrawlLevel.CAPTCHA):
            use_browser = True
        if use_browser and self.browser_manager:
            html = await self.browser_manager.fetch(url, wait_for_selector)
            if html:
                await self.anti_crawl.report_success(url)
                return html
            logger.warning("浏览器获取失败，回退到普通请求")
        if self.session is None:
            if self._no_async_lib_warned and not HAS_AIOHTTP:
                return await self._sync_fetch(url, referer)
            await self._init_session()
            if self.session is None:
                return await self._sync_fetch(url, referer)
        max_attempts = self.config.retry_times + 1
        last_error = None
        for attempt in range(max_attempts):
            await self._pause_event.wait()
            async with self.semaphore:
                if self._token_bucket:
                    wait = await self._token_bucket.consume()
                    if wait > 0:
                        await asyncio.sleep(wait)
                low, high = self.config.random_delay_range
                if anti_level == AntiCrawlLevel.DELAY:
                    low, high = low * 2, high * 3
                await asyncio.sleep(random.uniform(low, high))
                headers = self._get_base_headers()
                if anti_level == AntiCrawlLevel.UA_ROTATE:
                    headers["User-Agent"] = self.anti_crawl.get_next_ua(_enum_val(self.config.device))
                if anti_level == AntiCrawlLevel.TLS_ROTATE and self._use_curl:
                    self.config.impersonate = self.anti_crawl.get_next_tls()
                    await self._rebuild_session()
                referer_val = self._apply_referer_policy(url, referer)
                if referer_val:
                    headers["Referer"] = referer_val
                request_url = self._add_random_param(url)
                proxy = await self.proxy_manager.get_proxy() if self.proxy_manager else None
                proxy_url = proxy.get("http") if proxy else None
                start_time = time.time()
                try:
                    result = await self._do_request(
                        request_url, headers, proxy_url, attempt, referer_val,
                        anti_level
                    )
                    duration = time.time() - start_time
                    if result is not None:
                        if proxy_url and self.proxy_manager:
                            await self.proxy_manager.report_success(proxy_url)
                        await self.anti_crawl.report_success(url)
                        await self._log_request({
                            'url': url,
                            'status': 200,
                            'duration': duration,
                            'attempt': attempt + 1,
                            'proxy': bool(proxy_url),
                            'anti_level': anti_level.value,
                            'error': None
                        })
                        return result
                except Exception as e:
                    duration = time.time() - start_time
                    last_error = str(e)
                    logger.debug(f"请求失败 (尝试 {attempt+1}): {e}")
                    if proxy_url and self.proxy_manager:
                        await self.proxy_manager.report_failure(proxy_url)
                    await self._log_request({
                        'url': url,
                        'status': None,
                        'duration': duration,
                        'attempt': attempt + 1,
                        'proxy': bool(proxy_url),
                        'anti_level': anti_level.value,
                        'error': last_error
                    })
                if self.config.anti_crawl_enabled and self.config.anti_crawl_escalation:
                    new_level = await self.anti_crawl.report_failure(url)
                    if new_level != anti_level:
                        logger.warning(f"反爬策略升级: {anti_level.value} -> {new_level.value}")
                        anti_level = new_level
                if self._use_curl and not self._curl_failed and attempt >= self.config.retry_times // 2:
                    async with self._downgrade_lock:
                        if self._use_curl and not self._curl_failed:
                            logger.warning("curl_cffi 持续失败，永久降级到 aiohttp")
                            self._curl_failed = True
                            self._use_curl = False
                            if self.session:
                                await self.session.close()
                            await self._init_session()
                            return await self.fetch(url, referer, use_browser, wait_for_selector, chapter)
                if attempt < self.config.retry_times:
                    wait_time = 2**attempt + random.uniform(0, 1)
                    if anti_level == AntiCrawlLevel.DELAY:
                        wait_time *= 2
                    await asyncio.sleep(wait_time)
        return None

    async def _do_request(self, url: str, headers: dict, proxy_url: Optional[str], 
                         attempt: int, referer: Optional[str], anti_level: AntiCrawlLevel) -> Optional[str]:
        proxy = proxy_url if proxy_url else None
        if self._use_curl and self.session is not None:
            try:
                resp = await self.session.get(url, headers=headers, proxy=proxy)
                async with resp:
                    if resp.status_code == 200:
                        content = await resp.read()
                        encoding = getattr(resp, 'encoding', None)
                        return content.decode(self._detect_encoding(content, encoding), errors="replace")
                    elif resp.status_code in (403, 429, 503, 418, 500, 502, 504):
                        logger.warning(f"风控触发 {resp.status_code}: {url}")
                        if self.config.auto_downgrade and attempt < self.config.retry_times:
                            await asyncio.sleep(self.config.downgrade_sleep * (2**attempt) + random.uniform(0, 5))
                        return None
                    else:
                        return None
            except CurlError as e:
                logger.debug(f"curl 请求异常: {e}")
                return None
        elif HAS_AIOHTTP and self.session is not None:
            try:
                async with self.session.get(url, headers=headers, proxy=proxy, ssl=self.config.verify_ssl) as resp:
                    if resp.status == 200:
                        content = await resp.read()
                        return content.decode(self._detect_encoding(content, resp.charset), errors="replace")
                    elif resp.status in (403, 429, 503, 418, 500, 502, 504):
                        logger.warning(f"风控触发 {resp.status}: {url}")
                        if self.config.auto_downgrade and attempt < self.config.retry_times:
                            await asyncio.sleep(self.config.downgrade_sleep * (2**attempt) + random.uniform(0, 5))
                        return None
                    else:
                        return None
            except aiohttp.ClientError as e:
                logger.debug(f"aiohttp 请求异常: {e}")
                return None
        else:
            return await self._sync_fetch(url, referer)

    async def _sync_fetch(self, url: str, referer: Optional[str] = None) -> Optional[str]:
        if not HAS_REQUESTS:
            logger.error("同步请求需要 requests 库")
            return None
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_fetch_impl, url, referer)

    def _sync_fetch_impl(self, url: str, referer: Optional[str]) -> Optional[str]:
        headers = self._get_base_headers()
        if referer:
            headers["Referer"] = referer
        for attempt in range(self.config.retry_times + 1):
            try:
                resp = requests.get(url, headers=headers, timeout=self.config.timeout, verify=self.config.verify_ssl)
                if resp.status_code == 200:
                    return resp.text
                elif resp.status_code in (403, 429, 503, 418) and self.config.auto_downgrade:
                    wait = self.config.downgrade_sleep * (2**attempt)
                    time.sleep(wait)
                else:
                    time.sleep(2**attempt)
            except requests.RequestException as e:
                logger.debug(f"同步请求失败: {e}")
                time.sleep(2**attempt)
        return None

    async def _log_request(self, entry: Dict):
        if not self.config.log_requests:
            return
        url = entry['url']
        url = re.sub(r'(?i)(token|key|password|secret)=[^&]*', r'\1=***', url)
        proxy_str = "True" if entry['proxy'] else "False"
        async with self._request_log_lock:
            self._request_log.append(entry)
            if len(self._request_log) > 1000:
                self._request_log = self._request_log[-500:]
        logger.info(
            f"URL={url[:80]} | Status={entry['status']} | "
            f"Duration={entry['duration']:.2f}s | Attempt={entry['attempt']} | "
            f"Proxy={proxy_str} | AntiLevel={entry['anti_level']} | "
            f"Error={entry['error'][:100] if entry['error'] else 'None'}"
        )

    def get_request_stats(self) -> Dict:
        total = len(self._request_log)
        success = sum(1 for r in self._request_log if r['status'] == 200)
        failed = total - success
        avg_duration = sum(r['duration'] for r in self._request_log) / max(total, 1)
        return {
            'total': total,
            'success': success,
            'failed': failed,
            'success_rate': f"{success/max(total,1)*100:.1f}%",
            'avg_duration': f"{avg_duration:.2f}s",
            'anti_crawl_stats': self.anti_crawl.get_stats()
        }

    async def pause(self):
        self._pause_event.clear()
        logger.info("下载已暂停")
        
    async def resume(self):
        self._pause_event.set()
        logger.info("下载已恢复")
        
    @property
    def is_paused(self) -> bool:
        return not self._pause_event.is_set()