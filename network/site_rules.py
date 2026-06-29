import json
import logging
from typing import Optional, List, Dict
from urllib.parse import urlparse
from config.models import SiteRule

logger = logging.getLogger(__name__)

class SiteRuleManager:
    def __init__(self):
        self._rules: Dict[str, SiteRule] = {}
        self._lock = asyncio.Lock()
        
    def load_rules(self, rules: List[SiteRule]):
        for rule in rules:
            self._rules[rule.domain] = rule
        logger.info(f"已加载 {len(rules)} 条站点规则")
        
    def load_from_file(self, filepath: str):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            rules = []
            for item in data:
                if HAS_PYDANTIC:
                    rules.append(SiteRule(**item))
                else:
                    rules.append(SiteRule(**item))
            self.load_rules(rules)
        except Exception as e:
            logger.error(f"加载站点规则失败: {e}")
            
    def get_rule(self, url: str) -> Optional[SiteRule]:
        hostname = urlparse(url).hostname
        if not hostname:
            return None
        if hostname in self._rules:
            return self._rules[hostname]
        if hostname.startswith('www.'):
            alt = hostname[4:]
            if alt in self._rules:
                return self._rules[alt]
        parts = hostname.split('.')
        for i in range(1, len(parts)):
            wildcard = '*.' + '.'.join(parts[i:])
            if wildcard in self._rules:
                return self._rules[wildcard]
        return None
        
    def add_rule(self, rule: SiteRule):
        self._rules[rule.domain] = rule
        
    def remove_rule(self, domain: str):
        self._rules.pop(domain, None)
        
    def list_rules(self) -> List[SiteRule]:
        return list(self._rules.values())

try:
    from pydantic import BaseModel
    HAS_PYDANTIC = True
except ImportError:
    HAS_PYDANTIC = False