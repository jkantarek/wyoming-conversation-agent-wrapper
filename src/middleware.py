import re
from typing import Optional
from .config_manager import ConfigManager

class Middleware:
    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager

    def process_transcript(self, text: str) -> Optional[str]:
        """
        Intercepts a fast-path query.
        Returns the generated response if matched, otherwise None.
        """
        config = self.config_manager.get_config()
        text_lower = text.lower().strip()
        
        for rule in config.middleware_rules:
            # We can use simple substring match or regex
            # Here we try a simple regex match
            try:
                if re.search(rule.pattern, text_lower, re.IGNORECASE):
                    return rule.response
            except re.error:
                # Fallback to simple string match if invalid regex
                if rule.pattern.lower() in text_lower:
                    return rule.response
                    
        return None
