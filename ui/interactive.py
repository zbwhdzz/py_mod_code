from __future__ import annotations
import sys
import os
import json
import time
import re
import asyncio
import threading
from pathlib import Path
from typing import List, Dict, Any, Optional, Set, Tuple
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
from config.models import DownloadConfig, ProxyConfig, BrowserConfig, CaptchaConfig, SiteRule
from config.enums import DeviceType, OutputFormat, ProxyStrategy, RefererPolicy, CatalogMode, BatchMode, AntiCrawlLevel
from config.observable import ObservableConfig
from config.validator import validate_config
from models.history import HistoryManager
from models.subscription import SubscriptionManager
from downloader.novel_downloader import NovelDownloader
from parser.content_parser import GenericParser
from storage.storage_backend import StorageFactory
from network.proxy_manager import ProxyManager
from utils.helpers import _safe_path, run_async
from .gui import start_gui

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

class InteractiveUI:
    CONFIG_NAMES_CN = {
        "max_workers": "并发线程数",
        "batch_size": "批量大小",
        "retry_times": "重试次数",
        "timeout": "超时时间(秒)",
        "chapter_threshold": "章节数阈值",
        "max_pages": "最大翻页数",
        "device": "设备类型",
        "custom_headers": "自定义头",
        "cookie_file": "Cookie文件",
        "force_encoding": "强制编码",
        "verify_ssl": "SSL验证",
        "save_interval": "保存间隔",
        "random_delay_range": "随机延迟范围",
        "rate_limit": "限速设置",
        "impersonate": "TLS指纹模拟",
        "referer_policy": "Referer策略",
        "auto_downgrade": "自动降级",
        "downgrade_sleep": "降级后暂停(秒)",
        "add_random_param": "添加随机参数",
        "respect_robots": "遵守robots.txt",
        "proxy_config": "代理配置",
        "browser_config": "浏览器配置",
        "output_format": "输出格式",
        "output_dir": "输出目录",
        "download_images": "下载图片",
        "deduplicate": "内容去重",
        "chapter_filter": "章节过滤正则",
        "cache_chapters": "目录缓存",
        "error_report": "错误报告",
        "chapter_selector": "自定义选择器",
        "catalog_mode": "目录模式",
        "batch_mode": "批量模式",
        "max_concurrent_novels": "最大并发小说数",
        "state_file": "状态文件",
        "retry_failed": "重试失败章节",
        "dynamic_workers": "动态并发",
        "verbose": "详细输出",
        "config_file": "配置文件",
        "single": "单个代理",
        "rotation": "代理列表/文件",
        "check_url": "检测URL",
        "max_failures": "最大失败次数",
        "strategy": "轮换策略",
        "health_check_interval": "健康检查间隔(秒)",
        "enable": "启用浏览器",
        "headless": "无头模式",
        "stealth_mode": "隐身模式",
        "viewport_width": "视口宽度",
        "viewport_height": "视口高度",
        "locale": "区域设置",
        "timezone": "时区",
        "device_scale_factor": "设备缩放因子",
        "has_touch": "触屏支持",
        "wait_for_selector": "等待选择器",
        "use_curl_cffi": "使用TLS指纹模拟",
        "auto_update": "自动追更",
        "update_interval_hours": "追更间隔(小时)",
        "enable_notification": "下载完成通知",
        "pdf_font_path": "PDF中文字体路径",
        "allowed_domains": "允许域名白名单",
        "download_timeout": "整体下载超时(秒)",
        "anti_crawl_enabled": "启用反反爬",
        "anti_crawl_failure_threshold": "反爬升级阈值",
        "anti_crawl_escalation": "自动升级反爬",
        "anti_crawl_downgrade_after": "反爬降级阈值",
        "log_dir": "日志目录",
        "log_requests": "记录请求日志",
        "subscribe_file": "订阅文件",
        "search_enabled": "启用搜索",
        "search_sources": "搜索源站点",
        "custom_rules_file": "自定义规则文件",
        "proxy_pool_api": "代理池API",
        "proxy_pool_key": "代理池密钥",
        "auto_render_threshold": "自动渲染阈值",
        "render_timeout": "渲染超时",
        "font_decrypt": "字体解密",
        "font_mapping": "字形映射",
        "need_render": "需要渲染",
        "render_wait": "渲染等待",
        "anti_crawl_preset": "反爬预设",
        "web_auth_username": "Web认证用户名",
        "web_auth_password": "Web认证密码",
        "web_rate_limit": "Web限流(次/分钟)"
    }

    def __init__(self):
        self.config = DownloadConfig()
        self.observable_config = ObservableConfig(self.config)
        self.config_file = Path.home() / ".novel_spider_config.json"
        self.search_urls_file = Path.home() / ".novel_spider_search_urls.json"
        self.search_urls = self._load_search_urls()
        self._load_config()
        self._active_downloads: List[NovelDownloader] = []
        self._download_lock = asyncio.Lock()

    def _load_search_urls(self):
        if not self.search_urls_file.exists():
            default = [
                "https://www.biquge.com.cn/search?q={keyword}",
                "https://www.zwdu.com/search.php?keyword={keyword}",
                "https://www.xiaoshuo.com/search/?keyword={keyword}"
            ]
            self._save_search_urls(default)
            return default
        try:
            with open(self.search_urls_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []

    def _save_search_urls(self, urls):
        with open(self.search_urls_file, "w", encoding="utf-8") as f:
            json.dump(urls, f, ensure_ascii=False, indent=2)

    def _obj_to_dict(self, obj):
        if hasattr(obj, "__dict__"):
            result = {}
            for k, v in obj.__dict__.items():
                if k.startswith("_"):
                    continue
                if isinstance(v, Enum):
                    result[k] = v.value
                elif isinstance(v, tuple):
                    result[k] = list(v)
                elif isinstance(v, Path):
                    result[k] = str(v)
                elif hasattr(v, "__dict__"):
                    result[k] = self._obj_to_dict(v)
                else:
                    result[k] = v
            return result
        return obj

    def _dict_to_obj(self, data, target_obj):
        if not isinstance(data, dict):
            return
        for key, value in data.items():
            if not hasattr(target_obj, key):
                continue
            current = getattr(target_obj, key)
            if hasattr(current, "__dict__"):
                self._dict_to_obj(value, current)
            elif isinstance(current, Enum) and isinstance(value, str):
                try:
                    setattr(target_obj, key, type(current)(value))
                except Exception as e:
                    logger.warning(f"枚举转换失败: {key}={value}, {e}")
            elif isinstance(current, tuple) and isinstance(value, list):
                setattr(target_obj, key, tuple(value))
            elif isinstance(current, Path):
                setattr(target_obj, key, Path(value) if value else None)
            else:
                setattr(target_obj, key, value)

    def _save_config(self):
        errors = validate_config(self.config)
        if errors:
            print(f"⚠️ 配置校验失败，无法保存:")
            for err in errors:
                print(f"   - {err}")
            return False
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self._obj_to_dict(self.config), f, indent=2, ensure_ascii=False)
            print(f"✅ 配置已保存到 {self.config_file}")
            return True
        except OSError as e:
            print(f"⚠️ 保存配置失败: {e}")
            return False

    def _load_config(self):
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    self._dict_to_obj(json.load(f), self.config)
                print(f"✅ 已加载配置: {self.config_file}")
            except (json.JSONDecodeError, OSError) as e:
                print(f"⚠️ 加载配置失败: {e}")

    async def _add_history(self, novel: Novel, results: List[DownloadResult]):
        await HistoryManager.add_record(novel, results)

    def _show_history_sync(self):
        async def _show():
            history = await HistoryManager.get_history()
            if not history:
                print("\n📜 暂无下载历史记录。")
                return
            print("\n" + "=" * 60)
            print("📜 最近下载历史（最多5条）")
            print("=" * 60)
            for idx, record in enumerate(history, 1):
                dt = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record["timestamp"]))
                print(f"{idx}. 《{record['title']}》")
                print(f"   来源: {record['url']}")
                print(f"   最新章节: {record['last_chapter']}")
                print(f"   下载时间: {dt}")
                print(f"   进度: {record['downloaded']}/{record['total_chapters']} 章")
                print("-" * 40)
        run_async(_show())

    def _auto_check_subscriptions(self):
        sub_file = Path.home() / ".novel_spider_subscriptions.json"
        if not sub_file.exists():
            return
        print("\n📡 正在检查订阅更新...")
        mgr = SubscriptionManager(sub_file)
        subs = run_async(mgr.list_all())
        if not subs:
            return
        for sub in subs:
            url = sub["url"]
            name = sub["name"]
            print(f"检查《{name}》...")
            run_async(self._silent_update(url))
        print("✅ 订阅检查完成\n")

    async def _silent_update(self, url):
        async with NovelDownloader(self.config, GenericParser()) as dl:
            dl.bind_observable_config(self.observable_config)
            novel = await dl.fetch_catalog(url, check_update=True)
            if not novel:
                return
            state_file = _safe_path(novel.base_url, ".state", self.config.output_dir)
            downloaded_indices = set()
            if state_file.exists():
                try:
                    with open(state_file, "r", encoding="utf-8") as f:
                        state = json.load(f)
                        downloaded_indices = set(state.get("downloaded_indices", []))
                except (json.JSONDecodeError, OSError):
                    pass
            new_chapters = [ch for ch in novel.chapters if ch.index not in downloaded_indices]
            if not new_chapters:
                return
            print(f"发现《{novel.title}》有 {len(new_chapters)} 个新章节，正在下载...")
            start = min(ch.index for ch in new_chapters)
            end = max(ch.index for ch in new_chapters)
            results = await dl.download_chapters(start, end)
            storage = StorageFactory.create(self.config.output_format, self.config)
            output_path = Path(self.config.output_dir) / re.sub(r"[^\w\u4e00-\u9fff-]", "_", novel.title)
            if output_path.with_suffix(storage.get_extension()).exists():
                await storage.append(novel, results, output_path)
            else:
                await storage.save(novel, results, output_path, dl._images, dl._image_dir)
            await dl._save_state_async()
            print(f"《{novel.title}》更新完成，新增 {len(results)} 章")

    def _search_menu(self):
        while True:
            print("\n--- 小说搜索工具 ---")
            print("1. 开始搜索")
            print("2. 管理搜索网址（添加/删除/自动提取）")
            print("0. 返回上级")
            sub = input("请选择: ").strip()
            if sub == "1":
                run_async(self._execute_search())
            elif sub == "2":
                self._manage_search_urls()
            elif sub == "0":
                break
            else:
                print("❌ 无效输入")

    async def _execute_search(self):
        keyword = input("请输入搜索书名: ").strip()
        if not keyword:
            return
        if not self.search_urls:
            print("❌ 没有搜索网址，请先使用「管理搜索网址」添加。")
            return
        results = []
        for url_template in self.search_urls:
            search_url = url_template.replace("{keyword}", keyword)
            print(f"正在搜索: {search_url}")
            try:
                if HAS_AIOHTTP:
                    async with aiohttp.ClientSession() as sess:
                        async with sess.get(search_url, timeout=10) as resp:
                            if resp.status != 200:
                                continue
                            html = await resp.text()
                            soup = BeautifulSoup(html, "html.parser")
                            for a in soup.find_all("a", href=True):
                                title = a.get_text(strip=True)
                                if not title:
                                    continue
                                if keyword.lower() in title.lower():
                                    href = a["href"]
                                    full_url = urljoin(search_url, href)
                                    results.append({"title": title, "url": full_url})
                elif HAS_REQUESTS:
                    loop = asyncio.get_event_loop()
                    resp_text = await loop.run_in_executor(None, lambda: requests.get(search_url, timeout=10).text)
                    soup = BeautifulSoup(resp_text, "html.parser")
                    for a in soup.find_all("a", href=True):
                        title = a.get_text(strip=True)
                        if not title:
                            continue
                        if keyword.lower() in title.lower():
                            href = a["href"]
                            full_url = urljoin(search_url, href)
                            results.append({"title": title, "url": full_url})
            except Exception as e:
                print(f"搜索失败 {url_template}: {e}")
        if not results:
            print("未找到相关小说")
            return
        unique = {}
        for r in results:
            if r["url"] not in unique:
                unique[r["url"]] = r
        uniq_results = list(unique.values())
        for idx, r in enumerate(uniq_results[:50], 1):
            print(f"{idx}. {r['title']}\n   {r['url']}")
        choice = input("请输入序号开始下载 (直接回车跳过): ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(uniq_results):
            url = uniq_results[int(choice)-1]["url"]
            await self._new_download(url)

    def _manage_search_urls(self):
        while True:
            print("\n--- 管理搜索网址 ---")
            for i, url in enumerate(self.search_urls, 1):
                print(f"{i}. {url}")
            print("a. 添加网址")
            print("d. 删除网址")
            print("e. 从首页自动提取搜索模板")
            print("0. 返回")
            cmd = input("请选择: ").strip().lower()
            if cmd == "0":
                break
            elif cmd == "a":
                new_url = input("请输入搜索URL模板（使用{keyword}代替书名）:\n例如: https://www.example.com/search?q={keyword}\n").strip()
                if new_url and "{keyword}" in new_url:
                    self.search_urls.append(new_url)
                    self._save_search_urls(self.search_urls)
                    print("✅ 已添加")
                else:
                    print("❌ 无效模板，必须包含 {keyword}")
            elif cmd == "d":
                idx = input("请输入要删除的序号: ").strip()
                if idx.isdigit() and 1 <= int(idx) <= len(self.search_urls):
                    removed = self.search_urls.pop(int(idx)-1)
                    self._save_search_urls(self.search_urls)
                    print(f"已删除: {removed}")
                else:
                    print("❌ 序号无效")
            elif cmd == "e":
                homepage = input("请输入小说网站首页URL（例如 https://www.biquge.com.cn）:\n").strip()
                if not homepage.startswith(("http://", "https://")):
                    homepage = "https://" + homepage
                print("🔍 正在分析首页，请稍候...")
                template = run_async(self._extract_search_template_from_homepage(homepage))
                if template:
                    print(f"✅ 提取到搜索模板: {template}")
                    confirm = input("是否添加到搜索列表？(Y/n): ").strip().lower()
                    if confirm != "n":
                        self.search_urls.append(template)
                        self._save_search_urls(self.search_urls)
                        print("✅ 已添加")
                else:
                    print("❌ 自动提取失败，请手动添加搜索网址")
            else:
                print("❌ 无效输入")

    async def _extract_search_template_from_homepage(self, homepage_url: str):
        try:
            if HAS_AIOHTTP:
                async with aiohttp.ClientSession() as session:
                    async with session.get(homepage_url, timeout=10) as resp:
                        if resp.status != 200:
                            print(f"❌ 获取首页失败，状态码: {resp.status}")
                            return None
                        html = await resp.text()
            elif HAS_REQUESTS:
                loop = asyncio.get_event_loop()
                html = await loop.run_in_executor(None, lambda: requests.get(homepage_url, timeout=10).text)
            else:
                print("❌ 无可用HTTP库")
                return None
        except Exception as e:
            print(f"❌ 请求首页异常: {e}")
            return None

        soup = BeautifulSoup(html, "html.parser")
        forms = soup.find_all("form")
        for form in forms:
            action = form.get("action", "")
            method = form.get("method", "get").lower()
            text_inputs = form.find_all("input", {"type": ["text", "search"]})
            if not text_inputs:
                text_inputs = form.find_all("input", {"name": re.compile(r"(q|keyword|wd|searchword|key)", re.I)})
            if not text_inputs:
                continue
            param_name = text_inputs[0].get("name")
            if not param_name:
                continue
            base = urljoin(homepage_url, action)
            if method == "get":
                if "?" in base:
                    template = f"{base}&{param_name}={{keyword}}"
                else:
                    template = f"{base}?{param_name}={{keyword}}"
                return template
            else:
                print(f"⚠️ 该网站使用 POST 搜索，暂时无法自动提取，请手动添加 GET 类型的搜索网址。")
                return None
        print("❌ 未在首页找到搜索表单")
        return None

    def run(self):
        self._auto_check_subscriptions()
        while True:
            self.show_main_menu()

    def show_main_menu(self):
        print("\n" + "=" * 60)
        print("📚 智能小说爬虫-企业版（最终修复版）")
        print("=" * 60)
        print("1. 小说下载\n2. 设置\n3. 工具\n0. 退出")
        choice = input("请选择 (1-3 or 0): ").strip()
        actions = {"1": self.download_menu, "2": self.settings_menu, "3": self.tools_menu}
        if choice in actions:
            actions[choice]()
        elif choice == "0":
            self._save_config()
            print("👋 再见！")
            sys.exit(0)
        else:
            print("❌ 无效选择")

    def download_menu(self):
        while True:
            print("\n--- 小说下载 ---")
            url_input = input("📋 目录页 URL（多个用逗号分隔，或输入文件路径，直接回车返回）:\n").strip()
            if not url_input:
                break
            urls = []
            if os.path.isfile(url_input):
                try:
                    with open(url_input, "r", encoding="utf-8") as f:
                        urls = [l.strip() for l in f if l.strip()]
                    print(f"从文件读取到 {len(urls)} 个URL")
                except OSError as e:
                    print(f"❌ 读取文件失败: {e}")
                    continue
            else:
                urls = [u.strip() for u in url_input.split(",") if u.strip()]
            if not urls:
                print("❌ 未输入有效URL")
                continue
            invalid = [u for u in urls if not u.startswith(("http://", "https://"))]
            if invalid:
                print(f"❌ URL格式错误: {', '.join(invalid)}")
                continue
            if len(urls) == 1:
                url = urls[0]
                state_file = _safe_path(url, ".state", self.config.output_dir)
                cache_file = _safe_path(url, "_chapters.json", self.config.output_dir)
                if state_file.exists():
                    if input("检测到未完成的状态文件，是否续传？(y/N): ").strip().lower() == "y":
                        run_async(self._resume_download(state_file, url))
                        continue
                if cache_file.exists():
                    if input("检测到已下载的缓存，是否检查更新？(Y/n): ").strip().lower() != "n":
                        run_async(self._check_update(url))
                        continue
                run_async(self._new_download(url))
            else:
                print(f"将并发下载 {len(urls)} 本小说，最大并发数: {self.config.max_concurrent_novels}")
                run_async(self._download_multiple(urls))
                input("\n按回车返回...")

    async def _new_download(self, url: str):
        async with NovelDownloader(self.config, GenericParser()) as dl:
            dl.bind_observable_config(self.observable_config)
            async with self._download_lock:
                self._active_downloads.append(dl)
            try:
                novel = await dl.fetch_catalog(url)
                if not novel:
                    print("❌ 目录获取失败")
                    return
                print(f"\n📖 《{novel.title}》 共 {len(novel.chapters)} 章")
                await self._do_download(dl)
            finally:
                async with self._download_lock:
                    if dl in self._active_downloads:
                        self._active_downloads.remove(dl)

    async def _resume_download(self, state_file: Path, url: str):
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                state = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"❌ 加载状态失败: {e}")
            return
        print(f"📦 加载状态: 已下载 {len(state['downloaded_indices'])}/{len(state['all_chapters'])} 章")
        async with NovelDownloader(self.config, GenericParser()) as dl:
            dl.bind_observable_config(self.observable_config)
            async with self._download_lock:
                self._active_downloads.append(dl)
            try:
                dl.novel = Novel(state["novel_title"], state["base_url"], [Chapter.from_dict(ch) for ch in state["all_chapters"]])
                dl._downloaded_indices = set(state["downloaded_indices"])
                if self.config.deduplicate:
                    dl._content_hashes = set(state.get("chapter_hashes", []))
                if "failed_chapters" in state:
                    dl._failed_chapters = [Chapter.from_dict(ch) for ch in state["failed_chapters"]]
                await self._do_download(dl)
            finally:
                async with self._download_lock:
                    if dl in self._active_downloads:
                        self._active_downloads.remove(dl)

    async def _check_update(self, url: str):
        state_file = _safe_path(url, ".state", self.config.output_dir)
        downloaded_indices = set()
        if state_file.exists():
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    state = json.load(f)
                downloaded_indices = set(state.get("downloaded_indices", []))
            except (json.JSONDecodeError, OSError):
                pass
        async with NovelDownloader(self.config, GenericParser()) as dl:
            dl.bind_observable_config(self.observable_config)
            novel = await dl.fetch_catalog(url, check_update=True)
            if not novel:
                print("❌ 目录获取失败")
                return
            new_chapters = [ch for ch in novel.chapters if ch.index not in downloaded_indices]
            if not new_chapters:
                print("✅ 已是最新，无新章节")
                return
            print(f"发现 {len(new_chapters)} 个新章节，开始下载...")
            start = min(ch.index for ch in new_chapters)
            end = max(ch.index for ch in new_chapters)
            results = await dl.download_chapters(start, end)
            success = sum(1 for r in results if r.success)
            print(f"下载完成: 成功 {success}/{len(results)} 章")
            storage = StorageFactory.create(self.config.output_format, self.config)
            output_path = Path(self.config.output_dir) / re.sub(r"[^\w\u4e00-\u9fff-]", "_", novel.title)
            if output_path.with_suffix(storage.get_extension()).exists():
                await storage.append(novel, results, output_path)
            else:
                await storage.save(novel, results, output_path, dl._images, dl._image_dir)
            await dl._save_state_async()
            await self._add_history(novel, results)

    async def _do_download(self, dl: NovelDownloader):
        total = len(dl.novel.chapters)
        batch_mode = self.config.batch_mode.value if hasattr(self.config.batch_mode, 'value') else str(self.config.batch_mode)
        if batch_mode == "single" or (batch_mode == "auto" and total <= self.config.chapter_threshold):
            start, end = 1, total
        else:
            print("下载选项:\n 1. 全部\n 2. 前N章\n 3. 指定范围")
            choice = input("请选择 (1-3): ").strip()
            start, end = 1, total
            if choice == "2":
                n = input(f"前多少章 (1-{total}): ").strip()
                if n.isdigit() and 1 <= int(n) <= total:
                    end = int(n)
            elif choice == "3":
                s = input(f"起始章 (1-{total}): ").strip()
                e = input(f"结束章 ({s}-{total}): ").strip()
                if s.isdigit() and e.isdigit() and 1 <= int(s) <= int(e) <= total:
                    start, end = int(s), int(e)
        if start < 1: start = 1
        if end > total: end = total
        print(f"将下载第 {start} ~ {end} 章，共 {end-start+1} 章")
        print("提示: 下载过程中可以按 Ctrl+C 暂停，或在设置菜单中调整参数")
        
        async def progress_cb(current, total, chapter, status):
            if current % 10 == 0 or current == total:
                print(f"  进度: {current}/{total} ({current/total*100:.1f}%) - 第{chapter.index}章 {status}")
        dl.add_progress_callback(progress_cb)
        
        try:
            results = await dl.download_chapters(start, end)
        except KeyboardInterrupt:
            print("\n⏸️ 用户中断，正在保存状态...")
            await dl.pause()
            await dl._save_state_async()
            await dl._flush_pending()
            print("✅ 状态已保存，可以使用续传功能恢复")
            return
            
        success = sum(1 for r in results if r.success)
        print(f"\n✅ 下载完成: 成功 {success}/{len(results)} 章")
        
        storage = StorageFactory.create(self.config.output_format, self.config)
        output_path = Path(self.config.output_dir) / re.sub(r"[^\w\u4e00-\u9fff-]", "_", dl.novel.title)
        saved_path = await storage.save(dl.novel, results, output_path, dl._images, dl._image_dir)
        print(f"💾 已保存到: {saved_path}")
        
        failed = dl.get_failed_chapters()
        if failed:
            print(f"\n⚠️ 有 {len(failed)} 个章节下载失败")
            if input("是否立即重试失败章节？(y/N): ").strip().lower() == "y":
                retry_results = await dl.retry_failed()
                retry_success = sum(1 for r in retry_results if r.success)
                print(f"重试完成: 成功 {retry_success}/{len(retry_results)} 章")
                all_results = [r for r in results if r.success] + retry_results
                await storage.save(dl.novel, all_results, output_path, dl._images, dl._image_dir)
        
        await self._add_history(dl.novel, results)
        await dl._save_state_async()
        
        stats = dl.get_stats()
        print(f"\n📊 下载统计:")
        print(f"   总章节: {stats['total_chapters']}")
        print(f"   成功: {stats['downloaded']}")
        print(f"   失败: {stats['failed']}")
        print(f"   跳过: {stats['skipped']}")
        print(f"   总耗时: {stats['total_duration']:.1f}秒")
        
        if dl.network and hasattr(dl.network, 'get_request_stats'):
            req_stats = dl.network.get_request_stats()
            print(f"\n📡 请求统计:")
            print(f"   总请求: {req_stats['total']}")
            print(f"   成功率: {req_stats['success_rate']}")
            print(f"   平均耗时: {req_stats['avg_duration']}")
        
        input("\n按回车返回下载页面...")

    async def _download_multiple(self, urls: List[str]):
        sem = asyncio.Semaphore(self.config.max_concurrent_novels)
        async def dl_one(url):
            async with sem:
                async with NovelDownloader(self.config, GenericParser()) as dl:
                    dl.bind_observable_config(self.observable_config)
                    novel = await dl.fetch_catalog(url)
                    if not novel:
                        print(f"❌ 下载失败: {url}")
                        return
                    print(f"开始下载《{novel.title}》...")
                    results = await dl.download_chapters(1, len(novel.chapters))
                    success = sum(1 for r in results if r.success)
                    print(f"《{novel.title}》完成: {success}/{len(results)} 章")
                    storage = StorageFactory.create(self.config.output_format, self.config)
                    output_path = Path(self.config.output_dir) / re.sub(r"[^\w\u4e00-\u9fff-]", "_", novel.title)
                    saved_path = await storage.save(novel, results, output_path, dl._images, dl._image_dir)
                    print(f"💾 已保存到: {saved_path}")
                    await self._add_history(novel, results)
        await asyncio.gather(*(dl_one(url) for url in urls))

    # ========== 设置菜单（所有设置方法） ==========
    def settings_menu(self):
        menu = {"1":("基础设置", self._basic_settings), "2":("防爬设置", self._anti_crawl_settings),
                "3":("代理设置", self._proxy_settings), "4":("浏览器设置", self._browser_settings),
                "5":("输出设置", self._output_settings), "6":("高级设置", self._advanced_settings),
                "7":("批量下载设置", self._batch_settings), "8":("保存配置", self._save_config),
                "9":("应用配置到运行中任务", self._apply_to_running)}
        while True:
            print("\n" + "=" * 60 + "\n⚙️ 设置\n" + "=" * 60)
            for k, (name, _) in menu.items():
                print(f"{k}. {name}")
            print("0. 返回主菜单")
            choice = input("请选择: ").strip()
            if choice == "0":
                break
            elif choice in menu:
                menu[choice][1]()
            else:
                print("❌ 无效输入")

    def _basic_settings(self):
        while True:
            c = self.config
            device_display = c.device.value if hasattr(c.device, 'value') else c.device
            print(f"\n--- 基础设置 ---"
                  f"\n1. 并发线程数: {c.max_workers}"
                  f"\n2. 批量大小: {c.batch_size}"
                  f"\n3. 重试次数: {c.retry_times}"
                  f"\n4. 超时时间: {c.timeout} 秒"
                  f"\n5. 设备类型: {device_display}"
                  f"\n6. 强制编码: {c.force_encoding}"
                  f"\n7. SSL验证: {'启用' if c.verify_ssl else '禁用'}"
                  f"\n8. 最大并发小说数: {c.max_concurrent_novels}"
                  f"\n9. 允许的域名白名单 (逗号分隔): {','.join(c.allowed_domains) if c.allowed_domains else '无'}"
                  f"\n10. 日志目录: {c.log_dir}"
                  f"\n11. 详细日志: {'启用' if c.verbose else '禁用'}"
                  f"\n0. 返回上级")
            sub = input("请选择 (0-11): ").strip()
            if sub == "0": break
            elif sub == "1":
                v = input(f"并发线程数 (当前 {c.max_workers}): ").strip()
                if v.isdigit():
                    val = int(v)
                    if 1 <= val <= 50:
                        run_async(self.observable_config.set("max_workers", val))
                    else:
                        print("❌ 必须在 1-50 之间")
            elif sub == "2":
                v = input(f"批量大小 (当前 {c.batch_size}): ").strip()
                if v.isdigit():
                    val = int(v)
                    if 1 <= val <= 200:
                        run_async(self.observable_config.set("batch_size", val))
                    else:
                        print("❌ 必须在 1-200 之间")
            elif sub == "3":
                v = input(f"重试次数 (当前 {c.retry_times}): ").strip()
                if v.isdigit():
                    val = int(v)
                    if 0 <= val <= 10:
                        run_async(self.observable_config.set("retry_times", val))
                    else:
                        print("❌ 必须在 0-10 之间")
            elif sub == "4":
                v = input(f"超时秒数 (当前 {c.timeout}): ").strip()
                if v.isdigit():
                    val = int(v)
                    if 1 <= val <= 300:
                        run_async(self.observable_config.set("timeout", val))
                    else:
                        print("❌ 必须在 1-300 之间")
            elif sub == "5":
                print("1. PC  2. Android  3. iPhone")
                d = input("请选择 (1-3): ").strip()
                devs = {"1": DeviceType.PC, "2": DeviceType.ANDROID, "3": DeviceType.IPHONE}
                if d in devs:
                    run_async(self.observable_config.set("device", devs[d]))
            elif sub == "6":
                v = input("强制编码 (直接回车清除): ").strip()
                run_async(self.observable_config.set("force_encoding", v or None))
            elif sub == "7":
                run_async(self.observable_config.set("verify_ssl", not c.verify_ssl))
                print(f"SSL验证: {'启用' if not c.verify_ssl else '禁用'}")
            elif sub == "8":
                v = input(f"最大并发小说数 1-10 (当前 {c.max_concurrent_novels}): ").strip()
                if v.isdigit():
                    val = int(v)
                    if 1 <= val <= 10:
                        run_async(self.observable_config.set("max_concurrent_novels", val))
                    else:
                        print("❌ 必须在 1-10 之间")
            elif sub == "9":
                v = input("允许的域名（逗号分隔，留空清除）: ").strip()
                if v:
                    run_async(self.observable_config.set("allowed_domains", [d.strip() for d in v.split(",") if d.strip()]))
                else:
                    run_async(self.observable_config.set("allowed_domains", None))
            elif sub == "10":
                v = input(f"日志目录 (当前 {c.log_dir}): ").strip()
                if v:
                    run_async(self.observable_config.set("log_dir", v))
            elif sub == "11":
                run_async(self.observable_config.set("verbose", not c.verbose))
                print(f"详细日志: {'启用' if not c.verbose else '禁用'}")
            else:
                print("❌ 无效输入")

    def _anti_crawl_settings(self):
        while True:
            c = self.config
            ref_display = c.referer_policy.value if hasattr(c.referer_policy, 'value') else c.referer_policy
            print(f"\n--- 防爬设置 ---"
                  f"\n1. 随机延迟范围: {c.random_delay_range}"
                  f"\n2. 限速设置: {c.rate_limit}"
                  f"\n3. TLS指纹模拟: {c.impersonate}"
                  f"\n4. Referer策略: {ref_display}"
                  f"\n5. 自动降级: {'是' if c.auto_downgrade else '否'}"
                  f"\n6. 降级后暂停秒数: {c.downgrade_sleep}"
                  f"\n7. 添加随机参数: {'是' if c.add_random_param else '否'}"
                  f"\n8. Cookie文件: {c.cookie_file}"
                  f"\n9. 使用TLS指纹模拟: {'是' if c.use_curl_cffi else '否'}"
                  f"\n10. 启用反反爬: {'是' if c.anti_crawl_enabled else '否'}"
                  f"\n11. 反爬升级阈值: {c.anti_crawl_failure_threshold}"
                  f"\n12. 自动升级反爬: {'是' if c.anti_crawl_escalation else '否'}"
                  f"\n13. 反爬降级阈值: {c.anti_crawl_downgrade_after}"
                  f"\n0. 返回上级")
            sub = input("请选择 (0-13): ").strip()
            if sub == "0": break
            elif sub == "1":
                v = input(f"最小延迟,最大延迟 (当前 {c.random_delay_range[0]},{c.random_delay_range[1]}): ").strip()
                try:
                    parts = v.split(",")
                    if len(parts) == 2:
                        lo, hi = float(parts[0]), float(parts[1])
                        if lo >= 0 and hi >= lo:
                            run_async(self.observable_config.set("random_delay_range", (lo, hi)))
                        else:
                            print("❌ 延迟范围无效")
                except:
                    print("格式错误")
            elif sub == "2":
                print("1. 不限速\n2. 按请求数 (如100req/min)")
                opt = input("请选择: ").strip()
                if opt == "1":
                    run_async(self.observable_config.set("rate_limit", False))
                elif opt == "2":
                    v = input("每分钟请求数: ").strip()
                    if v.isdigit() and int(v) > 0:
                        run_async(self.observable_config.set("rate_limit", f"{v}req/min"))
            elif sub == "3":
                print("支持: chrome120, chrome121, edge120, firefox121, safari17")
                v = input(f"指纹 (当前 {c.impersonate}): ").strip()
                if v:
                    run_async(self.observable_config.set("impersonate", v))
            elif sub == "4":
                print("1. auto(上一页)  2. home(首页)  3. none(无)")
                opt = input("请选择: ").strip()
                policies = {"1": RefererPolicy.AUTO, "2": RefererPolicy.HOME, "3": RefererPolicy.NONE}
                if opt in policies:
                    run_async(self.observable_config.set("referer_policy", policies[opt]))
            elif sub == "5":
                run_async(self.observable_config.set("auto_downgrade", not c.auto_downgrade))
            elif sub == "6":
                v = input(f"降级后暂停秒数 (当前 {c.downgrade_sleep}): ").strip()
                if v.isdigit() and int(v) >= 0:
                    run_async(self.observable_config.set("downgrade_sleep", int(v)))
            elif sub == "7":
                run_async(self.observable_config.set("add_random_param", not c.add_random_param))
            elif sub == "8":
                v = input(f"Cookie文件路径 (当前 {c.cookie_file}，回车清除): ").strip()
                if v and os.path.exists(v):
                    run_async(self.observable_config.set("cookie_file", v))
                elif not v:
                    run_async(self.observable_config.set("cookie_file", None))
                else:
                    print("❌ 文件不存在")
            elif sub == "9":
                run_async(self.observable_config.set("use_curl_cffi", not c.use_curl_cffi))
            elif sub == "10":
                run_async(self.observable_config.set("anti_crawl_enabled", not c.anti_crawl_enabled))
            elif sub == "11":
                v = input(f"反爬升级阈值 (当前 {c.anti_crawl_failure_threshold}): ").strip()
                if v.isdigit():
                    val = int(v)
                    if 1 <= val <= 10:
                        run_async(self.observable_config.set("anti_crawl_failure_threshold", val))
                    else:
                        print("❌ 必须在 1-10 之间")
            elif sub == "12":
                run_async(self.observable_config.set("anti_crawl_escalation", not c.anti_crawl_escalation))
            elif sub == "13":
                v = input(f"反爬降级阈值 (连续成功次数，当前 {c.anti_crawl_downgrade_after}): ").strip()
                if v.isdigit() and int(v) >= 1:
                    run_async(self.observable_config.set("anti_crawl_downgrade_after", int(v)))
            else:
                print("❌ 无效输入")

    def _proxy_settings(self):
        proxy = self.config.proxy_config
        while True:
            strat_display = proxy.strategy.value if hasattr(proxy.strategy, 'value') else proxy.strategy
            print(f"\n--- 代理设置 ---"
                  f"\n1. 单个代理: {proxy.single}"
                  f"\n2. 代理列表/文件: {proxy.rotation}"
                  f"\n3. 检测URL: {proxy.check_url}"
                  f"\n4. 最大失败次数: {proxy.max_failures}"
                  f"\n5. 轮换策略: {strat_display}"
                  f"\n6. 健康检查间隔(秒): {getattr(proxy, 'health_check_interval', 300)}"
                  f"\n7. 代理池API: {getattr(proxy, 'proxy_pool_api', '无')}"
                  f"\n0. 返回上级")
            sub = input("请选择 (0-7): ").strip()
            if sub == "0": break
            elif sub == "1":
                v = input("单个代理 (回车清除): ").strip()
                run_async(self.observable_config.set("proxy_config.single", v or None))
            elif sub == "2":
                v = input("代理列表文件路径或逗号分隔列表 (回车清除): ").strip()
                if not v:
                    run_async(self.observable_config.set("proxy_config.rotation", None))
                elif os.path.exists(v):
                    run_async(self.observable_config.set("proxy_config.rotation", v))
                else:
                    run_async(self.observable_config.set("proxy_config.rotation", [x.strip() for x in v.split(",") if x.strip()]))
            elif sub == "3":
                v = input(f"检测URL (当前 {proxy.check_url}): ").strip()
                if v:
                    run_async(self.observable_config.set("proxy_config.check_url", v))
            elif sub == "4":
                v = input(f"最大失败次数 (当前 {proxy.max_failures}): ").strip()
                if v.isdigit() and int(v) > 0:
                    run_async(self.observable_config.set("proxy_config.max_failures", int(v)))
            elif sub == "5":
                print("1. 轮询  2. 随机  3. 最少使用")
                opt = input("请选择: ").strip()
                strategies = {"1": ProxyStrategy.ROUND_ROBIN, "2": ProxyStrategy.RANDOM, "3": ProxyStrategy.LEAST_USED}
                if opt in strategies:
                    run_async(self.observable_config.set("proxy_config.strategy", strategies[opt]))
            elif sub == "6":
                v = input(f"健康检查间隔(秒) (当前 {getattr(proxy,'health_check_interval',300)}): ").strip()
                if v.isdigit() and int(v) >= 60:
                    run_async(self.observable_config.set("proxy_config.health_check_interval", int(v)))
            elif sub == "7":
                v = input("代理池API地址 (回车清除): ").strip()
                run_async(self.observable_config.set("proxy_config.proxy_pool_api", v or None))
                if v:
                    key = input("API密钥 (可选): ").strip()
                    run_async(self.observable_config.set("proxy_config.proxy_pool_key", key or None))
            else:
                print("❌ 无效输入")

    def _browser_settings(self):
        browser = self.config.browser_config
        while True:
            print(f"\n--- 浏览器设置 ---\n1. 启用无头浏览器: {'是' if browser.enable else '否'}")
            if browser.enable:
                print(f"2. 无头模式: {'是' if browser.headless else '否'}"
                      f"\n3. 隐身模式: {'是' if browser.stealth_mode else '否'}"
                      f"\n4. 视口宽度: {browser.viewport_width}"
                      f"\n5. 视口高度: {browser.viewport_height}"
                      f"\n6. 区域设置: {browser.locale}"
                      f"\n7. 时区: {browser.timezone}"
                      f"\n8. 等待选择器: {getattr(browser,'wait_for_selector',None)}"
                      f"\n9. 自动渲染阈值: {getattr(browser,'auto_render_threshold',50)} 字符"
                      f"\n10. 渲染超时: {getattr(browser,'render_timeout',30)} 秒")
            print("0. 返回上级")
            sub = input("请选择: ").strip()
            if sub == "0": break
            elif sub == "1":
                run_async(self.observable_config.set("browser_config.enable", not browser.enable))
            elif sub == "2" and browser.enable:
                run_async(self.observable_config.set("browser_config.headless", not browser.headless))
            elif sub == "3" and browser.enable:
                run_async(self.observable_config.set("browser_config.stealth_mode", not browser.stealth_mode))
            elif sub == "4" and browser.enable:
                v = input(f"视口宽度 (当前 {browser.viewport_width}): ").strip()
                if v.isdigit() and int(v) > 0:
                    run_async(self.observable_config.set("browser_config.viewport_width", int(v)))
            elif sub == "5" and browser.enable:
                v = input(f"视口高度 (当前 {browser.viewport_height}): ").strip()
                if v.isdigit() and int(v) > 0:
                    run_async(self.observable_config.set("browser_config.viewport_height", int(v)))
            elif sub == "6" and browser.enable:
                v = input(f"区域设置 (当前 {browser.locale}): ").strip()
                if v:
                    run_async(self.observable_config.set("browser_config.locale", v))
            elif sub == "7" and browser.enable:
                v = input(f"时区 (当前 {browser.timezone}): ").strip()
                if v:
                    run_async(self.observable_config.set("browser_config.timezone", v))
            elif sub == "8" and browser.enable:
                v = input("等待选择器 (回车清除): ").strip()
                run_async(self.observable_config.set("browser_config.wait_for_selector", v or None))
            elif sub == "9" and browser.enable:
                v = input(f"自动渲染阈值 (当前 {getattr(browser,'auto_render_threshold',50)}): ").strip()
                if v.isdigit() and int(v) > 0:
                    run_async(self.observable_config.set("browser_config.auto_render_threshold", int(v)))
            elif sub == "10" and browser.enable:
                v = input(f"渲染超时 (当前 {getattr(browser,'render_timeout',30)}): ").strip()
                if v.isdigit() and int(v) > 0:
                    run_async(self.observable_config.set("browser_config.render_timeout", int(v)))
            else:
                print("❌ 无效输入")

    def _output_settings(self):
        while True:
            c = self.config
            fmt_display = c.output_format.value if hasattr(c.output_format, 'value') else c.output_format
            print(f"\n--- 输出设置 ---"
                  f"\n1. 输出格式: {fmt_display}"
                  f"\n2. 输出目录: {c.output_dir}"
                  f"\n3. 下载图片: {'是' if c.download_images else '否'}"
                  f"\n4. 内容去重: {'是' if c.deduplicate else '否'}"
                  f"\n5. 章节过滤正则: {c.chapter_filter}"
                  f"\n6. 目录缓存: {'启用' if c.cache_chapters else '禁用'}"
                  f"\n7. 错误报告: {'启用' if c.error_report else '禁用'}"
                  f"\n8. 自定义选择器: {c.chapter_selector}"
                  f"\n9. PDF中文字体路径: {c.pdf_font_path}"
                  f"\n10. 保存间隔: {c.save_interval} 章"
                  f"\n0. 返回上级")
            sub = input("请选择 (0-10): ").strip()
            if sub == "0": break
            elif sub == "1":
                print("1. TXT  2. JSON  3. EPUB  4. PDF")
                opt = input("请选择: ").strip()
                fmts = {"1": OutputFormat.TXT, "2": OutputFormat.JSON,
                        "3": OutputFormat.EPUB if HAS_EPUB else None, "4": OutputFormat.PDF}
                if opt in fmts:
                    if fmts[opt] is None:
                        print("未安装 ebooklib")
                    else:
                        run_async(self.observable_config.set("output_format", fmts[opt]))
            elif sub == "2":
                v = input(f"输出目录 (当前 {c.output_dir}): ").strip()
                if v:
                    run_async(self.observable_config.set("output_dir", v))
            elif sub == "3":
                run_async(self.observable_config.set("download_images", not c.download_images))
            elif sub == "4":
                run_async(self.observable_config.set("deduplicate", not c.deduplicate))
            elif sub == "5":
                v = input("正则表达式 (回车清除): ").strip()
                if v:
                    try:
                        re.compile(v)
                        run_async(self.observable_config.set("chapter_filter", v))
                    except re.error as e:
                        print(f"❌ 无效的正则表达式: {e}")
                else:
                    run_async(self.observable_config.set("chapter_filter", None))
            elif sub == "6":
                run_async(self.observable_config.set("cache_chapters", not c.cache_chapters))
            elif sub == "7":
                run_async(self.observable_config.set("error_report", not c.error_report))
            elif sub == "8":
                v = input("自定义CSS选择器 (回车清除): ").strip()
                run_async(self.observable_config.set("chapter_selector", v or None))
            elif sub == "9":
                v = input("PDF中文字体文件路径 (.ttf): ").strip()
                if v and os.path.exists(v):
                    run_async(self.observable_config.set("pdf_font_path", v))
                else:
                    print("文件不存在，已忽略")
            elif sub == "10":
                v = input(f"保存间隔 (当前 {c.save_interval}): ").strip()
                if v.isdigit():
                    val = int(v)
                    if 1 <= val <= 100:
                        run_async(self.observable_config.set("save_interval", val))
                    else:
                        print("❌ 必须在 1-100 之间")
            else:
                print("❌ 无效输入")

    def _advanced_settings(self):
        while True:
            c = self.config
            mode_display = c.catalog_mode.value if hasattr(c.catalog_mode, 'value') else c.catalog_mode
            print(f"\n--- 高级设置 ---"
                  f"\n1. 目录模式: {mode_display}"
                  f"\n2. 自动追更: {'开启' if c.auto_update else '关闭'}"
                  f"\n3. 追更间隔(小时): {c.update_interval_hours}"
                  f"\n4. 下载完成通知: {'开启' if c.enable_notification else '关闭'}"
                  f"\n5. 整体下载超时(秒，0不限): {c.download_timeout}"
                  f"\n6. 站点规则文件: {c.custom_rules_file or '无'}"
                  f"\n7. Web认证用户名: {c.web_auth_username or '未设置'}"
                  f"\n8. Web认证密码: {'已设置' if c.web_auth_password else '未设置'}"
                  f"\n9. Web限流(次/分钟): {c.web_rate_limit}"
                  f"\n0. 返回上级")
            sub = input("请选择 (0-9): ").strip()
            if sub == "0": break
            elif sub == "1":
                print("1. 自动  2. 单页  3. 多页")
                opt = input("请选择: ").strip()
                modes = {"1": CatalogMode.AUTO, "2": CatalogMode.SINGLE, "3": CatalogMode.MULTI}
                if opt in modes:
                    run_async(self.observable_config.set("catalog_mode", modes[opt]))
            elif sub == "2":
                run_async(self.observable_config.set("auto_update", not c.auto_update))
            elif sub == "3":
                v = input(f"追更间隔（小时） (当前 {c.update_interval_hours}): ").strip()
                if v.isdigit() and int(v) > 0:
                    run_async(self.observable_config.set("update_interval_hours", int(v)))
            elif sub == "4":
                run_async(self.observable_config.set("enable_notification", not c.enable_notification))
            elif sub == "5":
                v = input(f"整体下载超时（秒） (当前 {c.download_timeout}): ").strip()
                if v.isdigit() and int(v) >= 0:
                    run_async(self.observable_config.set("download_timeout", int(v)))
            elif sub == "6":
                v = input("站点规则文件路径 (JSON格式，回车清除): ").strip()
                if v and os.path.exists(v):
                    run_async(self.observable_config.set("custom_rules_file", v))
                elif not v:
                    run_async(self.observable_config.set("custom_rules_file", None))
                else:
                    print("❌ 文件不存在")
            elif sub == "7":
                v = input("Web认证用户名 (回车清除): ").strip()
                run_async(self.observable_config.set("web_auth_username", v or None))
            elif sub == "8":
                v = input("Web认证密码 (回车清除): ").strip()
                run_async(self.observable_config.set("web_auth_password", v or None))
            elif sub == "9":
                v = input(f"Web限流(次/分钟) (当前 {c.web_rate_limit}): ").strip()
                if v.isdigit() and int(v) >= 5:
                    run_async(self.observable_config.set("web_rate_limit", int(v)))
            else:
                print("❌ 无效输入")

    def _batch_settings(self):
        while True:
            c = self.config
            mode_display = c.batch_mode.value if hasattr(c.batch_mode, 'value') else c.batch_mode
            print(f"\n--- 批量下载设置 ---"
                  f"\n1. 批量模式: {mode_display}"
                  f"\n2. 章节数阈值 (用于自动模式): {c.chapter_threshold}"
                  f"\n0. 返回上级")
            sub = input("请选择 (0-2): ").strip()
            if sub == "0": break
            elif sub == "1":
                print("1. 自动 (超过阈值询问分批)  2. 单次 (全部下载)  3. 批次 (手动选择范围)")
                opt = input("请选择 (1-3): ").strip()
                modes = {"1": BatchMode.AUTO, "2": BatchMode.SINGLE, "3": BatchMode.BATCH}
                if opt in modes:
                    run_async(self.observable_config.set("batch_mode", modes[opt]))
            elif sub == "2":
                v = input(f"章节数阈值 (当前 {c.chapter_threshold}): ").strip()
                if v.isdigit() and int(v) > 0:
                    run_async(self.observable_config.set("chapter_threshold", int(v)))
            else:
                print("❌ 无效输入")

    def _apply_to_running(self):
        async def _apply():
            async with self._download_lock:
                count = len(self._active_downloads)
                if count == 0:
                    print("\n当前没有运行中的下载任务")
                    return
                print(f"\n🔄 正在将新配置应用到 {count} 个运行中的任务...")
                print(f"✅ 配置已应用到所有运行中任务")
        run_async(_apply())

    # ========== 工具菜单 ==========
    def tools_menu(self):
        while True:
            print("\n" + "=" * 60 + "\n🛠️ 工具\n" + "=" * 60)
            print("1. 测试代理\n2. 查看当前配置\n3. 清理缓存文件\n4. 查看下载历史\n5. 清空下载历史\n6. 启动图形界面\n7. 小说搜索\n8. 订阅管理\n9. 启动Web服务器\n10. 查看运行中任务\n11. 暂停/恢复下载\n12. 查看请求统计\n0. 返回主菜单")
            choice = input("请选择 (0-12): ").strip()
            if choice == "0":
                break
            elif choice == "1":
                self._test_proxy()
            elif choice == "2":
                self._show_config()
            elif choice == "3":
                self._clean_cache()
            elif choice == "4":
                self._show_history_sync()
            elif choice == "5":
                run_async(HistoryManager.clear_history())
                print("✅ 历史记录已清空")
            elif choice == "6":
                start_gui(self.observable_config)
            elif choice == "7":
                self._search_menu()
            elif choice == "8":
                run_async(self._subscription_menu())
            elif choice == "9":
                self._start_web_server()
            elif choice == "10":
                self._show_running_tasks()
            elif choice == "11":
                self._pause_resume_menu()
            elif choice == "12":
                self._show_request_stats()
            else:
                print("❌ 无效输入")

    def _show_running_tasks(self):
        async def _show():
            async with self._download_lock:
                count = len(self._active_downloads)
                if count == 0:
                    print("\n当前没有运行中的下载任务")
                    return
                print(f"\n📊 运行中的任务 ({count} 个):")
                for i, dl in enumerate(self._active_downloads, 1):
                    stats = dl.get_stats()
                    status = "⏸️ 暂停中" if dl.is_paused else "▶️ 运行中"
                    print(f"  {i}. {status} 《{dl.novel.title if dl.novel else '未知'}》")
                    print(f"     进度: {stats['downloaded']}/{stats['total_chapters']} 章")
                    print(f"     失败: {stats['failed']} 章")
                    if stats['started_at']:
                        elapsed = time.time() - stats['started_at']
                        print(f"     已运行: {elapsed:.1f} 秒")
        run_async(_show())

    def _pause_resume_menu(self):
        async def _control():
            async with self._download_lock:
                if not self._active_downloads:
                    print("\n当前没有运行中的下载任务")
                    return
                print("\n--- 暂停/恢复控制 ---")
                for i, dl in enumerate(self._active_downloads, 1):
                    status = "⏸️ 暂停中" if dl.is_paused else "▶️ 运行中"
                    print(f"{i}. {status} 《{dl.novel.title if dl.novel else '未知'}》")
                print("a. 全部暂停\nr. 全部恢复\nc. 全部取消")
                cmd = input("请选择: ").strip().lower()
                if cmd == "a":
                    for dl in self._active_downloads:
                        await dl.pause()
                    print("✅ 所有任务已暂停")
                elif cmd == "r":
                    for dl in self._active_downloads:
                        await dl.resume()
                    print("✅ 所有任务已恢复")
                elif cmd == "c":
                    for dl in self._active_downloads:
                        await dl.cancel()
                    print("✅ 所有任务已取消")
                elif cmd.isdigit():
                    idx = int(cmd) - 1
                    if 0 <= idx < len(self._active_downloads):
                        dl = self._active_downloads[idx]
                        if dl.is_paused:
                            await dl.resume()
                            print(f"✅ 任务 {idx+1} 已恢复")
                        else:
                            await dl.pause()
                            print(f"✅ 任务 {idx+1} 已暂停")
        run_async(_control())

    def _show_request_stats(self):
        print("\n📡 请求统计功能需要在下载任务运行时查看")
        print("下载过程中，请求日志会自动写入 logs/requests_*.log 文件")

    def _test_proxy(self):
        proxy_cfg = self.config.proxy_config
        if not proxy_cfg.single and not proxy_cfg.rotation and not proxy_cfg.proxy_pool_api:
            print("❌ 未配置代理")
            return
        async def do_test():
            mgr = ProxyManager(proxy_cfg)
            try:
                proxy = await mgr.get_proxy()
                if not proxy:
                    print("❌ 无可用代理")
                    return
                test_url = proxy_cfg.check_url
                print(f"正在测试代理 {proxy.get('http')} ...")
                if HAS_AIOHTTP:
                    async with aiohttp.ClientSession() as s:
                        async with s.get(test_url, proxy=proxy.get("http"), timeout=aiohttp.ClientTimeout(total=10)) as resp:
                            if resp.status == 200:
                                try:
                                    data = await resp.json()
                                    print(f"✅ 代理可用: {data}")
                                except:
                                    text = await resp.text()
                                    print(f"✅ 代理可用: {text[:200]}")
                            else:
                                print(f"❌ 代理返回状态码: {resp.status}")
                elif HAS_REQUESTS:
                    resp = requests.get(test_url, proxies=proxy, timeout=10)
                    if resp.status_code == 200:
                        print(f"✅ 代理可用: {resp.text[:200]}")
                    else:
                        print(f"❌ 代理返回状态码: {resp.status_code}")
                stats = mgr.get_stats()
                print(f"\n📊 代理统计:")
                print(f"   总数: {stats['total']}")
                print(f"   有效: {stats['valid']}")
                print(f"   无效: {stats['invalid']}")
            except Exception as e:
                print(f"❌ 代理测试失败: {e}")
            finally:
                await mgr.close()
        run_async(do_test())

    def _show_config(self):
        print("\n当前配置：")
        self._print_config(self.config)

    def _print_config(self, obj, indent=0):
        prefix = "  " * indent
        if hasattr(obj, "__dict__"):
            for key, value in obj.__dict__.items():
                if key.startswith("_"):
                    continue
                cn_key = self.CONFIG_NAMES_CN.get(key, key)
                if hasattr(value, "__dict__"):
                    print(f"{prefix}{cn_key}:")
                    self._print_config(value, indent+1)
                else:
                    if hasattr(value, "value"):
                        value = value.value
                    elif isinstance(value, bool):
                        value = "是" if value else "否"
                    elif isinstance(value, list):
                        value = ", ".join(map(str, value))
                    print(f"{prefix}{cn_key}: {value}")
        elif isinstance(obj, dict):
            for k, v in obj.items():
                cn_k = self.CONFIG_NAMES_CN.get(k, k)
                if isinstance(v, (dict, list)):
                    print(f"{prefix}{cn_k}:")
                    self._print_config(v, indent+1)
                else:
                    print(f"{prefix}{cn_k}: {v}")
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                print(f"{prefix}[{i}]:")
                self._print_config(item, indent+1)
        else:
            print(f"{prefix}{obj}")

    def _clean_cache(self):
        output_dir = Path(self.config.output_dir)
        if not output_dir.exists():
            print("输出目录不存在")
            return
        count = 0
        for f in list(output_dir.glob("*_chapters.json")) + list(output_dir.glob("*.state")) + list(output_dir.glob("*.tmp")) + list(output_dir.glob("*_epub_tmp")) + list(output_dir.glob("*_pdf_tmp")):
            try:
                if f.is_dir():
                    import shutil
                    shutil.rmtree(f)
                else:
                    f.unlink()
                count += 1
            except OSError:
                pass
        print(f"已清理 {count} 个缓存文件/目录")

    async def _subscription_menu(self):
        sub_file = Path.home() / ".novel_spider_subscriptions.json"
        mgr = SubscriptionManager(sub_file)
        while True:
            subs = await mgr.list_all()
            print("\n--- 订阅列表 ---")
            for i, sub in enumerate(subs):
                print(f"{i}. {sub['name']} - {sub['url']}")
            print("a. 添加订阅   d. 删除订阅   u. 检查更新   q. 返回")
            cmd = input("请选择: ").strip().lower()
            if cmd == "q":
                break
            elif cmd == "a":
                url = input("目录页URL: ").strip()
                name = input("别名 (可选): ").strip()
                await mgr.add(url, name or None)
                print("已添加")
            elif cmd == "d":
                idx = int(input("删除序号: ").strip())
                await mgr.remove(idx)
            elif cmd == "u":
                for i, sub in enumerate(subs):
                    print(f"检查 {sub['name']} ...")
                    async with NovelDownloader(self.config, GenericParser()) as dl:
                        dl.bind_observable_config(self.observable_config)
                        await dl.fetch_catalog(sub["url"], check_update=True)
                        if dl.novel and len(dl.novel.chapters) > 0:
                            print(f"《{sub['name']}》 共 {len(dl.novel.chapters)} 章")
                    await mgr.update_last_check(i)

    def _start_web_server(self, port=8000):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex(("127.0.0.1", port))
        sock.close()
        if result == 0:
            print(f"\n⚠️ 端口 {port} 已被占用，Web 服务器可能已在运行")
            print(f"📍 请访问 http://127.0.0.1:{port} 使用")
            return
        print(f"\n🌐 正在启动 Web 服务器，端口 {port}...")
        allowed = self.config.allowed_domains
        web_username = self.config.web_auth_username
        web_password = self.config.web_auth_password
        rate_limit = self.config.web_rate_limit
        def run_server():
            from web.server import start_web_server as _start_web
            _start_web(port=port, allowed_domains=allowed, ui=self,
                       username=web_username, password=web_password, rate_limit=rate_limit)
        t = threading.Thread(target=run_server, daemon=True)
        t.start()
        print(f"✅ Web 服务器已在后台运行")
        print(f"📱 请在手机浏览器中打开: http://127.0.0.1:{port}")
        if web_username:
            print(f"🔒 认证: 用户名 {web_username}")
        print(f"🔧 按回车返回工具菜单，服务器将继续运行\n")
        input("按回车键返回...")