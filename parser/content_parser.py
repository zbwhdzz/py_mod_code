from __future__ import annotations
import re
import logging
import base64
import io
from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Set, Tuple
from urllib.parse import urljoin
from functools import lru_cache
from bs4 import BeautifulSoup, Tag
from config.models import SiteRule
from models.chapter import Chapter
from utils.helpers import safe_html_escape

try:
    from fontTools.ttLib import TTFont as FontToolsTTFont
    HAS_FONTTOOLS = True
except ImportError:
    HAS_FONTTOOLS = False

logger = logging.getLogger(__name__)

class ContentParser(ABC):
    def __init__(self):
        self.ad_patterns = self._compile_ad_patterns()
        self._exclude_content_classes = {"comment", "comments", "review", "recommend", "related", "ad", "ads"}

    @staticmethod
    def _compile_ad_patterns() -> List[re.Pattern]:
        keywords = ["上一页", "下一页", "目录", "首页", "末页", "返回", "电脑版", "手机版",
                    "加入书架", "求书", "催更", "推荐票", "月票", "打赏", "收藏", "投票",
                    "请记住", "收藏本站", "QQ群", "微信公众号", "天才一秒记住",
                    "本章未完", "点击下一页", "继续阅读", "热门推荐", "刷新", "重试",
                    "关灯", "护眼", "字体大小"]
        patterns = [rf"^.*{re.escape(kw)}.*$" for kw in keywords]
        patterns.extend([r"^.*https?://[^\s]+.*$", r"^.{0,10}章\s*节.*$", r"^[\s\d\W]{1,20}$"])
        return [re.compile(p, re.I) for p in patterns]

    @abstractmethod
    def parse_chapters(self, html: str, base_url: str, rule: Optional[SiteRule] = None) -> List[Chapter]:
        pass

    @abstractmethod
    def parse_content(self, html: str, chapter: Chapter, custom_selector: Optional[str] = None,
                     rule: Optional[SiteRule] = None) -> Optional[str]:
        pass

    def _create_soup(self, html: str) -> BeautifulSoup:
        try:
            return BeautifulSoup(html, "lxml")
        except Exception:
            return BeautifulSoup(html, "html.parser")

    def _clean_content(self, content: str) -> str:
        content = content.replace("\u3000", " ").replace("\xa0", " ")
        content = re.sub(r"\n{3,}", "\n\n", content)
        lines = content.split("\n")
        clean_lines = [l for l in (line.strip() for line in lines) if not l or not any(p.match(l) for p in self.ad_patterns)]
        result, prev_empty = [], False
        for line in clean_lines:
            if line == "":
                if not prev_empty:
                    result.append("")
                prev_empty = True
            else:
                result.append(line)
                prev_empty = False
        return "\n".join(result)

    def _is_undesired_content_block(self, elem: Tag) -> bool:
        if not elem:
            return True
        classes = set(elem.get("class", []))
        if classes & self._exclude_content_classes:
            return True
        parent = elem.parent
        if parent and hasattr(parent, "get"):
            parent_classes = set(parent.get("class", []))
            if parent_classes & self._exclude_content_classes:
                return True
        return False

    def _decrypt_font(self, html: str, font_mapping: Optional[Dict[str, str]] = None) -> str:
        if not HAS_FONTTOOLS:
            return html
        soup = self._create_soup(html)
        font_pattern = re.compile(r'data:font/[^;]+;base64,([^"\']+)')
        matches = font_pattern.findall(html)
        if not matches and not font_mapping:
            return html
        decrypted = html
        if font_mapping:
            for encoded, decoded in font_mapping.items():
                decrypted = decrypted.replace(encoded, decoded)
            return decrypted
        for i, b64_font in enumerate(matches):
            try:
                font_data = base64.b64decode(b64_font)
                font = FontToolsTTFont(io.BytesIO(font_data))
                cmap = font.getBestCmap()
                if cmap:
                    logger.info(f"检测到字体加密，提取到 {len(cmap)} 个字形映射")
            except Exception as e:
                logger.debug(f"字体解析失败: {e}")
        return decrypted

