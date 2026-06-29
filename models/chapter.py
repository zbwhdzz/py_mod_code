from dataclasses import dataclass, asdict
from typing import Dict, Any

@dataclass(frozen=True)
class Chapter:
    index: int
    title: str
    url: str
    def __post_init__(self):
        url = self.url
        if url.startswith("//"):
            url = f"http:{url}"
        elif not url.startswith(("http://", "https://")):
            url = f"http://{url}"
        if url != self.url:
            object.__setattr__(self, "url", url)
    def to_dict(self):
        return asdict(self)
    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Chapter":
        return Chapter(**data)