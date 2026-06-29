import os
import re
from .models import DownloadConfig
from typing import List

def validate_config(config: DownloadConfig) -> List[str]:
    errors = []
    if hasattr(config, 'max_workers') and not (1 <= config.max_workers <= 50):
        errors.append(f"max_workers 必须在 1-50 之间，当前: {config.max_workers}")
    if hasattr(config, 'batch_size') and not (1 <= config.batch_size <= 200):
        errors.append(f"batch_size 必须在 1-200 之间，当前: {config.batch_size}")
    if hasattr(config, 'retry_times') and not (0 <= config.retry_times <= 10):
        errors.append(f"retry_times 必须在 0-10 之间，当前: {config.retry_times}")
    if hasattr(config, 'timeout') and not (1 <= config.timeout <= 300):
        errors.append(f"timeout 必须在 1-300 之间，当前: {config.timeout}")
    if hasattr(config, 'random_delay_range'):
        r = config.random_delay_range
        if len(r) != 2 or r[0] < 0 or r[1] < r[0]:
            errors.append(f"random_delay_range 格式无效: {r}")
    if hasattr(config, 'chapter_filter') and config.chapter_filter:
        try:
            re.compile(config.chapter_filter)
        except re.error as e:
            errors.append(f"chapter_filter 正则表达式无效: {e}")
    if hasattr(config, 'proxy_config') and config.proxy_config:
        proxy = config.proxy_config
        if hasattr(proxy, 'max_failures') and not (1 <= proxy.max_failures <= 10):
            errors.append(f"proxy.max_failures 必须在 1-10 之间")
        if hasattr(proxy, 'health_check_interval') and not (60 <= proxy.health_check_interval <= 3600):
            errors.append(f"proxy.health_check_interval 必须在 60-3600 之间")
    if hasattr(config, 'save_interval') and not (1 <= config.save_interval <= 100):
        errors.append(f"save_interval 必须在 1-100 之间，当前: {config.save_interval}")
    if hasattr(config, 'downgrade_sleep') and not (0 <= config.downgrade_sleep <= 300):
        errors.append(f"downgrade_sleep 必须在 0-300 之间，当前: {config.downgrade_sleep}")
    if hasattr(config, 'max_concurrent_novels') and not (1 <= config.max_concurrent_novels <= 10):
        errors.append(f"max_concurrent_novels 必须在 1-10 之间，当前: {config.max_concurrent_novels}")
    if hasattr(config, 'cookie_file') and config.cookie_file:
        if not os.path.exists(config.cookie_file):
            errors.append(f"Cookie 文件不存在: {config.cookie_file}")
        elif not os.access(config.cookie_file, os.R_OK):
            errors.append(f"Cookie 文件不可读: {config.cookie_file}")
    if hasattr(config, 'pdf_font_path') and config.pdf_font_path:
        if not os.path.exists(config.pdf_font_path):
            errors.append(f"PDF 字体文件不存在: {config.pdf_font_path}")
    return errors