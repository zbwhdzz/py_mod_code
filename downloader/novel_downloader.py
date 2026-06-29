from __future__ import annotations
import asyncio
import time
import json
import hashlib
import re
import random
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any, Callable, Set
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
from config.models import DownloadConfig
from config.observable import ObservableConfig
from config.enums import DownloadStatus, OutputFormat, CatalogMode, BatchMode
from models.novel import Novel
from models.chapter import Chapter
from models.result import DownloadResult
from models.history import HistoryManager
from network.network_manager import NetworkManager
from parser.content_parser import ContentParser, GenericParser
from storage.storage_backend import StorageFactory, StorageBackend
from utils.helpers import _safe_path

logger = logging.getLogger(__name__)

class NovelDownloader:
    def __init__(self, config: DownloadConfig, parser: ContentParser):
        self.config = config
        self.observable_config: Optional[ObservableConfig] = None
        self.parser = parser
        self.network: Optional[NetworkManager] = None
        self.novel: Optional[Novel] = None
        self._downloaded_indices: Set[int] = set()
        self._content_hashes: Set[str] = set()
        self._images: Dict[str, bytes] = {}
        self._image_dir: Optional[Path] = None
        self._state_save_lock = asyncio.Lock()
        self._save_counter = 0
        self._pending_results: List[DownloadResult] = []
        self._output_path: Optional[Path] = None
        self._storage: Optional[StorageBackend] = None
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._cancelled = False
        self._failed_chapters: List[Chapter] = []
        self._stats = {
            'started_at': None,
            'completed_at': None,
            'total_chapters': 0,
            'downloaded': 0,
            'failed': 0,
            'skipped': 0,
            'total_duration': 0.0
        }
        self._progress_callbacks: List[Callable] = []
        self._save_counter_lock = asyncio.Lock()
        self._temp_chapter_dir: Optional[Path] = None
        self._append_lock = asyncio.Lock()

    def bind_observable_config(self, obs_config: ObservableConfig):
        self.observable_config = obs_config
        obs_config.add_listener(self._on_config_change)
        
    def _on_config_change(self, key: str, old_val: Any, new_val: Any):
        logger.info(f"[NovelDownloader] 配置变更: {key} = {old_val} -> {new_val}")
            
    def add_progress_callback(self, callback: Callable):
        self._progress_callbacks.append(callback)
        
    def remove_progress_callback(self, callback: Callable):
        if callback in self._progress_callbacks:
            self._progress_callbacks.remove(callback)
            
    async def _notify_progress(self, current: int, total: int, chapter: Chapter = None, status: str = ""):
        for cb in self._progress_callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    asyncio.create_task(cb(current, total, chapter, status))
                else:
                    cb(current, total, chapter, status)
            except Exception as e:
                logger.debug(f"进度回调错误: {e}")

    async def __aenter__(self):
        self.network = NetworkManager(self.config)
        if self.observable_config:
            self.network.bind_observable_config(self.observable_config)
        await self.network.__aenter__()
        if self.config.download_images:
            self._image_dir = Path(self.config.output_dir) / "images"
            self._image_dir.mkdir(parents=True, exist_ok=True)
        fmt = self.config.output_format.value if hasattr(self.config.output_format, 'value') else str(self.config.output_format)
        if fmt in ("epub", "pdf"):
            self._temp_chapter_dir = Path(self.config.output_dir) / f"temp_{int(time.time())}"
            self._temp_chapter_dir.mkdir(parents=True, exist_ok=True)
        return self

    async def __aexit__(self, *args):
        await self._flush_pending()
        if self.network:
            await self.network.__aexit__(*args)
        if self._temp_chapter_dir and self._temp_chapter_dir.exists():
            for f in self._temp_chapter_dir.glob("*"):
                try:
                    f.unlink()
                except:
                    pass
            try:
                self._temp_chapter_dir.rmdir()
            except:
                pass

    async def fetch_catalog(self, catalog_url: str, check_update: bool = False) -> Optional[Novel]:
        logger.info(f"正在解析目录: {catalog_url}")
        cache_file = _safe_path(catalog_url, "_chapters.json", self.config.output_dir)
        if self.config.cache_chapters and not check_update and cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.novel = Novel.from_dict(data)
                logger.info(f"从缓存加载: {len(self.novel.chapters)} 章")
                return self.novel
            except (json.JSONDecodeError, OSError) as e:
                logger.debug(f"缓存加载失败: {e}")

        html_content = await self.network.fetch(catalog_url, referer=None, use_browser=self.config.browser_config.enable)
        if not html_content:
            logger.error("目录页获取失败")
            return None

        soup = BeautifulSoup(html_content, "html.parser")
        title = self._extract_title(soup, catalog_url)
        rule = self.network.site_rule_manager.get_rule(catalog_url)
        chapters = self.parser.parse_chapters(html_content, catalog_url, rule=rule)
        if not chapters:
            logger.error("未找到任何章节")
            return None

        metadata = await self._extract_metadata(soup, catalog_url)
        catalog_mode = self.config.catalog_mode.value if hasattr(self.config.catalog_mode, 'value') else str(self.config.catalog_mode)
        if len(chapters) < self.config.chapter_threshold and catalog_mode != "single":
            paginated = await self._try_fetch_paginated_catalog(catalog_url, chapters)
            if paginated:
                logger.info(f"分页模式获取到更多章节，总数: {len(paginated)}")
                chapters = paginated
            elif len(chapters) < self.config.chapter_threshold:
                chapters = await self._fetch_multi_page_catalog(catalog_url, chapters)

        if check_update and self.novel and self.novel.chapters:
            old_urls = {ch.url for ch in self.novel.chapters}
            new_chapters = [ch for ch in chapters if ch.url not in old_urls]
            if new_chapters:
                logger.info(f"发现 {len(new_chapters)} 个新章节")
                all_chs = sorted(self.novel.chapters + new_chapters, key=lambda c: c.index)
                self.novel.chapters = [Chapter(i, ch.title, ch.url) for i, ch in enumerate(all_chs, 1)]
            else:
                logger.info("已是最新")
        else:
            self.novel = Novel(title=title, base_url=catalog_url, chapters=chapters, metadata=metadata)

        if self.config.cache_chapters:
            try:
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(self.novel.to_dict(), f, ensure_ascii=False, indent=2)
            except OSError as e:
                logger.debug(f"缓存保存失败: {e}")
        logger.info(f"解析完成: 《{self.novel.title}》 共 {len(self.novel.chapters)} 章")
        return self.novel

    async def _try_fetch_paginated_catalog(self, url: str, first_page_chapters: List[Chapter]) -> Optional[List[Chapter]]:
        match = re.search(r"^(.*?)(?:_(\d+))?(?:/|#|$)", url)
        if not match:
            return None
        base = match.group(1).rstrip("/")
        current_page = int(match.group(2)) if match.group(2) else 1
        all_chapters = {ch.url: ch for ch in first_page_chapters}
        page = 1
        while page <= self.config.max_pages:
            if page == current_page:
                page += 1
                continue
            candidates = [f"{base}/", base] if page == 1 else [f"{base}_{page}/", f"{base}_{page}"]
            html_content, used_url = None, None
            for cand in candidates:
                html_content = await self.network.fetch(cand, referer=url, use_browser=self.config.browser_config.enable)
                if html_content:
                    used_url = cand
                    break
            if not html_content:
                break
            rule = self.network.site_rule_manager.get_rule(used_url) if self.network else None
            page_chapters = self.parser.parse_chapters(html_content, used_url, rule=rule)
            if not page_chapters:
                break
            new_count = 0
            for ch in page_chapters:
                if ch.url not in all_chapters:
                    all_chapters[ch.url] = ch
                    new_count += 1
            logger.info(f"分页 {page} 获取到 {new_count} 个新章节")
            if new_count == 0:
                break
            page += 1
        if len(all_chapters) > len(first_page_chapters):
            sorted_chs = sorted(all_chapters.values(), key=lambda c: c.index)
            return [Chapter(i, ch.title, ch.url) for i, ch in enumerate(sorted_chs, 1)]
        return None

    async def _fetch_multi_page_catalog(self, base_url: str, first_page_chapters: List[Chapter]) -> List[Chapter]:
        logger.info("多页目录，正在获取全部页面...")
        all_chapters = {ch.url: ch for ch in first_page_chapters}
        visited, to_visit = {base_url}, [base_url]
        while to_visit and len(visited) < self.config.max_pages:
            current = to_visit.pop(0)
            html_content = await self.network.fetch(current, referer=base_url, use_browser=self.config.browser_config.enable)
            if not html_content:
                continue
            soup = BeautifulSoup(html_content, "html.parser")
            next_link = next((a["href"] for a in soup.find_all("a", href=True)
                              if any(t in a.get_text() for t in ["下一页","下页","next"])), None)
            if not next_link:
                elem = soup.find("a", rel="next")
                if elem:
                    next_link = elem["href"]
            if next_link:
                next_url = urljoin(current, next_link)
                if next_url not in visited:
                    visited.add(next_url)
                    to_visit.append(next_url)
                    rule = self.network.site_rule_manager.get_rule(current) if self.network else None
                    for ch in self.parser.parse_chapters(html_content, current, rule=rule):
                        if ch.url not in all_chapters:
                            all_chapters[ch.url] = ch
        sorted_chs = sorted(all_chapters.values(), key=lambda c: c.index)
        return [Chapter(i, ch.title, ch.url) for i, ch in enumerate(sorted_chs, 1)]

    def _extract_title(self, soup: BeautifulSoup, url: str) -> str:
        sources = [lambda: soup.find("meta", property="og:title"), lambda: soup.find("meta", attrs={"name": "title"}),
                   lambda: soup.find("h1"), lambda: soup.find("h2"), lambda: soup.find("title")]
        for getter in sources:
            elem = getter()
            if elem:
                text = elem.get("content") if hasattr(elem, "get") and elem.get("content") else elem.get_text()
                if text:
                    return self._clean_title(text.strip())
        path = urlparse(url).path.strip("/")
        return self._clean_title(path.split("/")[-1]) if path else f"小说_{int(time.time())}"

    async def _extract_metadata(self, soup: BeautifulSoup, url: str) -> Dict[str, Any]:
        meta = {}
        for sel in ['meta[property="og:novel:author"]', 'meta[name="author"]', ".author a", ".info a", "#author", ".writer"]:
            elem = soup.select_one(sel)
            if elem:
                author = elem.get("content") or elem.get_text(strip=True)
                if author:
                    meta["author"] = author[:100]
                    break
        for sel in ['meta[property="og:description"]', 'meta[name="description"]', ".intro", ".summary", "#description", ".book-intro"]:
            elem = soup.select_one(sel)
            if elem:
                desc = elem.get("content") or elem.get_text(strip=True)
                if desc and len(desc) > 20:
                    meta["description"] = desc[:500]
                    break
        for sel in ['meta[property="og:image"]', ".cover img", ".book-img img", "#bookimg img"]:
            elem = soup.select_one(sel)
            if elem:
                img_url = elem.get("content") or elem.get("src")
                if img_url:
                    meta["cover_url"] = urljoin(url, img_url)
                    break
        for sel in [".update", ".last-update", ".uptime"]:
            elem = soup.select_one(sel)
            if elem:
                meta["last_update"] = elem.get_text(strip=True)[:50]
                break
        return meta

    @staticmethod
    def _clean_title(text: str) -> str:
        text = re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9 _-]", "_", text)
        return re.sub(r"[ _]+", "_", text).strip("_")[:80] or "未知标题"

    async def _save_state_async(self):
        if not self.novel:
            return
        state_file = _safe_path(self.novel.base_url, ".state", self.config.output_dir)
        async with self._state_save_lock:
            try:
                state = {
                    "novel_title": self.novel.title,
                    "base_url": self.novel.base_url,
                    "all_chapters": [ch.to_dict() for ch in self.novel.chapters],
                    "downloaded_indices": list(self._downloaded_indices),
                    "chapter_hashes": list(self._content_hashes),
                    "failed_chapters": [ch.to_dict() for ch in self._failed_chapters],
                    "stats": self._stats,
                    "timestamp": time.time()
                }
                tmp_file = state_file.with_suffix(".state.tmp")
                content = json.dumps(state, ensure_ascii=False, indent=2)
                try:
                    import aiofiles
                    async with aiofiles.open(tmp_file, "w", encoding="utf-8") as f:
                        await f.write(content)
                except ImportError:
                    with open(tmp_file, "w", encoding="utf-8") as f:
                        f.write(content)
                os.replace(tmp_file, state_file)
            except OSError as e:
                logger.warning(f"自动保存状态失败: {e}")

    def _get_previous_chapter_url(self, chapter: Chapter) -> Optional[str]:
        if not self.novel:
            return None
        for i, ch in enumerate(self.novel.chapters):
            if ch.url == chapter.url:
                if i > 0:
                    return self.novel.chapters[i-1].url
                else:
                    return self.novel.base_url
        return self.novel.base_url

    async def _flush_pending(self):
        if not self._pending_results:
            return
        async with self._append_lock:
            if not self._pending_results:
                return
            to_write = self._pending_results[:]
            self._pending_results.clear()
        successful = [r for r in to_write if r.success]
        if not successful:
            return
        fmt = self.config.output_format.value if hasattr(self.config.output_format, 'value') else str(self.config.output_format)
        if fmt in ("epub", "pdf"):
            pass
        else:
            await self._storage.append(self.novel, successful, self._output_path)

    async def _atomic_append(self, result: DownloadResult):
        fmt = self.config.output_format.value if hasattr(self.config.output_format, 'value') else str(self.config.output_format)
        if fmt in ("epub", "pdf"):
            if self._temp_chapter_dir and result.success:
                chap_file = self._temp_chapter_dir / f"{result.chapter.index:06d}.txt"
                content = f"第{result.chapter.index}章 {result.chapter.title}\n\n{result.content}"
                await StorageBackend._write(chap_file, content)
            return
        if not self._storage or not self._output_path:
            return
        try:
            await self._storage.append_atomic(self.novel, result, self._output_path)
        except Exception as e:
            logger.error(f"原子追加失败: {e}")

    async def pause(self):
        self._pause_event.clear()
        if self.network:
            await self.network.pause()
        logger.info("下载已暂停")
        
    async def resume(self):
        self._pause_event.set()
        if self.network:
            await self.network.resume()
        logger.info("下载已恢复")
        
    async def cancel(self):
        self._cancelled = True
        await self.resume()
        logger.info("下载已取消")
        
    @property
    def is_paused(self) -> bool:
        return not self._pause_event.is_set()
        
    @property
    def is_cancelled(self) -> bool:
        return self._cancelled
        
    def get_stats(self) -> Dict:
        return dict(self._stats)
        
    def get_failed_chapters(self) -> List[Chapter]:
        return self._failed_chapters.copy()

    async def download_chapters(self, start: int = 1, end: Optional[int] = None,
                                progress_callback: Optional[Callable[[int, int], None]] = None) -> List[DownloadResult]:
        if not self.novel:
            raise RuntimeError("请先调用 fetch_catalog")
        chapters = self.novel.chapters[start-1:end]
        total = len(chapters)
        logger.info(f"开始下载 {total} 个章节...")
        
        self._stats['started_at'] = time.time()
        self._stats['total_chapters'] = total

        self._storage = StorageFactory.create(self.config.output_format, self.config)
        self._output_path = Path(self.config.output_dir) / re.sub(r"[^\w\u4e00-\u9fff-]", "_", self.novel.title)
        
        if self._temp_chapter_dir and self._temp_chapter_dir.exists():
            existing = set(int(f.stem) for f in self._temp_chapter_dir.glob("*.txt") if f.stem.isdigit())
            self._downloaded_indices.update(existing)
        
        async def download_one(chapter: Chapter) -> DownloadResult:
            if self._cancelled:
                return DownloadResult(chapter, status=DownloadStatus.FAILED, error="用户取消")
            await self._pause_event.wait()
            start_time = time.time()
            
            if chapter.index in self._downloaded_indices:
                self._stats['skipped'] += 1
                return DownloadResult(chapter, status=DownloadStatus.SKIPPED_DOWNLOADED,
                                      error="已下载", attempts=0)
            if self.config.chapter_filter and not re.search(self.config.chapter_filter, chapter.title):
                self._stats['skipped'] += 1
                return DownloadResult(chapter, status=DownloadStatus.SKIPPED_FILTER,
                                      error="正则过滤", attempts=0)
                                      
            referer = self._get_previous_chapter_url(chapter)
            rule = self.network.site_rule_manager.get_rule(chapter.url) if self.network else None
            
            for attempt in range(self.config.retry_times + 1):
                await self._pause_event.wait()
                if self._cancelled:
                    return DownloadResult(chapter, status=DownloadStatus.FAILED, error="用户取消")
                    
                html_content = await self.network.fetch(
                    chapter.url, referer=referer, use_browser=self.config.browser_config.enable,
                    wait_for_selector=getattr(self.config.browser_config, "wait_for_selector", None),
                    chapter=chapter
                )
                if html_content:
                    content = self.parser.parse_content(
                        html_content, chapter, self.config.chapter_selector, rule=rule
                    )
                    if content and self.config.download_images:
                        content = await self._extract_and_download_images(content, chapter.url)
                    if content:
                        if len(content) < self.config.browser_config.auto_render_threshold and not self.config.browser_config.enable:
                            logger.warning(f"第{chapter.index}章内容过短({len(content)}字符)，尝试浏览器渲染")
                            html_content = await self.network.fetch(
                                chapter.url, referer=referer, use_browser=True,
                                wait_for_selector=getattr(self.config.browser_config, "wait_for_selector", None),
                                chapter=chapter
                            )
                            if html_content:
                                content = self.parser.parse_content(
                                    html_content, chapter, self.config.chapter_selector, rule=rule
                                )
                        
                        if content and len(content) >= 20:
                            if self.config.deduplicate:
                                h = hashlib.md5(content.encode()).hexdigest()
                                if h in self._content_hashes:
                                    self._stats['skipped'] += 1
                                    return DownloadResult(chapter, status=DownloadStatus.SKIPPED_DUPLICATE,
                                                          error="内容重复", attempts=attempt+1)
                                self._content_hashes.add(h)
                            self._downloaded_indices.add(chapter.index)
                            self._stats['downloaded'] += 1
                            
                            result = DownloadResult(
                                chapter, content, status=DownloadStatus.SUCCESS,
                                attempts=attempt+1, duration=time.time()-start_time,
                                anti_crawl_level=self.network.anti_crawl.get_current_level(chapter.url).value if self.network else "normal"
                            )
                            
                            await self._atomic_append(result)
                            
                            async with self._save_counter_lock:
                                self._save_counter += 1
                                if self.config.save_interval > 0 and self._save_counter % self.config.save_interval == 0:
                                    await self._save_state_async()
                                    
                            return result
                            
                await asyncio.sleep(2**attempt + random.uniform(0, 1))
                
            self._stats['failed'] += 1
            self._failed_chapters.append(chapter)
            return DownloadResult(chapter, status=DownloadStatus.FAILED,
                                  error="多次尝试失败", attempts=self.config.retry_times+1,
                                  duration=time.time()-start_time)

        tasks = [download_one(ch) for ch in chapters]
        total_tasks = len(tasks)

        results = []
        try:
            from tqdm.asyncio import tqdm_asyncio
            pbar = tqdm_asyncio.as_completed(tasks, total=total_tasks, desc="下载进度", unit="章")
            for i, f in enumerate(pbar):
                r = await f
                results.append(r)
                await self._notify_progress(i+1, total_tasks, r.chapter, r.status.value)
        except ImportError:
            completed = 0
            for f in asyncio.as_completed(tasks):
                r = await f
                results.append(r)
                completed += 1
                await self._notify_progress(completed, total_tasks, r.chapter, r.status.value)
                
        elapsed = time.time() - self._stats['started_at']
        self._stats['completed_at'] = time.time()
        self._stats['total_duration'] = elapsed
        success = sum(1 for r in results if r.success)
        
        fmt = self.config.output_format.value if hasattr(self.config.output_format, 'value') else str(self.config.output_format)
        if fmt in ("epub", "pdf") and self._temp_chapter_dir:
            storage = StorageFactory.create(self.config.output_format, self.config)
            output_path = Path(self.config.output_dir) / re.sub(r"[^\w\u4e00-\u9fff-]", "_", self.novel.title)
            if fmt == "epub":
                if hasattr(storage, "merge_to_final"):
                    saved_path = await storage.merge_to_final(self.novel, output_path, self._images)
                else:
                    saved_path = await storage.save(self.novel, results, output_path, self._images, self._image_dir)
            else:
                if hasattr(storage, "merge_to_final"):
                    saved_path = await storage.merge_to_final(self.novel, output_path)
                else:
                    saved_path = await storage.save(self.novel, results, output_path, self._images, self._image_dir)
            print(f"💾 已保存到: {saved_path}")
        else:
            if fmt == "json" and hasattr(self._storage, "merge_to_final"):
                await self._storage.merge_to_final(self.novel, self._output_path)
        
        print(f"\n✅ 下载完成: 成功 {success}/{total_tasks} 章，总耗时 {elapsed:.1f} 秒", flush=True)

        if self.config.enable_notification:
            try:
                from plyer import notification
                notification.notify(
                    title="小说下载完成",
                    message=f"《{self.novel.title}》 共 {success}/{total_tasks} 章已保存",
                    timeout=5
                )
            except Exception as e:
                logger.debug(f"通知发送失败: {e}")
                
        return results

    async def retry_failed(self) -> List[DownloadResult]:
        if not self._failed_chapters:
            logger.info("没有失败的章节需要重试")
            return []
        logger.info(f"开始重试 {len(self._failed_chapters)} 个失败章节...")
        chapters = self._failed_chapters.copy()
        self._failed_chapters.clear()
        for ch in chapters:
            self._downloaded_indices.discard(ch.index)
        results = []
        for chapter in chapters:
            result = await self._download_single(chapter)
            results.append(result)
        return results
        
    async def _download_single(self, chapter: Chapter) -> DownloadResult:
        start_time = time.time()
        referer = self._get_previous_chapter_url(chapter)
        rule = self.network.site_rule_manager.get_rule(chapter.url) if self.network else None
        
        for attempt in range(self.config.retry_times + 1):
            html_content = await self.network.fetch(
                chapter.url, referer=referer, use_browser=self.config.browser_config.enable,
                wait_for_selector=getattr(self.config.browser_config, "wait_for_selector", None),
                chapter=chapter
            )
            if html_content:
                content = self.parser.parse_content(
                    html_content, chapter, self.config.chapter_selector, rule=rule
                )
                if content and self.config.download_images:
                    content = await self._extract_and_download_images(content, chapter.url)
                if content and len(content) >= 20:
                    if self.config.deduplicate:
                        h = hashlib.md5(content.encode()).hexdigest()
                        if h in self._content_hashes:
                            return DownloadResult(chapter, status=DownloadStatus.SKIPPED_DUPLICATE,
                                                  error="内容重复", attempts=attempt+1)
                        self._content_hashes.add(h)
                    self._downloaded_indices.add(chapter.index)
                    result = DownloadResult(
                        chapter, content, status=DownloadStatus.SUCCESS,
                        attempts=attempt+1, duration=time.time()-start_time
                    )
                    await self._atomic_append(result)
                    return result
            await asyncio.sleep(2**attempt + random.uniform(0, 1))
            
        return DownloadResult(chapter, status=DownloadStatus.FAILED,
                              error="多次尝试失败", attempts=self.config.retry_times+1,
                              duration=time.time()-start_time)

    async def _extract_and_download_images(self, content: str, base_url: str) -> str:
        if not (self.network and self.network.session and self._image_dir):
            return content
        soup = BeautifulSoup(content, "html.parser")
        img_tags = soup.find_all("img", src=True)
        if not img_tags:
            return str(soup)
        sem = asyncio.Semaphore(5)
        token_bucket = self.network._token_bucket if self.network else None

        async def dl_img(img):
            async with sem:
                if token_bucket:
                    wait = await token_bucket.consume()
                    if wait > 0:
                        await asyncio.sleep(wait)
                src = img["src"]
                img_url = urljoin(base_url, src) if not src.startswith(("http://", "https://")) else src
                ext = os.path.splitext(urlparse(src).path)[1]
                if not ext or len(ext) > 5:
                    ext = ".jpg"
                img_name = hashlib.md5(img_url.encode()).hexdigest() + ext
                img_path = self._image_dir / img_name
                if img_name not in self._images:
                    try:
                        if self.network._use_curl and self.network.session:
                            resp = await self.network.session.get(img_url, timeout=10)
                            async with resp:
                                if resp.status_code == 200:
                                    data = await resp.read()
                                    self._images[img_name] = data
                                    await StorageBackend._write_bytes(img_path, data)
                        elif HAS_AIOHTTP and self.network.session:
                            async with self.network.session.get(img_url, timeout=10) as resp:
                                if resp.status == 200:
                                    data = await resp.read()
                                    self._images[img_name] = data
                                    await StorageBackend._write_bytes(img_path, data)
                        elif HAS_REQUESTS:
                            loop = asyncio.get_event_loop()
                            data = await loop.run_in_executor(None, self._sync_download_image, img_url)
                            if data:
                                self._images[img_name] = data
                                await StorageBackend._write_bytes(img_path, data)
                    except Exception as e:
                        logger.debug(f"图片下载失败 {img_url}: {e}")
                        return
                img["src"] = str(img_path.relative_to(Path(self.config.output_dir)))
        await asyncio.gather(*(dl_img(img) for img in img_tags))
        return str(soup)

    def _sync_download_image(self, url: str) -> Optional[bytes]:
        try:
            import requests
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                return resp.content
        except Exception:
            pass
        return None