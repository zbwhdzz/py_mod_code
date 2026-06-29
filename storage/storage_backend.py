from __future__ import annotations
import json
import time
import hashlib
import mimetypes
import os
import re
import asyncio
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Dict, Optional, Any
from models.novel import Novel
from models.result import DownloadResult
from config.models import DownloadConfig
from config.enums import OutputFormat
from utils.helpers import safe_html_escape

try:
    from ebooklib import epub
    HAS_EPUB = True
except ImportError:
    HAS_EPUB = False

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False

logger = logging.getLogger(__name__)

class StorageBackend(ABC):
    @abstractmethod
    async def save(self, novel: Novel, results: List[DownloadResult], path: Path,
                   images: Dict[str, bytes] = None, image_dir: Path = None) -> Path:
        pass

    @abstractmethod
    def get_extension(self) -> str:
        pass

    @abstractmethod
    async def append(self, novel: Novel, results: List[DownloadResult], path: Path) -> Path:
        pass

    @abstractmethod
    async def append_atomic(self, novel: Novel, result: DownloadResult, path: Path) -> Path:
        pass

    @staticmethod
    async def _write(path: Path, content: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            import aiofiles
            async with aiofiles.open(path, "w", encoding="utf-8") as f:
                await f.write(content)
        except ImportError:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)

    @staticmethod
    async def _write_bytes(path: Path, content: bytes):
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            import aiofiles
            async with aiofiles.open(path, "wb") as f:
                await f.write(content)
        except ImportError:
            with open(path, "wb") as f:
                f.write(content)

class TextStorage(StorageBackend):
    async def save(self, novel, results, path, images=None, image_dir=None):
        path = path.with_suffix(".txt")
        lines = [f"《{novel.title}》", f"来源: {novel.base_url}", f"生成: {time.strftime('%Y-%m-%d %H:%M:%S')}",
                 f"总章节: {len(novel.chapters)}", f"下载: {len(results)} 章", "=" * 60, ""]
        for r in sorted(results, key=lambda r: r.chapter.index):
            if r.success:
                lines.extend(["", f"第{r.chapter.index}章 {r.chapter.title}", "", r.content])
            else:
                lines.extend(["", f"第{r.chapter.index}章 {r.chapter.title}", "", f"【{r.status.value}: {r.error}】", ""])
        await self._write(path, "\n".join(lines))
        return path

    def get_extension(self):
        return ".txt"

    async def append(self, novel: Novel, results: List[DownloadResult], path: Path) -> Path:
        path = path.with_suffix(".txt")
        lines = []
        for r in sorted(results, key=lambda r: r.chapter.index):
            if r.success:
                lines.extend(["", f"第{r.chapter.index}章 {r.chapter.title}", "", r.content])
            else:
                lines.extend(["", f"第{r.chapter.index}章 {r.chapter.title}", "", f"【{r.status.value}: {r.error}】", ""])
        try:
            import aiofiles
            async with aiofiles.open(path, "a", encoding="utf-8") as f:
                await f.write("\n".join(lines))
        except ImportError:
            with open(path, "a", encoding="utf-8") as f:
                f.write("\n".join(lines))
        return path

    async def append_atomic(self, novel: Novel, result: DownloadResult, path: Path) -> Path:
        path = path.with_suffix(".txt")
        lines = []
        if result.success:
            lines.extend(["", f"第{result.chapter.index}章 {result.chapter.title}", "", result.content])
        else:
            lines.extend(["", f"第{result.chapter.index}章 {result.chapter.title}", "", f"【{result.status.value}: {result.error}】", ""])
        try:
            import aiofiles
            async with aiofiles.open(path, "a", encoding="utf-8") as f:
                await f.write("\n".join(lines))
        except ImportError:
            with open(path, "a", encoding="utf-8") as f:
                f.write("\n".join(lines))
        return path

