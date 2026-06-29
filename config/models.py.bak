from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Union, List, Dict, Tuple, Any
from pathlib import Path
from .enums import *
import re
import os

try:
    from pydantic import BaseModel, Field, validator
    HAS_PYDANTIC = True
except ImportError:
    HAS_PYDANTIC = False
    BaseModel = object

# ---------- 定义 ----------
if HAS_PYDANTIC:
    class ProxyConfig(BaseModel):
        single: Optional[str] = None
        rotation: Optional[Union[str, List[str]]] = None
        check_url: str = "http://httpbin.org/ip"
        max_failures: int = Field(default=3, ge=1, le=10)
        strategy: ProxyStrategy = ProxyStrategy.ROUND_ROBIN
        health_check_interval: int = Field(default=300, ge=60, le=3600)
        proxy_pool_api: Optional[str] = None
        proxy_pool_key: Optional[str] = None

    class BrowserConfig(BaseModel):
        enable: bool = False
        headless: bool = True
        stealth_mode: bool = True
        viewport_width: int = 1920
        viewport_height: int = 1080
        locale: str = "zh-CN"
        timezone: str = "Asia/Shanghai"
        device_scale_factor: float = 1.0
        has_touch: bool = False
        wait_for_selector: Optional[str] = None
        auto_render_threshold: int = 50
        render_timeout: int = 30

    class CaptchaConfig(BaseModel):
        enabled: bool = False
        service: str = "2captcha"
        api_key: Optional[str] = None
        timeout: int = 120

    class SiteRule(BaseModel):
        domain: str
        chapter_selector: Optional[str] = None
        content_selector: Optional[str] = None
        title_selector: Optional[str] = None
        next_page_selector: Optional[str] = None
        font_decrypt: bool = False
        font_mapping: Optional[Dict[str, str]] = None
        need_render: bool = False
        render_wait: Optional[str] = None
        anti_crawl_preset: Optional[str] = None

    class DownloadConfig(BaseModel):
        max_workers: int = Field(default=10, ge=1, le=50)
        batch_size: int = Field(default=50, ge=1, le=200)
        retry_times: int = Field(default=3, ge=0, le=10)
        timeout: int = Field(default=15, ge=1, le=300)
        chapter_threshold: int = Field(default=50, ge=10, le=500)
        max_pages: int = Field(default=100, ge=1, le=1000)
        device: DeviceType = DeviceType.PC
        custom_headers: Dict[str, str] = Field(default_factory=dict)
        cookie_file: Optional[str] = None
        force_encoding: Optional[str] = None
        verify_ssl: bool = True
        save_interval: int = Field(default=5, ge=1, le=100)
        random_delay_range: Tuple[float, float] = (0.25, 0.75)
        rate_limit: Union[bool, str] = False
        impersonate: str = "chrome120"
        referer_policy: RefererPolicy = RefererPolicy.AUTO
        auto_downgrade: bool = True
        downgrade_sleep: int = Field(default=60, ge=0, le=300)
        add_random_param: bool = False
        respect_robots: bool = False
        proxy_config: ProxyConfig = Field(default_factory=ProxyConfig)
        browser_config: BrowserConfig = Field(default_factory=BrowserConfig)
        captcha_config: CaptchaConfig = Field(default_factory=CaptchaConfig)
        output_format: OutputFormat = OutputFormat.TXT
        output_dir: str = "."
        download_images: bool = False
        deduplicate: bool = False
        chapter_filter: Optional[str] = None
        cache_chapters: bool = True
        error_report: bool = True
        chapter_selector: Optional[str] = None
        catalog_mode: CatalogMode = CatalogMode.AUTO
        batch_mode: BatchMode = BatchMode.AUTO
        max_concurrent_novels: int = Field(default=3, ge=1, le=10)
        state_file: Optional[str] = None
        retry_failed: bool = False
        dynamic_workers: bool = False
        verbose: bool = False
        config_file: Optional[str] = None
        use_curl_cffi: bool = True
        auto_update: bool = False
        update_interval_hours: int = 24
        subscribe_file: Optional[str] = None
        enable_notification: bool = True
        search_enabled: bool = True
        search_sources: List[str] = Field(default_factory=lambda: ["biquge", "zwdu", "xiaoshuo"])
        custom_rules_file: Optional[str] = None
        pdf_font_path: Optional[str] = None
        allowed_domains: Optional[List[str]] = Field(default=None)
        download_timeout: int = Field(default=0, ge=0)
        anti_crawl_enabled: bool = True
        anti_crawl_failure_threshold: int = Field(default=3, ge=1, le=10)
        anti_crawl_escalation: bool = True
        anti_crawl_downgrade_after: int = Field(default=10, ge=1)
        site_rules: List[SiteRule] = Field(default_factory=list)
        log_dir: str = "logs"
        log_requests: bool = True
        web_auth_username: Optional[str] = None
        web_auth_password: Optional[str] = None
        web_rate_limit: int = Field(default=20, ge=5)

        @validator("random_delay_range")
        def validate_delay_range(cls, v):
            if len(v) != 2 or v[0] < 0 or v[1] < v[0]:
                raise ValueError("延迟范围无效")
            return v

        @validator("chapter_filter")
        def validate_regex(cls, v):
            if v is not None:
                try:
                    re.compile(v)
                except re.error as e:
                    raise ValueError(f"无效的正则表达式: {e}")
            return v
            
        @validator("custom_rules_file")
        def validate_rules_file(cls, v):
            if v and not os.path.exists(v):
                raise ValueError(f"规则文件不存在: {v}")
            return v

