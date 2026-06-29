from dataclasses import dataclass, field
import time
from typing import Optional
from config.enums import DownloadStatus
from .chapter import Chapter

@dataclass
class DownloadResult:
    chapter: Chapter
    content: Optional[str] = None
    status: DownloadStatus = DownloadStatus.FAILED
    error: Optional[str] = None
    attempts: int = 0
    duration: float = 0.0
    anti_crawl_level: str = "normal"
    timestamp: float = field(default_factory=time.time)

    @property
    def success(self) -> bool:
        return self.status == DownloadStatus.SUCCESS