from dataclasses import dataclass, field
from typing import List, Dict, Any
from .chapter import Chapter

@dataclass
class Novel:
    title: str
    base_url: str
    chapters: List[Chapter] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    def to_dict(self):
        return {"title": self.title, "base_url": self.base_url,
                "chapters": [ch.to_dict() for ch in self.chapters], "metadata": self.metadata}
    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Novel":
        return Novel(title=data["title"], base_url=data["base_url"],
                     chapters=[Chapter.from_dict(ch) for ch in data["chapters"]],
                     metadata=data.get("metadata", {}))