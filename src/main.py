import asyncio
import logging
import uuid
import uvicorn
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config_manager import ConfigManager, MiddlewareRule
from .middleware import Middleware
from .omp_subprocess import OmpSubprocess
from .wyoming_server import run_wyoming_server

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

config_manager = ConfigManager()
omp_subprocess = OmpSubprocess(config_manager)
middleware = Middleware(config_manager)

wyoming_task: Optional[asyncio.Task] = None


def _log_task_error(task: asyncio.Task) -> None:
    """Callback to observe Wyoming server task errors."""
    if task.cancelled():
        logger.info("Wyoming server task cancelled")
    elif task.exception() is not None:
        logger.error(f"Wyoming server task failed: {task.exception()}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting Wyoming Server...")
    global wyoming_task
    # Run the Wyoming server in the background
    wyoming_task = asyncio.create_task(run_wyoming_server("0.0.0.0", 10300, middleware, omp_subprocess))
    wyoming_task.add_done_callback(_log_task_error)
    yield
    # Shutdown
    if wyoming_task:
        wyoming_task.cancel()
    if omp_subprocess.process:
        omp_subprocess.process.terminate()

app = FastAPI(lifespan=lifespan)

# Setup Templates
import os
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    config = config_manager.get_config()
    return templates.TemplateResponse(request=request, name="index.html", context={"config": config})

@app.post("/omp/update")
async def update_omp(
    request: Request,
    provider: str = Form("OpenAI"),
    api_key: str = Form(""),
    base_url: str = Form(""),
    model: str = Form(""),
    host_info: str = Form(""),
    tool_extensions: str = Form(""),
    local_mounts: str = Form(""),
    api_keys: str = Form(""),
    # Runtime behavior
    thinking_level: str = Form("auto"),
    system_prompt: str = Form(""),
    append_system_prompt: str = Form(""),
    # Multi-model roles
    smol_model: str = Form(""),
    slow_model: str = Form(""),
    plan_model: str = Form(""),
    # Tooling and approvals
    tools_filter: str = Form(""),
    auto_approve: str = Form("on"),  # Checkbox sends "on" when checked
    approval_mode: str = Form(""),
    disable_lsp: str = Form("on"),
    disable_pty: str = Form("on"),
    no_session: str = Form("on"),
    hide_thinking: str = Form("on"),
    advisor: str = Form("on"),
    # Advanced
    fast_mode: str = Form("on"),
    auto_compaction: str = Form("on"),
):
    config = config_manager.get_config()

    config.omp.provider = provider
    config.omp.api_key = api_key
    config.omp.base_url = base_url
    config.omp.model = model
    config.omp.host_info = host_info

    # Process comma separated lists
    config.omp.tool_extensions = [x.strip() for x in tool_extensions.split(",") if x.strip()]
    config.omp.local_mounts = [x.strip() for x in local_mounts.split(",") if x.strip()]

    # Process extra environment variables
    keys_dict = {}
    for line in api_keys.split("\n"):
        line = line.strip()
        if "=" in line:
            k, v = line.split("=", 1)
            keys_dict[k.strip()] = v.strip()
    config.omp.api_keys = keys_dict

    # Runtime behavior
    config.omp.thinking_level = thinking_level
    config.omp.system_prompt = system_prompt
    config.omp.append_system_prompt = append_system_prompt

    # Multi-model roles
    config.omp.smol_model = smol_model
    config.omp.slow_model = slow_model
    config.omp.plan_model = plan_model

    # Tooling and approvals
    config.omp.tools_filter = tools_filter
    config.omp.auto_approve = auto_approve == "on"
    config.omp.approval_mode = approval_mode
    config.omp.disable_lsp = disable_lsp == "on"
    config.omp.disable_pty = disable_pty == "on"
    config.omp.no_session = no_session == "on"
    config.omp.hide_thinking = hide_thinking == "on"
    config.omp.advisor = advisor == "on"

    # Advanced
    config.omp.fast_mode = fast_mode == "on"
    config.omp.auto_compaction = auto_compaction == "on"

    config_manager.save_config()

    # Restart OMP subprocess with new config
    asyncio.create_task(omp_subprocess.restart())

    return RedirectResponse(url="/", status_code=303)

@app.post("/middleware/add")
async def add_middleware_rule(request: Request, pattern: str = Form(...), response: str = Form(...)):
    config = config_manager.get_config()
    rule = MiddlewareRule(id=str(uuid.uuid4()), pattern=pattern, response=response)
    config.middleware_rules.append(rule)
    config_manager.save_config()
    return RedirectResponse(url="/", status_code=303)

@app.post("/middleware/delete/{rule_id}")
async def delete_middleware_rule(request: Request, rule_id: str):
    config = config_manager.get_config()
    config.middleware_rules = [r for r in config.middleware_rules if r.id != rule_id]
    config_manager.save_config()
    return RedirectResponse(url="/", status_code=303)

from pydantic import BaseModel
class ChatRequest(BaseModel):
    message: str

@app.post("/api/chat")
async def chat_with_omp(chat_request: ChatRequest):
    # Pass through middleware first, just like the Wyoming bridge
    middleware_response = middleware.process_transcript(chat_request.message)
    if middleware_response:
        return {"reply": middleware_response, "source": "middleware"}
        
    omp_response = await omp_subprocess.query(chat_request.message)
    return {"reply": omp_response, "source": "omp"}

import urllib.request
import urllib.error
import json

@app.get("/api/models")
async def get_models(provider: str, base_url: str = "", api_key: str = ""):
    if provider == "Custom" and base_url:
        url = base_url.rstrip("/") + "/models"
    elif provider == "OpenAI":
        url = "https://api.openai.com/v1/models"
    else:
        # Standard way not easily supported for anthropic/gemini
        return {"models": []} 
        
    def fetch():
        req = urllib.request.Request(url)
        if api_key:
            req.add_header("Authorization", f"Bearer {api_key}")
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode())
                return {"models": [m["id"] for m in data.get("data", []) if "id" in m]}
        except Exception as e:
            return {"error": str(e)}

    return await asyncio.to_thread(fetch)

if __name__ == "__main__":
    uvicorn.run("src.main:app", host="0.0.0.0", port=8080, reload=False)
