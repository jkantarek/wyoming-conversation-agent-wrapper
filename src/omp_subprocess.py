import asyncio
import json
import logging
from typing import Optional, Dict, Any

from .config_manager import ConfigManager

logger = logging.getLogger(__name__)

class OmpSubprocess:
    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.process: Optional[asyncio.subprocess.Process] = None
        self._lock = asyncio.Lock()

    async def start(self):
        async with self._lock:
            await self._start_unsafe()

    async def _start_unsafe(self):
        if self.process and self.process.returncode is None:
            logger.info("Terminating existing OMP subprocess")
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("Killing OMP subprocess (did not terminate gracefully)")
                self.process.kill()
                await self.process.wait()

        config = self.config_manager.get_config().omp
        
        # Build command based on config
        cmd = ["omp", "--mode", "rpc"]
        
        import os
        env = os.environ.copy()
        
        # Inject standard LLM environment variables
        if config.provider == "Anthropic":
            env["ANTHROPIC_API_KEY"] = config.api_key
        elif config.provider == "Gemini":
            env["GEMINI_API_KEY"] = config.api_key
        else:  # OpenAI or Custom (llama.cpp/Ollama/LM Studio/vLLM)
            # omp resolves models via its catalog; local *.gguf models map to
            # provider "llama.cpp" which uses LLAMA_CPP_BASE_URL / LLAMA_CPP_API_KEY.
            # Set all known local-provider env vars so the subprocess works
            # regardless of how omp resolves the model.
            if config.base_url:
                env["OPENAI_BASE_URL"] = config.base_url
                env["OPENAI_API_BASE"] = config.base_url
                env["LLAMA_CPP_BASE_URL"] = config.base_url
                env["OLLAMA_BASE_URL"] = config.base_url
                env["LM_STUDIO_BASE_URL"] = config.base_url
                env["VLLM_BASE_URL"] = config.base_url
            # API key — omit if empty rather than injecting "sk-dummy"
            if config.api_key:
                env["OPENAI_API_KEY"] = config.api_key
                env["LLAMA_CPP_API_KEY"] = config.api_key
                env["OLLAMA_API_KEY"] = config.api_key
                env["LM_STUDIO_API_KEY"] = config.api_key
                env["VLLM_API_KEY"] = config.api_key

        if config.model:
            env["MODEL"] = config.model
            env["OPENAI_MODEL_NAME"] = config.model

        # Runtime behavior — CLI flags
        if config.thinking_level and config.thinking_level != "auto":
            cmd.extend(["--thinking", config.thinking_level])

        if config.system_prompt:
            cmd.extend(["--system-prompt", config.system_prompt])

        if config.append_system_prompt:
            cmd.extend(["--append-system-prompt", config.append_system_prompt])

        # Multi-model roles
        if config.smol_model:
            cmd.extend(["--smol", config.smol_model])
            env["PI_SMOL_MODEL"] = config.smol_model
        if config.slow_model:
            cmd.extend(["--slow", config.slow_model])
            env["PI_SLOW_MODEL"] = config.slow_model
        if config.plan_model:
            cmd.extend(["--plan", config.plan_model])
            env["PI_PLAN_MODEL"] = config.plan_model

        # Tooling and approvals
        if config.tools_filter:
            cmd.extend(["--tools", config.tools_filter])

        if config.auto_approve:
            cmd.append("--auto-approve")

        if config.approval_mode:
            cmd.extend(["--approval-mode", config.approval_mode])

        if config.disable_lsp:
            cmd.append("--no-lsp")
            env["PI_DISABLE_LSPMUX"] = "1"

        if config.disable_pty:
            cmd.append("--no-pty")
            env["PI_NO_PTY"] = "1"

        if config.no_session:
            cmd.append("--no-session")

        if config.hide_thinking:
            cmd.append("--hide-thinking")

        if config.advisor:
            cmd.append("--advisor")

        # OMP platform authentication
        if config.omp_auth_json:
            env["OMP_AGENT_AUTH_JSON"] = config.omp_auth_json
        if config.omp_auth_json_file:
            env["OMP_AGENT_AUTH_JSON_FILE"] = config.omp_auth_json_file

        # Inject extra env vars
        for k, v in config.api_keys.items():
            env[k] = v

        logger.info(f"Spawning OMP subprocess: {' '.join(cmd)}")
        try:
            self.process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env
            )
            logger.info("OMP subprocess spawned. Waiting for initialization...")
            
            # Wait for agent to become idle before accepting queries
            try:
                while True:
                    line = await asyncio.wait_for(self.process.stdout.readline(), timeout=60.0)
                    if not line:
                        raise Exception("OMP subprocess closed stdout during initialization")
                    
                    line_str = line.decode("utf-8").strip()
                    logger.info(f"OMP INIT: {line_str}")
                    
                    try:
                        data = json.loads(line_str)
                        # OMP emits "ready" as init signal; accept that or agent_state:idle
                        if data.get("type") == "ready":
                            logger.info("OMP subprocess initialized (ready received).")
                            # Post-init setup — send RPC commands now that agent is ready
                            await self._post_init_setup(config)
                            break
                        if data.get("type") == "agent_state" and data.get("state") == "idle":
                            logger.info("OMP subprocess is fully initialized and idle.")
                            await self._post_init_setup(config)
                            break
                    except json.JSONDecodeError:
                        pass
            except asyncio.TimeoutError:
                logger.error("Timeout waiting for OMP to initialize.")
                self.process.kill()
                self.process = None
                raise Exception("Timeout waiting for OMP to initialize. See server logs.")

            # Start background task to consume stderr so it doesn't block
            asyncio.create_task(self._consume_stderr())
        except FileNotFoundError:
            logger.error("omp executable not found. Make sure it is installed and in PATH.")
            self.process = None
            raise
        except Exception as e:
            logger.error(f"Failed to spawn OMP subprocess: {e}")
            self.process = None
            raise

    async def _consume_stderr(self):
        if not self.process or not self.process.stderr:
            return
        while True:
            line = await self.process.stderr.readline()
            if not line:
                break
            logger.debug(f"OMP STDERR: {line.decode().rstrip()}")

    async def _post_init_setup(self, config):
        """Send RPC commands after init for settings that require agent to be running."""
        if not self.process:
            return

        commands = []
        if config.fast_mode:
            commands.append({"type": "set_fast_mode", "enabled": True})
        if not config.auto_compaction:
            commands.append({"type": "set_auto_compaction", "enabled": False})

        if not commands:
            return

        for cmd in commands:
            cmd_str = json.dumps(cmd) + "\n"
            try:
                self.process.stdin.write(cmd_str.encode("utf-8"))
                await self.process.stdin.drain()
                # Wait for response
                resp = await asyncio.wait_for(self.process.stdout.readline(), timeout=10.0)
                resp_str = resp.decode("utf-8").strip() if resp else ""
                try:
                    resp_data = json.loads(resp_str)
                    if resp_data.get("type") == "response":
                        logger.info(f"Post-init {cmd.get('type')}: {resp_data.get('success', '?')}")
                except json.JSONDecodeError:
                    pass
            except Exception as e:
                logger.warning(f"Failed to send {cmd.get('type')}: {e}")

    async def restart(self):
        logger.info("Restarting OMP subprocess with new config")
        await self.start()

    async def query(self, text: str) -> str:
        if not self.process or self.process.returncode is not None:
            logger.info("OMP process is not running, attempting to start")
            try:
                await asyncio.wait_for(self._start_unsafe(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.error("OMP subprocess did not become ready in time.")
                return "OMP is not ready. Ensure OMP_AGENT_AUTH_JSON or OMP_AGENT_AUTH_JSON_FILE is configured."
            except Exception as e:
                logger.error(f"Failed to start OMP subprocess: {e}")
                return "OMP failed to start. Check server logs."

        # Guard: if start failed (raised + set self.process = None), bail
        if not self.process:
            logger.error("Failed to start OMP subprocess; cannot query")
            return "OMP failed to start. Check server logs."

        # Format ACP / NDJSON payload for OMP (message field per rpc-types.ts)
        payload = {"type": "prompt", "message": text}
        payload_str = json.dumps(payload) + "\n"

        async with self._lock:
            try:
                # Write prompt
                self.process.stdin.write(payload_str.encode("utf-8"))
                await self.process.stdin.drain()

                # Read response stream until agent_end
                while True:
                    try:
                        response_line = await asyncio.wait_for(self.process.stdout.readline(), timeout=120.0)
                    except asyncio.TimeoutError:
                        return "Error: Timed out waiting for OMP response."
                        
                    if not response_line:
                        return "Error: OMP subprocess closed stdout unexpectedly."
                    
                    line_str = response_line.decode("utf-8").strip()
                    logger.info(f"OMP RECV: {line_str}")
                    
                    try:
                        data = json.loads(line_str)
                        
                        # Return the error if API key failed or other hard error
                        if data.get("type") == "message_end" and "message" in data:
                            msg = data["message"]
                            if msg.get("role") == "assistant" and msg.get("errorMessage"):
                                return f"LLM Error: {msg['errorMessage']}"

                        # Wait for the agent to finish its turn
                        if data.get("type") == "agent_end":
                            messages = data.get("messages", [])
                            if not messages:
                                return "No response generated."
                                
                            # Find the last assistant message
                            for msg in reversed(messages):
                                if msg.get("role") == "assistant":
                                    content_blocks = msg.get("content", [])
                                    text_parts = []
                                    for block in content_blocks:
                                        if block.get("type") == "text":
                                            text_parts.append(block.get("text", ""))
                                    
                                    if text_parts:
                                        return "".join(text_parts)
                                    
                            return "Agent completed task without a text response."
                    except json.JSONDecodeError:
                        continue
                        
            except Exception as e:
                logger.error(f"Error querying OMP: {e}")
                self.process = None
                return f"Internal Error: {str(e)}"