class GenericParser(ContentParser):
    CN_NUM_MAP = {"零":0,"一":1,"二":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9,"十":10,"百":100,"千":1000,"万":10000}
    CONTENT_SELECTORS = [("id","content"),("id","chaptercontent"),("id","booktext"),("id","novelcontent"),
                         ("id","chapterContent"),("id","ChapterContent"),("class","content"),("class","chapter-content"),
                         ("class","novel-content"),("class","read-content"),("class","article-content"),
                         ("tag","article"),("tag","div"),("tag","section"),("tag","main")]

    @staticmethod
    @lru_cache(maxsize=128)
    def _compile_chapter_regex():
        return re.compile(r"第\s*(\d+)\s*章")

    @staticmethod
    @lru_cache(maxsize=128)
    def _compile_cn_regex():
        return re.compile(r"第\s*([零一二三四五六七八九十百千万]+)\s*")

    @staticmethod
    def cn_to_num(cn_str: str) -> int:
        total, temp = 0, 0
        for char in cn_str:
            if char not in GenericParser.CN_NUM_MAP:
                continue
            num = GenericParser.CN_NUM_MAP[char]
            if num >= 10:
                total += (temp or 1) * num
                temp = 0
            else:
                temp = temp * 10 + num
        return total + temp

    def parse_chapters(self, html: str, base_url: str, rule: Optional[SiteRule] = None) -> List[Chapter]:
        soup = self._create_soup(html)
        chapters, seen_urls = [], set()
        if rule and rule.chapter_selector:
            try:
                container = soup.select_one(rule.chapter_selector)
                if container:
                    chapters = self._extract_from_container(container, base_url, seen_urls)
                    if chapters:
                        logger.info(f"站点规则选择器匹配到 {len(chapters)} 章")
                        return self._sort_chapters(chapters)
            except Exception as e:
                logger.warning(f"站点规则选择器失败: {e}")
        catalog_selectors = ["#list", ".listmain", ".chapter-list", ".chapterlist", ".chapters", ".catalog",
                             ".directory", "#chapter-list", "#catalog", ".volume-list", "dl.chapterlist",
                             "ul.chapter-list", ".chapter_List"]
        for sel in catalog_selectors:
            container = soup.select_one(sel)
            if container:
                chapters = self._extract_from_container(container, base_url, seen_urls)
                if chapters:
                    break
        if not chapters:
            chapters = self._scan_all_links(soup, base_url, seen_urls)
        return self._sort_chapters(chapters)

    def _extract_from_container(self, container: Tag, base_url: str, seen: Set[str]) -> List[Chapter]:
        chapters = []
        for link in container.find_all("a", href=True):
            title, href = link.get_text(strip=True), link["href"]
            if not self._is_valid_chapter(title, href):
                continue
            full_url = self._normalize_url(urljoin(base_url, href))
            if full_url not in seen:
                seen.add(full_url)
                chapters.append(Chapter(index=len(chapters), title=title, url=full_url))
        return chapters

    def _scan_all_links(self, soup: BeautifulSoup, base_url: str, seen: Set[str]) -> List[Chapter]:
        chapters = []
        for link in soup.find_all("a", href=True):
            title, href = link.get_text(strip=True), link["href"]
            if not self._is_valid_chapter(title, href):
                continue
            full_url = self._normalize_url(urljoin(base_url, href))
            if full_url not in seen:
                seen.add(full_url)
                chapters.append(Chapter(index=len(chapters), title=title, url=full_url))
        return chapters

    def _is_valid_chapter(self, title: str, href: str) -> bool:
        if not title or not href:
            return False
        if re.search(r"第\d+-\d+章", title):
            return False
        if href.startswith(("javascript:", "mailto:", "tel:", "#")):
            return False
        nav_words = {"上一页", "下一页", "首页", "末页", "目录", "返回", "书架"}
        if any(w in title for w in nav_words):
            return False
        special_titles = {"前言","后记","楔子","番外","引言","序言","终章","大结局","完本感言"}
        if any(t in title for t in special_titles):
            return True
        has_chapter_mark = any(c in title for c in ["章","节","回","卷","集","话"])
        has_number_start = re.search(r"^第[零一二三四五六七八九十百千万\d]+", title)
        if not (has_chapter_mark or has_number_start):
            return False
        return 2 <= len(title) <= 100

    @staticmethod
    def _normalize_url(url: str) -> str:
        url = url.split("#")[0]
        return re.sub(r"(/index\.html?)?$", "", url)

    def _sort_chapters(self, chapters: List[Chapter]) -> List[Chapter]:
        def extract_key(ch: Chapter) -> Tuple[int, int]:
            m = self._compile_chapter_regex().search(ch.title)
            if m:
                return int(m.group(1)), 0
            m = self._compile_cn_regex().search(ch.title)
            if m:
                return self.cn_to_num(m.group(1)), 0
            vol_match = re.search(r"第\s*(\d+)\s*卷", ch.title)
            if vol_match:
                return int(vol_match.group(1)) * 1000, 1
            nums = re.findall(r"(\d+)", ch.url)
            if nums:
                return int(nums[-1]), 2
            title_nums = re.findall(r"(\d+)", ch.title)
            if title_nums:
                return int(title_nums[-1]), 3
            return 999999, 4
        sorted_chs = sorted(chapters, key=extract_key)
        return [Chapter(i, ch.title, ch.url) for i, ch in enumerate(sorted_chs, 1)]

    def parse_content(self, html: str, chapter: Chapter, custom_selector: Optional[str] = None,
                     rule: Optional[SiteRule] = None) -> Optional[str]:
        html = self._decrypt_font(html, rule.font_mapping if rule else None)
        soup = self._create_soup(html)
        content_elem = None
        if rule and rule.content_selector:
            try:
                elems = soup.select(rule.content_selector)
                if elems:
                    content_elem = elems[0]
                    logger.debug(f"使用站点规则内容选择器: {rule.content_selector}")
            except Exception as e:
                logger.debug(f"站点规则内容选择器失败: {e}")
        if not content_elem and custom_selector:
            try:
                elems = soup.select(custom_selector)
                if elems:
                    content_elem = elems[0]
            except Exception:
                pass
        if not content_elem:
            for sel_type, sel_value in self.CONTENT_SELECTORS:
                try:
                    if sel_type == "id":
                        elem = soup.find(id=sel_value)
                    elif sel_type == "class":
                        elem = soup.find(class_=sel_value)
                    else:
                        elem = soup.find(sel_value)
                    if elem and len(elem.get_text(strip=True)) > 100 and not self._is_undesired_content_block(elem):
                        content_elem = elem
                        break
                except Exception:
                    continue
        if not content_elem:
            best_elem, max_len = None, 0
            for elem in soup.find_all(["div", "article", "section"]):
                if self._is_undesired_content_block(elem):
                    continue
                for noise in elem.find_all(["script", "style", "nav", "footer"]):
                    noise.decompose()
                text_len = len(elem.get_text(strip=True))
                if text_len > max_len and text_len > 200:
                    max_len = text_len
                    best_elem = elem
            content_elem = best_elem or soup.find("body")
        if not content_elem:
            return None
        for noise in content_elem.find_all(["script", "style", "nav", "footer", "header", "iframe"]):
            noise.decompose()
        text = content_elem.get_text("\n", strip=False)
        cleaned = self._clean_content(text)
        return cleaned if len(cleaned) > 20 else None