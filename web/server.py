import json
import time
import asyncio
import threading
import logging
import base64
from collections import deque
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from typing import Optional, List, Dict
from pathlib import Path
import re

from config.models import DownloadConfig
from utils.semaphore import DynamicSemaphore
from utils.ssrf import is_private_ip
from downloader.novel_downloader import NovelDownloader
from parser.content_parser import GenericParser
from storage.storage_backend import StorageFactory

logger = logging.getLogger(__name__)

_web_download_semaphore = None
_web_loop = None
_web_loop_thread = None
_web_ui = None
_web_rate_limiter: Dict[str, deque] = {}

class NovelWebHandler(BaseHTTPRequestHandler):
    allowed_domains: Optional[List[str]] = None
    ui = None
    username: Optional[str] = None
    password: Optional[str] = None
    rate_limit: int = 20

    def _check_auth(self, auth_header: str) -> bool:
        if not self.username or not self.password:
            return True
        if not auth_header:
            return False
        if not auth_header.startswith("Basic "):
            return False
        encoded = auth_header[6:]
        try:
            decoded = base64.b64decode(encoded).decode("utf-8")
            user, pwd = decoded.split(":", 1)
            return user == self.username and pwd == self.password
        except:
            return False

    def _check_rate_limit(self, client_ip: str) -> bool:
        global _web_rate_limiter
        now = time.time()
        if client_ip not in _web_rate_limiter:
            _web_rate_limiter[client_ip] = deque()
        q = _web_rate_limiter[client_ip]
        while q and q[0] < now - 60:
            q.popleft()
        if len(q) >= self.rate_limit:
            return False
        q.append(now)
        return True

    def do_GET(self):
        parsed = urlparse(self.path)
        client_ip = self.client_address[0]
        if not self._check_rate_limit(client_ip):
            self.send_response(429)
            self.end_headers()
            self.wfile.write(b"Too Many Requests")
            return
        auth = self.headers.get('Authorization')
        if not self._check_auth(auth):
            self.send_response(401)
            self.send_header('WWW-Authenticate', 'Basic realm="Novel Spider"')
            self.end_headers()
            return
        if parsed.path == "/":
            self._send_html(WEB_PAGE)
        elif parsed.path == "/style.css":
            self._send_css()
        elif parsed.path == "/api/status":
            self._send_status()
        elif parsed.path == "/api/config":
            self._send_config()
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        client_ip = self.client_address[0]
        if not self._check_rate_limit(client_ip):
            self.send_response(429)
            self.end_headers()
            return
        auth = self.headers.get('Authorization')
        if not self._check_auth(auth):
            self.send_response(401)
            self.send_header('WWW-Authenticate', 'Basic realm="Novel Spider"')
            self.end_headers()
            return
        if self.path == "/download":
            self._handle_download()
        elif self.path == "/api/pause":
            self._handle_pause()
        elif self.path == "/api/resume":
            self._handle_resume()
        elif self.path == "/api/config":
            self._handle_config_update()
        else:
            self._send_json({"status": "error", "msg": "Not Found"}, 404)

    def _handle_download(self):
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length)
        params = parse_qs(post_data.decode("utf-8"))
        url = params.get("url", [""])[0].strip()
        fmt = params.get("format", ["txt"])[0].strip().lower()
        if not url:
            self._send_json({"status": "error", "msg": "URL不能为空"})
            return
        allowed = self.allowed_domains
        if allowed:
            hostname = urlparse(url).hostname
            if hostname not in allowed:
                self._send_json({"status": "error", "msg": f"域名 {hostname} 不在允许列表中"})
                return
            if is_private_ip(hostname):
                self._send_json({"status": "error", "msg": "禁止访问内网地址"})
                return
        if fmt not in ("txt", "json", "epub", "pdf"):
            self._send_json({"status": "error", "msg": "格式错误，必须为txt/json/epub/pdf"})
            return
        if _web_download_semaphore is None:
            self._send_json({"status": "error", "msg": "服务器未就绪"})
            return
            
        async def task():
            async with _web_download_semaphore:
                dl_config = _web_ui.observable_config.snapshot() if _web_ui and _web_ui.observable_config else DownloadConfig()
                dl_config.output_format = fmt
                dl_config.output_dir = "downloads"
                if allowed:
                    dl_config.allowed_domains = allowed
                async with NovelDownloader(dl_config, GenericParser()) as dl:
                    if _web_ui and _web_ui.observable_config:
                        dl.bind_observable_config(_web_ui.observable_config)
                    novel = await dl.fetch_catalog(url)
                    if not novel:
                        logger.error(f"[Web] 获取目录失败: {url}")
                        return
                    results = await dl.download_chapters(1, len(novel.chapters))
                    storage = StorageFactory.create(fmt, dl_config)
                    out_path = Path("downloads") / re.sub(r"[^\w\u4e00-\u9fff-]", "_", novel.title)
                    saved = await storage.save(novel, results, out_path, dl._images, dl._image_dir)
                    logger.info(f"[Web] 下载完成: {saved}")
        asyncio.run_coroutine_threadsafe(task(), _web_loop)
        self._send_json({"status": "started", "msg": "已开始下载，请查看终端输出"})

    def _handle_pause(self):
        async def task():
            if _web_ui:
                async with _web_ui._download_lock:
                    for dl in _web_ui._active_downloads:
                        await dl.pause()
        asyncio.run_coroutine_threadsafe(task(), _web_loop)
        self._send_json({"status": "paused", "msg": "所有下载已暂停"})

    def _handle_resume(self):
        async def task():
            if _web_ui:
                async with _web_ui._download_lock:
                    for dl in _web_ui._active_downloads:
                        await dl.resume()
        asyncio.run_coroutine_threadsafe(task(), _web_loop)
        self._send_json({"status": "resumed", "msg": "所有下载已恢复"})

    def _handle_config_update(self):
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length)
        try:
            updates = json.loads(post_data.decode("utf-8"))
            if _web_ui and _web_ui.observable_config:
                loop = asyncio.new_event_loop()
                results = loop.run_until_complete(_web_ui.observable_config.update(updates))
                loop.close()
                self._send_json({"status": "ok", "results": results})
            else:
                self._send_json({"status": "error", "msg": "配置管理器未初始化"})
        except json.JSONDecodeError:
            self._send_json({"status": "error", "msg": "无效的JSON数据"})

    def _send_status(self):
        status = {
            "status": "running",
            "allowed_domains": self.allowed_domains,
            "semaphore_limit": _web_download_semaphore._max if _web_download_semaphore else 0,
        }
        if _web_ui:
            status["config_loaded"] = True
        self._send_json(status)

    def _send_config(self):
        if _web_ui:
            config_dict = _web_ui._obj_to_dict(_web_ui.config)
            self._send_json({"status": "ok", "config": config_dict})
        else:
            self._send_json({"status": "error", "msg": "配置未加载"})

    def _send_html(self, content):
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(content.encode("utf-8"))

    def _send_css(self):
        self.send_response(200)
        self.send_header("Content-type", "text/css")
        self.end_headers()
        self.wfile.write(CSS_STYLE.encode("utf-8"))

    def _send_json(self, data, code=200):
        self.send_response(code)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

