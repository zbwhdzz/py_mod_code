import asyncio
import logging
import base64
from typing import Optional
from config.models import BrowserConfig
from utils.ssrf import is_private_ip

try:
    from playwright.async_api import async_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

logger = logging.getLogger(__name__)

class BrowserManager:
    def __init__(self, config: BrowserConfig):
        self.config = config
        self.browser = self.context = self.page = self.playwright = None
        self._lock = asyncio.Lock()
        self._initialized = False
        self._network_manager = None

    def bind_network_manager(self, network_manager):
        self._network_manager = network_manager

    async def start(self):
        if not HAS_PLAYWRIGHT:
            logger.error("Playwright未安装")
            return None
        async with self._lock:
            if self._initialized:
                return self.page
            try:
                self.playwright = await async_playwright().start()
                self.browser = await self.playwright.chromium.launch(
                    headless=self.config.headless
                )
                self.context = await self.browser.new_context(
                    viewport={"width": self.config.viewport_width, "height": self.config.viewport_height},
                    locale=self.config.locale,
                    timezone_id=self.config.timezone,
                    device_scale_factor=self.config.device_scale_factor,
                    has_touch=self.config.has_touch
                )
                self.page = await self.context.new_page()
                if self.config.stealth_mode:
                    await self._apply_stealth()
                self._initialized = True
                logger.info("浏览器管理器初始化成功")
                return self.page
            except Exception as e:
                logger.error(f"浏览器启动失败: {e}")
                await self._cleanup()
                return None

    async def _cleanup(self):
        if self.page:
            try:
                await self.page.close()
            except:
                pass
            self.page = None
        if self.context:
            try:
                await self.context.close()
            except:
                pass
            self.context = None
        if self.browser:
            try:
                await self.browser.close()
            except:
                pass
            self.browser = None
        if self.playwright:
            try:
                await self.playwright.stop()
            except:
                pass
            self.playwright = None
        self._initialized = False

    async def _apply_stealth(self):
        scripts = [
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});",
            "Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});",
            "Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh']});",
            "Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});",
            "Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});",
            "delete window.cdc_adoQpoasnfa76pfcZLmcfl_;",
            "Object.defineProperty(navigator, 'maxTouchPoints', {get: () => 0});",
            "Object.defineProperty(navigator, 'deviceMemory', {get: () => 8});",
            "Object.defineProperty(screen, 'colorDepth', {get: () => 24});",
        ]
        for script in scripts:
            try:
                await self.page.add_init_script(script)
            except:
                pass

    async def close(self):
        async with self._lock:
            await self._cleanup()

    async def fetch(self, url: str, wait_for_selector: str = None, timeout: int = None) -> Optional[str]:
        if self._network_manager:
            if not self._network_manager._check_domain_allowed(url):
                logger.warning(f"浏览器拒绝访问内网/禁止域名: {url}")
                return None
        if not self.page:
            await self.start()
            if not self.page:
                logger.error("浏览器启动失败")
                return None
        try:
            page_timeout = timeout or self.config.render_timeout or 30
            await self.page.goto(url, wait_until="networkidle", timeout=page_timeout * 1000)
            selector = wait_for_selector or self.config.wait_for_selector
            if selector:
                await self.page.wait_for_selector(selector, timeout=15000)
            await asyncio.sleep(1)
            return await self.page.content()
        except Exception as e:
            logger.error(f"浏览器获取失败: {e}")
            return None
            
    async def solve_captcha(self, captcha_url: str = None, selector: str = None) -> Optional[str]:
        if not self.page:
            return None
        try:
            if selector:
                element = await self.page.query_selector(selector)
                if element:
                    screenshot = await element.screenshot()
                    return base64.b64encode(screenshot).decode()
            else:
                screenshot = await self.page.screenshot()
                return base64.b64encode(screenshot).decode()
        except Exception as e:
            logger.error(f"验证码截图失败: {e}")
            return None