class JsonStorage(StorageBackend):
    async def save(self, novel, results, path, images=None, image_dir=None):
        path = path.with_suffix(".json")
        data = {"title": novel.title, "source": novel.base_url, "generated": time.strftime('%Y-%m-%d %H:%M:%S'),
                "total_chapters": len(novel.chapters),
                "chapters": [{"index": r.chapter.index, "title": r.chapter.title, "content": r.content,
                              "status": r.status.value, "error": r.error, "timestamp": r.timestamp}
                             for r in sorted(results, key=lambda x: x.chapter.index)]}
        await self._write(path, json.dumps(data, ensure_ascii=False, indent=2))
        return path

    def get_extension(self):
        return ".json"

    async def append(self, novel: Novel, results: List[DownloadResult], path: Path) -> Path:
        chapter_dir = path.parent / f"{path.stem}_chapters"
        chapter_dir.mkdir(parents=True, exist_ok=True)
        for r in results:
            chap_file = chapter_dir / f"{r.chapter.index:06d}.json"
            chap_data = {
                "index": r.chapter.index,
                "title": r.chapter.title,
                "content": r.content,
                "status": r.status.value,
                "error": r.error,
                "timestamp": r.timestamp
            }
            await self._write(chap_file, json.dumps(chap_data, ensure_ascii=False, indent=2))
        return path

    async def append_atomic(self, novel: Novel, result: DownloadResult, path: Path) -> Path:
        return await self.append(novel, [result], path)

    async def merge_to_final(self, novel: Novel, path: Path) -> Path:
        path = path.with_suffix(".json")
        chapter_dir = path.parent / f"{path.stem}_chapters"
        chapters = []
        if chapter_dir.exists():
            for chap_file in sorted(chapter_dir.glob("*.json")):
                try:
                    with open(chap_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    chapters.append(data)
                except:
                    pass
        data = {"title": novel.title, "source": novel.base_url, "generated": time.strftime('%Y-%m-%d %H:%M:%S'),
                "total_chapters": len(chapters), "chapters": chapters}
        await self._write(path, json.dumps(data, ensure_ascii=False, indent=2))
        return path

class EpubStorage(StorageBackend):
    async def save(self, novel, results, path, images=None, image_dir=None):
        if not HAS_EPUB:
            raise RuntimeError("ebooklib未安装，无法生成EPUB")
        path = path.with_suffix(".epub")
        book = epub.EpubBook()
        book.set_identifier(hashlib.md5(novel.base_url.encode()).hexdigest()[:8])
        book.set_title(novel.title)
        book.set_language("zh-CN")
        book.add_author("智能爬虫")
        if images:
            for img_name, img_data in images.items():
                item = epub.EpubImage()
                item.file_name = f"Images/{img_name}"
                ext = img_name.rsplit(".", 1)[-1].lower() if "." in img_name else "jpeg"
                item.media_type = mimetypes.types_map.get(f".{ext}", f"image/{ext}")
                item.content = img_data
                book.add_item(item)
        chapters_epub = []
        for r in sorted(results, key=lambda r: r.chapter.index):
            if not r.content:
                continue
            content = r.content
            if images:
                for img_name in images:
                    content = content.replace(f'src="{img_name}"', f'src="Images/{img_name}"')
            html_parts = []
            for line in content.split("\n"):
                s = line.strip()
                if not s:
                    continue
                imgs = re.findall(r"<img[^>]*>", s)
                temp = re.sub(r"<img[^>]*>", "__IMG__", s)
                temp = safe_html_escape(temp)
                for img in imgs:
                    temp = temp.replace("__IMG__", img, 1)
                html_parts.append(f"<p>{temp}</p>")
            chap = epub.EpubHtml(title=r.chapter.title, file_name=f"chap_{r.chapter.index}.xhtml", lang="zh-CN")
            chap.content = f"<h1>{safe_html_escape(r.chapter.title)}</h1>{''.join(html_parts)}"
            book.add_item(chap)
            chapters_epub.append(chap)
        book.toc = chapters_epub
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        book.spine = ["nav"] + chapters_epub
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, epub.write_epub, str(path), book, {})
        return path

    def get_extension(self):
        return ".epub"

    async def append(self, novel: Novel, results: List[DownloadResult], path: Path) -> Path:
        tmp_dir = path.parent / f"{path.stem}_epub_tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        for r in results:
            if r.success:
                chap_file = tmp_dir / f"{r.chapter.index:06d}.xhtml"
                content = f"<h1>{safe_html_escape(r.chapter.title)}</h1>\n"
                for para in r.content.split("\n"):
                    if para.strip():
                        content += f"<p>{safe_html_escape(para)}</p>\n"
                await self._write(chap_file, content)
        return path

    async def append_atomic(self, novel: Novel, result: DownloadResult, path: Path) -> Path:
        return await self.append(novel, [result], path)

    async def merge_to_final(self, novel: Novel, path: Path, images: Dict = None) -> Path:
        if not HAS_EPUB:
            raise RuntimeError("ebooklib未安装")
        path = path.with_suffix(".epub")
        tmp_dir = path.parent / f"{path.stem}_epub_tmp"
        if not tmp_dir.exists():
            return path
        book = epub.EpubBook()
        book.set_identifier(hashlib.md5(novel.base_url.encode()).hexdigest()[:8])
        book.set_title(novel.title)
        book.set_language("zh-CN")
        book.add_author("智能爬虫")
        if images:
            for img_name, img_data in images.items():
                item = epub.EpubImage()
                item.file_name = f"Images/{img_name}"
                ext = img_name.rsplit(".", 1)[-1].lower() if "." in img_name else "jpeg"
                item.media_type = mimetypes.types_map.get(f".{ext}", f"image/{ext}")
                item.content = img_data
                book.add_item(item)
        chapters_epub = []
        for chap_file in sorted(tmp_dir.glob("*.xhtml")):
            with open(chap_file, "r", encoding="utf-8") as f:
                content = f.read()
            title_match = re.search(r"<h1>(.*?)</h1>", content)
            title = title_match.group(1) if title_match else "章节"
            chap = epub.EpubHtml(title=title, file_name=chap_file.name, lang="zh-CN")
            chap.content = content
            book.add_item(chap)
            chapters_epub.append(chap)
        book.toc = chapters_epub
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        book.spine = ["nav"] + chapters_epub
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, epub.write_epub, str(path), book, {})
        for f in tmp_dir.glob("*"):
            f.unlink()
        tmp_dir.rmdir()
        return path

