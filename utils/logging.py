import logging
import sys
import time
from pathlib import Path
from logging.handlers import RotatingFileHandler

class ColoredFormatter(logging.Formatter):
    COLORS = {
        'DEBUG': '\033[36m',
        'INFO': '\033[32m',
        'WARNING': '\033[33m',
        'ERROR': '\033[31m',
        'CRITICAL': '\033[35m',
        'RESET': '\033[0m'
    }
    def format(self, record):
        color = self.COLORS.get(record.levelname, self.COLORS['RESET'])
        reset = self.COLORS['RESET']
        record.levelname = f"{color}{record.levelname}{reset}"
        return super().format(record)

def setup_logging(log_dir: str = "logs", verbose: bool = False):
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console_fmt = ColoredFormatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%H:%M:%S"
    )
    console.setFormatter(console_fmt)
    root_logger.addHandler(console)
    log_file = log_path / f"novel_spider_{time.strftime('%Y%m%d_%H%M%S')}.log"
    file_handler = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_fmt = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s"
    )
    file_handler.setFormatter(file_fmt)
    root_logger.addHandler(file_handler)
    request_log = log_path / f"requests_{time.strftime('%Y%m%d_%H%M%S')}.log"
    req_handler = RotatingFileHandler(request_log, maxBytes=5*1024*1024, backupCount=3, encoding='utf-8')
    req_handler.setLevel(logging.DEBUG)
    req_fmt = logging.Formatter("%(asctime)s | %(message)s")
    req_handler.setFormatter(req_fmt)
    req_logger = logging.getLogger("novel_spider.requests")
    req_logger.setLevel(logging.DEBUG)
    req_logger.addHandler(req_handler)
    req_logger.propagate = False
    return root_logger, req_logger

logger = logging.getLogger(__name__)
req_logger = logging.getLogger("novel_spider.requests")