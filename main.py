import sys
import asyncio
import argparse
from pathlib import Path
from utils.logging import setup_logging
from config.models import DownloadConfig
from ui.interactive import InteractiveUI
from web.server import start_web_server
from downloader.novel_downloader import NovelDownloader
from parser.content_parser import GenericParser
from storage.storage_backend import StorageFactory
from models.history import HistoryManager
import re

def main():
    parser = argparse.ArgumentParser(description="智能小说爬虫 - 企业级重构版 v3.0")
    parser.add_argument("-u", "--url", help="目录页URL")
    parser.add_argument("-o", "--output", default=".", help="输出目录")
    parser.add_argument("-f", "--format", choices=["txt","json","epub","pdf"], default="txt", help="输出格式")
    parser.add_argument("-w", "--workers", type=int, default=10, help="并发数")
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--end", type=int)
    parser.add_argument("-i", "--interactive", action="store_true", help="交互模式")
    parser.add_argument("-c", "--config", help="配置文件")
    parser.add_argument("--web", action="store_true", help="启动网页服务器")
    parser.add_argument("--port", type=int, default=8000, help="Web服务器端口")
    parser.add_argument("--allowed-domains", nargs="*", help="Web服务器允许的域名白名单")
    parser.add_argument("--web-auth-username", help="Web认证用户名")
    parser.add_argument("--web-auth-password", help="Web认证密码")
    parser.add_argument("--verbose", action="store_true", help="详细日志")
    args = parser.parse_args()

    setup_logging(verbose=args.verbose)

    if args.web:
        start_web_server(port=args.port, allowed_domains=args.allowed_domains,
                        username=args.web_auth_username, password=args.web_auth_password)
        return

    if args.interactive or not args.url:
        ui = InteractiveUI()
        ui.run()
        return

    config = DownloadConfig(output_dir=args.output, output_format=args.format, max_workers=args.workers)
    if args.config:
        try:
            import json
            with open(args.config, "r") as f:
                cfg = json.load(f)
            ui = InteractiveUI()
            ui._dict_to_obj(cfg, config)
        except Exception as e:
            print(f"加载配置文件失败: {e}")

    urls = [u.strip() for u in args.url.split(",") if u.strip()]
    async def run_single(url):
        async with NovelDownloader(config, GenericParser()) as dl:
            novel = await dl.fetch_catalog(url)
            if not novel:
                return
            start = max(1, args.start)
            end = min(args.end or len(novel.chapters), len(novel.chapters))
            results = await dl.download_chapters(start, end)
            storage = StorageFactory.create(args.format, config)
            out = Path(args.output) / re.sub(r"[^\w\u4e00-\u9fff-]", "_", novel.title)
            saved = await storage.save(novel, results, out)
            print(f"保存: {saved}")
            await HistoryManager.add_record(novel, results)

    async def run_multiple():
        sem = asyncio.Semaphore(config.max_concurrent_novels)
        async def dl_one(url):
            async with sem, NovelDownloader(config, GenericParser()) as dl:
                novel = await dl.fetch_catalog(url)
                if not novel:
                    return
                results = await dl.download_chapters(1, len(novel.chapters))
                storage = StorageFactory.create(args.format, config)
                out = Path(args.output) / re.sub(r"[^\w\u4e00-\u9fff-]", "_", novel.title)
                saved = await storage.save(novel, results, out)
                print(f"保存: {saved}")
                await HistoryManager.add_record(novel, results)
        await asyncio.gather(*(dl_one(u) for u in urls))

    asyncio.run(run_single(urls[0]) if len(urls) == 1 else run_multiple())

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⏹️ 用户中断")
        sys.exit(0)
    except Exception as e:
        import traceback
        traceback.print_exc()
        sys.exit(0)