def start_web_server(port=8000, allowed_domains=None, ui=None,
                    username=None, password=None, rate_limit=20):
    global _web_download_semaphore, _web_loop, _web_loop_thread, _web_ui
    _web_ui = ui
    def run_loop(loop):
        asyncio.set_event_loop(loop)
        loop.run_forever()
    _web_loop = asyncio.new_event_loop()
    _web_loop_thread = threading.Thread(target=run_loop, args=(_web_loop,), daemon=True)
    _web_loop_thread.start()
    async def init_semaphore():
        global _web_download_semaphore
        _web_download_semaphore = DynamicSemaphore(3)
    asyncio.run_coroutine_threadsafe(init_semaphore(), _web_loop).result()
    NovelWebHandler.allowed_domains = allowed_domains
    NovelWebHandler.ui = ui
    NovelWebHandler.username = username
    NovelWebHandler.password = password
    NovelWebHandler.rate_limit = rate_limit
    server = HTTPServer(("0.0.0.0", port), NovelWebHandler)
    print(f"\n🌐 网页服务器已启动")
    print(f"📱 请在手机浏览器中打开: http://127.0.0.1:{port}")
    if username:
        print(f"🔒 需要认证: 用户名 {username}")
    print(f"⚠️ 如果手机与电脑在同一局域网，也可使用局域网 IP 访问")
    if allowed_domains:
        print(f"🔒 已限制只允许域名: {', '.join(allowed_domains)}")
    else:
        print(f"⚠️ 未设置域名白名单，可以下载任意 URL，存在安全风险。")
    print(f"🛑 按 Ctrl+C 停止服务器\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务器已关闭")
        _web_loop.call_soon_threadsafe(_web_loop.stop)

