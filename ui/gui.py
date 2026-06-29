import threading
import asyncio
import re
from pathlib import Path
from tkinter import *
from tkinter import ttk, scrolledtext, messagebox

from config.models import DownloadConfig
from config.observable import ObservableConfig
from downloader.novel_downloader import NovelDownloader
from parser.content_parser import GenericParser
from storage.storage_backend import StorageFactory
from utils.helpers import run_async

def start_gui(obs_config: ObservableConfig = None):
    root = Tk()
    root.title("智能小说爬虫 - v3.0")
    root.geometry("700x600")
    
    config = obs_config.config if obs_config else DownloadConfig()
    
    Label(root, text="📖 小说目录页 URL：").pack(pady=5)
    url_entry = Entry(root, width=70)
    url_entry.pack(pady=5)
    
    Label(root, text="📁 输出格式（txt/json/epub/pdf）：").pack(pady=5)
    fmt_entry = Entry(root, width=20)
    fmt_entry.insert(0, "txt")
    fmt_entry.pack(pady=5)
    
    status_frame = Frame(root)
    status_frame.pack(pady=5)
    status_label = Label(status_frame, text="状态: 就绪", fg="green")
    status_label.pack(side=LEFT, padx=5)
    pause_btn = Button(status_frame, text="⏸️ 暂停", state=DISABLED)
    pause_btn.pack(side=LEFT, padx=5)
    
    output_text = scrolledtext.ScrolledText(root, height=18, width=80)
    output_text.pack(pady=10)
    
    current_dl = [None]
    is_paused = [False]
    
    def log(msg):
        output_text.insert(END, msg + "\n")
        output_text.see(END)
        root.update_idletasks()
        
    async def progress_cb(current, total, chapter, status):
        root.after(0, lambda: status_label.config(
            text=f"进度: {current}/{total} ({current/total*100:.1f}%)"
        ))

    def toggle_pause():
        if current_dl[0] and not is_paused[0]:
            run_async(current_dl[0].pause())
            is_paused[0] = True
            pause_btn.config(text="▶️ 恢复")
            log("⏸️ 下载已暂停")
        elif current_dl[0] and is_paused[0]:
            run_async(current_dl[0].resume())
            is_paused[0] = False
            pause_btn.config(text="⏸️ 暂停")
            log("▶️ 下载已恢复")

    pause_btn.config(command=toggle_pause)

    def download():
        url = url_entry.get().strip()
        if not url:
            messagebox.showerror("错误", "URL 不能为空")
            return
        fmt = fmt_entry.get().strip().lower()
        if fmt not in ("txt", "json", "epub", "pdf"):
            messagebox.showerror("错误", "输出格式错误，必须为 txt/json/epub/pdf")
            return
        log(f"开始下载: {url}")
        status_label.config(text="状态: 下载中...", fg="blue")
        pause_btn.config(state=NORMAL)
        
        def worker():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            async def run():
                dl_config = DownloadConfig()
                if obs_config:
                    dl_config = obs_config.snapshot()
                dl_config.output_format = fmt
                dl_config.output_dir = "downloads"
                async with NovelDownloader(dl_config, GenericParser()) as dl:
                    current_dl[0] = dl
                    if obs_config:
                        dl.bind_observable_config(obs_config)
                    dl.add_progress_callback(progress_cb)
                    novel = await dl.fetch_catalog(url)
                    if not novel:
                        root.after(0, lambda: log("获取目录失败"))
                        return
                    root.after(0, lambda: log(f"《{novel.title}》共 {len(novel.chapters)} 章"))
                    results = await dl.download_chapters(1, len(novel.chapters))
                    success = sum(1 for r in results if r.success)
                    root.after(0, lambda: log(f"下载完成: 成功 {success}/{len(results)} 章"))
                    storage = StorageFactory.create(fmt, dl_config)
                    out_path = Path("downloads") / re.sub(r"[^\w\u4e00-\u9fff-]", "_", novel.title)
                    saved = await storage.save(novel, results, out_path, dl._images, dl._image_dir)
                    root.after(0, lambda: log(f"已保存: {saved}"))
                    root.after(0, lambda: status_label.config(text="状态: 完成", fg="green"))
                    root.after(0, lambda: pause_btn.config(state=DISABLED))
                    messagebox.showinfo("完成", f"下载完成，保存至 {saved}")
            loop.run_until_complete(run())
            loop.close()
        threading.Thread(target=worker, daemon=True).start()
        
    Button(root, text="开始下载", command=download).pack(pady=10)
    root.mainloop()