def _find_system_chinese_font() -> Optional[str]:
    import platform
    system = platform.system()
    candidates = []
    if system == "Windows":
        candidates = [r"C:\Windows\Fonts\simsun.ttc", r"C:\Windows\Fonts\msyh.ttc"]
    elif system == "Darwin":
        candidates = ["/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Light.ttc"]
    elif system == "Linux":
        candidates = ["/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"]
    for f in candidates:
        if os.path.exists(f):
            return f
    return None

class PdfStorage(StorageBackend):
    def __init__(self, config: DownloadConfig):
        self.config = config

    def get_extension(self) -> str:
        return ".pdf"

    async def save(self, novel, results, path, images=None, image_dir=None):
        if not HAS_REPORTLAB:
            raise RuntimeError("请安装 reportlab 以生成 PDF: pip install reportlab")
        path = path.with_suffix(".pdf")
        doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=20*mm, rightMargin=20*mm,
                                topMargin=20*mm, bottomMargin=20*mm)
        styles = getSampleStyleSheet()
        font_path = getattr(self.config, "pdf_font_path", None) or _find_system_chinese_font()
        if font_path and os.path.exists(font_path):
            pdfmetrics.registerFont(TTFont("ChineseFont", font_path))
            styles.add(ParagraphStyle(name="ChineseNormal", fontName="ChineseFont", fontSize=12, leading=14))
            styles.add(ParagraphStyle(name="ChineseTitle", fontName="ChineseFont", fontSize=20, leading=24))
        else:
            logger.warning("未找到中文字体，PDF 中文字可能无法正常显示。")
        story = []
        story.append(Paragraph(f"《{safe_html_escape(novel.title)}》", styles["Title"]))
        story.append(Spacer(1, 12*mm))
        if novel.metadata.get("author"):
            story.append(Paragraph(f"作者：{safe_html_escape(novel.metadata['author'])}", styles["Normal"]))
        story.append(Spacer(1, 6*mm))
        if novel.metadata.get("description"):
            story.append(Paragraph("内容简介", styles["Heading2"]))
            story.append(Paragraph(safe_html_escape(novel.metadata["description"]).replace("\n", "<br/>"), styles["Normal"]))
            story.append(PageBreak())
        for r in sorted(results, key=lambda x: x.chapter.index):
            if not r.content:
                continue
            story.append(Paragraph(f"第{r.chapter.index}章 {safe_html_escape(r.chapter.title)}", styles["Heading2"]))
            for para in r.content.split("\n"):
                if para.strip():
                    escaped_para = safe_html_escape(para)
                    story.append(Paragraph(escaped_para, styles.get("ChineseNormal", styles["Normal"])))
            story.append(Spacer(1, 6*mm))
        doc.build(story)
        return path

    async def append(self, novel: Novel, results: List[DownloadResult], path: Path) -> Path:
        tmp_dir = path.parent / f"{path.stem}_pdf_tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        for r in results:
            if r.success:
                chap_file = tmp_dir / f"{r.chapter.index:06d}.txt"
                content = f"第{r.chapter.index}章 {r.chapter.title}\n\n{r.content}"
                await self._write(chap_file, content)
        return path

    async def append_atomic(self, novel: Novel, result: DownloadResult, path: Path) -> Path:
        return await self.append(novel, [result], path)

    async def merge_to_final(self, novel: Novel, path: Path) -> Path:
        if not HAS_REPORTLAB:
            raise RuntimeError("reportlab未安装")
        path = path.with_suffix(".pdf")
        tmp_dir = path.parent / f"{path.stem}_pdf_tmp"
        if not tmp_dir.exists():
            return path
        doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=20*mm, rightMargin=20*mm,
                                topMargin=20*mm, bottomMargin=20*mm)
        styles = getSampleStyleSheet()
        font_path = getattr(self.config, "pdf_font_path", None) or _find_system_chinese_font()
        if font_path and os.path.exists(font_path):
            pdfmetrics.registerFont(TTFont("ChineseFont", font_path))
            styles.add(ParagraphStyle(name="ChineseNormal", fontName="ChineseFont", fontSize=12, leading=14))
            styles.add(ParagraphStyle(name="ChineseTitle", fontName="ChineseFont", fontSize=20, leading=24))
        story = []
        story.append(Paragraph(f"《{safe_html_escape(novel.title)}》", styles["Title"]))
        story.append(Spacer(1, 12*mm))
        if novel.metadata.get("author"):
            story.append(Paragraph(f"作者：{safe_html_escape(novel.metadata['author'])}", styles["Normal"]))
        story.append(Spacer(1, 6*mm))
        if novel.metadata.get("description"):
            story.append(Paragraph("内容简介", styles["Heading2"]))
            story.append(Paragraph(safe_html_escape(novel.metadata["description"]).replace("\n", "<br/>"), styles["Normal"]))
            story.append(PageBreak())
        for chap_file in sorted(tmp_dir.glob("*.txt")):
            with open(chap_file, "r", encoding="utf-8") as f:
                content = f.read()
            lines = content.split("\n", 1)
            title = lines[0] if lines else "章节"
            body = lines[1] if len(lines) > 1 else ""
            story.append(Paragraph(safe_html_escape(title), styles["Heading2"]))
            for para in body.split("\n"):
                if para.strip():
                    story.append(Paragraph(safe_html_escape(para), styles.get("ChineseNormal", styles["Normal"])))
            story.append(Spacer(1, 6*mm))
        doc.build(story)
        for f in tmp_dir.glob("*"):
            f.unlink()
        tmp_dir.rmdir()
        return path

class StorageFactory:
    _backends = {"txt": TextStorage, "json": JsonStorage, "epub": EpubStorage, "pdf": PdfStorage}

    @classmethod
    def create(cls, format_type, config: Optional[DownloadConfig] = None) -> StorageBackend:
        fmt = format_type.value if hasattr(format_type, 'value') else str(format_type).lower()
        if fmt == "pdf":
            if config is None:
                raise ValueError("PDF 存储需要提供 config 参数")
            return PdfStorage(config)
        backend_cls = cls._backends.get(fmt, TextStorage)
        return backend_cls()