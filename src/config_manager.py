import json
import os
from pydantic import BaseModel, Field
from typing import List, Dict, Optional

CONFIG_FILE = os.environ.get("CONFIG_FILE", "config/config.json")

class MiddlewareRule(BaseModel):
    id: str
    pattern: str
    response: str

class OmpConfig(BaseModel):
    host_info: str = ""
    provider: str = "OpenAI"
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    api_keys: Dict[str, str] = Field(default_factory=dict)
    tool_extensions: List[str] = Field(default_factory=list)
    local_mounts: List[str] = Field(default_factory=list)
    # Runtime behavior
    thinking_level: str = "auto"
    system_prompt: str = ""
    append_system_prompt: str = ""
    # Multi-model roles
    smol_model: str = ""
    slow_model: str = ""
    plan_model: str = ""
    # Tooling and approvals
    tools_filter: str = ""
    auto_approve: bool = False
    approval_mode: str = ""
    disable_lsp: bool = False
    disable_pty: bool = False
    no_session: bool = False
    hide_thinking: bool = False
    advisor: bool = False
    fast_mode: bool = False
    auto_compaction: bool = True
    # OMP platform authentication
    omp_auth_json: str = ""
    omp_auth_json_file: str = ""

class AppConfig(BaseModel):
    omp: OmpConfig = Field(default_factory=OmpConfig)
    middleware_rules: List[MiddlewareRule] = Field(default_factory=list)

class ConfigManager:
    def __init__(self, config_path: str = CONFIG_FILE):
        self.config_path = config_path
        self.config = self.load_config()

    def load_config(self) -> AppConfig:
        if os.path.exists(self.config_path):
            with open(self.config_path, "r") as f:
                try:
                    data = json.load(f)
                    return AppConfig(**data)
                except Exception as e:
                    print(f"Error loading config: {e}")
        return AppConfig()

    def save_config(self):
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, "w") as f:
            json.dump(self.config.model_dump(), f, indent=2)

    def get_config(self) -> AppConfig:
        return self.config
