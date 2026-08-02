# Wyoming Conversation Agent Wrapper

Wyoming protocol bridge that connects Home Assistant voice assistants to [Oh My Pi (OMP)](https://github.com/oh-my-pi/pi-coding-agent) for LLM-powered conversation handling.

## Architecture

```
┌──────────────┐     Wyoming      ┌──────────────────┐     ACP/NDJSON     ┌──────────┐
│ Home Assistant │ ─────────────► │  Wyoming Bridge   │ ───────────────► │  OMP      │
│  (client)      │   :10300       │  (FastAPI +       │                   │  Agent    │
└──────────────┘                 │   Wyoming Server)  │                   └──────────┘
                                 │  :8080 (Web UI)   │
                                 └──────────────────┘
```

- **Wyoming port** (`:10300`) — TCP protocol for Home Assistant voice assistants
- **Web UI** (`:8080`) — FastAPI interface for configuration and direct chat

## Quick Start

### Docker

```bash
docker run -p 10300:10300 -p 8083:8080 \
  -v $(pwd)/config:/app/config \
  wyoming-omp-bridge
```

### From Source

```bash
pip install -r requirements.txt
python -m src.main
```

## Configuration

Copy `config/config.default.json` to `config/config.json` and edit:

```bash
cp config/config.default.json config/config.json
```

Key settings in `config/config.json`:

| Setting | Description |
|---|---|
| `omp.provider` | LLM provider (`OpenAI`, `Anthropic`, `Gemini`, `Custom`) |
| `omp.api_key` | API key for the provider |
| `omp.base_url` | Custom API endpoint (for local/Custom providers) |
| `omp.model` | Model to use for queries |
| `omp.system_prompt` | System prompt for OMP agent |
| `middleware_rules` | Regex patterns for query interception |

### Middleware Rules

Middleware rules intercept queries before they reach OMP:

```json
{
  "middleware_rules": [
    {
      "id": "123",
      "pattern": "what time is it",
      "response": "The current time is {{now}}"
    }
  ]
}
```

## Home Assistant Setup

1. Add the Wyoming integration in HA
2. Point to the bridge IP on port `10300`
3. The bridge advertises as an intent/handle service
4. Use the Wyoming conversation agent for text queries

## Wyoming Protocol

The bridge implements the [Wyoming protocol](https://github.com/rhasspy/wyoming):

- **Describe** — Reports available services (intent recognition, handling)
- **Transcript** — Receives transcribed text, returns LLM response
- **Recognize** — Receives intent recognition requests
- **Intent / Handled** — Returns response text

## Ports

| Port | Service | Protocol |
|---|---|---|
| `10300` | Wyoming protocol | TCP |
| `8080` | Web UI / REST API | HTTP |

## License

MIT