WEB_PAGE = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=yes">
    <title>智能小说爬虫 - 企业级版 v3.0</title>
    <link rel="stylesheet" href="/style.css">
</head>
<body>
    <div class="container">
        <h1>📚 智能小说爬虫 v3.0</h1>
        <p style="color:#ff9800;font-size:14px;">⚠️ 管理员已配置域名白名单，仅允许访问指定站点。</p>
        <form id="downloadForm">
            <label>📖 小说目录页 URL：</label>
            <input type="url" id="url" placeholder="https://www.biquge.com.cn/book/123/" required>
            <label>📁 输出格式：</label>
            <select id="format">
                <option value="txt">TXT文本</option>
                <option value="json">JSON数据</option>
                <option value="epub">EPUB电子书</option>
                <option value="pdf">PDF文档</option>
            </select>
            <button type="submit">🚀 开始下载</button>
        </form>
        <div class="controls">
            <button onclick="pauseAll()">⏸️ 暂停全部</button>
            <button onclick="resumeAll()">▶️ 恢复全部</button>
        </div>
        <div id="status">⭐ 等待下载任务</div>
    </div>
    <script>
        document.getElementById('downloadForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            const url = document.getElementById('url').value;
            const format = document.getElementById('format').value;
            const statusDiv = document.getElementById('status');
            statusDiv.innerHTML = '⏳ 提交任务中...';
            try {
                const formData = new URLSearchParams();
                formData.append('url', url);
                formData.append('format', format);
                const resp = await fetch('/download', { method: 'POST', body: formData });
                const data = await resp.json();
                if (data.status === 'started') {
                    statusDiv.innerHTML = '✅ ' + data.msg + '<br>📂 文件将保存在 downloads 目录，请查看终端输出。';
                } else {
                    statusDiv.innerHTML = '❌ 错误：' + data.msg;
                }
            } catch (err) {
                statusDiv.innerHTML = '❌ 网络错误：' + err;
            }
        });
        async function pauseAll() {
            const resp = await fetch('/api/pause', { method: 'POST' });
            const data = await resp.json();
            document.getElementById('status').innerHTML = '⏸️ ' + data.msg;
        }
        async function resumeAll() {
            const resp = await fetch('/api/resume', { method: 'POST' });
            const data = await resp.json();
            document.getElementById('status').innerHTML = '▶️ ' + data.msg;
        }
    </script>
</body>
</html>"""

CSS_STYLE = """body {
    font-family: system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
    background: #f5f5f5;
    margin: 0;
    padding: 20px;
}
.container {
    max-width: 600px;
    margin: 0 auto;
    background: white;
    border-radius: 28px;
    box-shadow: 0 8px 20px rgba(0,0,0,0.1);
    padding: 24px 20px 32px;
}
h1 {
    font-size: 1.8rem;
    text-align: center;
    color: #1e3c72;
    margin-bottom: 28px;
}
label {
    font-weight: 600;
    display: block;
    margin-top: 16px;
    margin-bottom: 6px;
    color: #2c3e50;
}
input, select {
    width: 100%;
    padding: 12px 14px;
    font-size: 16px;
    border: 1px solid #ccc;
    border-radius: 14px;
    box-sizing: border-box;
    background: #fff;
}
button {
    margin-top: 24px;
    width: 100%;
    background: #1e88e5;
    color: white;
    border: none;
    padding: 14px;
    font-size: 18px;
    font-weight: bold;
    border-radius: 40px;
    cursor: pointer;
    transition: 0.2s;
}
button:hover {
    background: #0b5e8a;
}
.controls {
    display: flex;
    gap: 10px;
    margin-top: 16px;
}
.controls button {
    flex: 1;
    margin-top: 0;
    font-size: 14px;
    padding: 10px;
}
#status {
    margin-top: 24px;
    background: #e9ecef;
    padding: 14px;
    border-radius: 20px;
    font-size: 14px;
    color: #1e466e;
    text-align: center;
}"""