else:
    @dataclass
    class ProxyConfig:
        single: Optional[str] = None
        rotation: Optional[Union[str, List[str]]] = None
        check_url: str = "http://httpbin.org/ip"
        max_failures: int = 3
        strategy: ProxyStrategy = ProxyStrategy.ROUND_ROBIN
        health_check_interval: int = 300
        proxy_pool_api: Optional[str] = None
        proxy_pool_key: Optional[str] = None

    @dataclass
    class BrowserConfig:
        enable: bool = False
        headless: bool = True
        stealth_mode: bool = True
        viewport_width: int = 1920
        viewport_height: int = 1080
        locale: str = "zh-CN"
        timezone: str = "Asia/Shanghai"
        device_scale_factor: float = 1.0
        has_touch: bool = False
        wait_for_selector: Optional[str] = None
        auto_render_threshold: int = 50
        render_timeout: int = 30

    @dataclass
    class CaptchaConfig:
        enabled: bool = False
        service: str = "2captcha"
        api_key: Optional[str] = None
        timeout: int = 120

    @dataclass
    class SiteRule:
        domain: str
        chapter_selector: Optional[str] = None
        content_selector: Optional[str] = None
        title_selector: Optional[str] = None
        next_page_selector: Optional[str] = None
        font_decrypt: bool = False
        font_mapping: Optional[Dict[str, str]] = None
        need_render: bool = False
        render_wait: Optional[str] = None
        anti_crawl_preset: Optional[str] = None

    @dataclass
    class DownloadConfig:
        max_workers: int = 10
        batch_size: int = 50
        retry_times: int = 3
        timeout: int = 15
        chapter_threshold: int = 50
        max_pages: int = 100
        device: DeviceType = DeviceType.PC
        custom_headers: Dict[str, str] = field(default_factory=dict)
        cookie_file: Optional[str] = None
        force_encoding: Optional[str] = None
        verify_ssl: bool = True
        save_interval: int = 5
        random_delay_range: Tuple[float, float] = (0.25, 0.75)
        rate_limit: Union[bool, str] = False
        impersonate: str = "chrome120"
        referer_policy: RefererPolicy = RefererPolicy.AUTO
        auto_downgrade: bool = True
        downgrade_sleep: int = 60
        add_random_param: bool = False
        respect_robots: bool = False
        proxy_config: ProxyConfig = field(default_factory=ProxyConfig)
        browser_config: BrowserConfig = field(default_factory=BrowserConfig)
        captcha_config: CaptchaConfig = field(default_factory=CaptchaConfig)
        output_format: OutputFormat = OutputFormat.TXT
        output_dir: str = "."
        download_images: bool = False
        deduplicate: bool = False
        chapter_filter: Optional[str] = None
        cache_chapters: bool = True
        error_report: bool = True
        chapter_selector: Optional[str] = None
        catalog_mode: CatalogMode = CatalogMode.AUTO
        batch_mode: BatchMode = BatchMode.AUTO
        max_concurrent_novels: int = 3
        state_file: Optional[str] = None
        retry_failed: bool = False
        dynamic_workers: bool = False
        verbose: bool = False
        config_file: Optional[str] = None
        use_curl_cffi: bool = True
        auto_update: bool = False
        update_interval_hours: int = 24
        subscribe_file: Optional[str] = None
        enable_notification: bool = True
        search_enabled: bool = True
        search_sources: List[str] = field(default_factory=lambda: ["biquge", "zwdu", "xiaoshuo"])
        custom_rules_file: Optional[str] = None
        pdf_font_path: Optional[str] = None
        allowed_domains: Optional[List[str]] = None
        download_timeout: int = 0
        anti_crawl_enabled: bool = True
        anti_crawl_failure_threshold: int = 3
        anti_crawl_escalation: bool = True
        anti_crawl_downgrade_after: int = 10
        site_rules: List[SiteRule] = field(default_factory=list)
        log_dir: str = "logs"
        log_requests: bool = True
        web_auth_username: Optional[str] = None
        web_auth_password: Optional[str] = None
        web_rate_limit: int = 20