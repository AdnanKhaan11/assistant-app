# IT Support Assistant (production-style)

Pure-Python AI agent for IT support - from Chapter 9 of *Building Production AI Agents for the Web*.
Any LLM (OpenAI, Groq, Gemini, Anthropic, Ollama, OpenRouter, DeepSeek) + full OpenTelemetry observability.

## Quick start
```bash
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                      # set LLM_PROVIDER + the matching API key
pytest                                                    # telemetry + tool-governance tests pass immediately
make console                                              # run, telemetry printed to terminal (no docker)
# or the full stack:
make obs-up && make run                                   # Grafana :3000  Jaeger :16686  Prometheus :9090
```
Open http://localhost:8000 . Complete the TODOs first (see "Build order").

## Build order (each file has numbered YOUR TASKS)
1. `utils/config.py`, `utils/json_utils.py`
2. `llm/openai_compat.py`, then `llm/anthropic_provider.py`   (smoke test with one provider)
3. `memory/short_term.py`, `memory/long_term.py`
4. `tools/issue_lookup.py`, `network_check.py`, `network_action.py`
5. `services/classifier.py` -> `summarizer.py` -> `advisor.py`
6. `assistant.py`, then routes in `main.py`
7. `frontend/app.js`, then write `tests/test_services.py` and `tests/test_assistant_flow.py`

## Architecture
```
frontend -> FastAPI (main.py) -> AssistantService (assistant.py)
                                   |- ClassifierService -> SummarizerService -> AdvisorService   (LLM steps)
                                   |- ToolRegistry: issue_lookup | network_check | network_action(confirm!)
                                   |- ShortTermMemory (per ticket) + LongTermMemory (SQLite)
                                   '- LLM stack: Retrying( Instrumented( OpenAICompat | Anthropic ) )
```
Safety rules enforced in CODE: ACTION tools never run without confirmation; tools time out; tool crashes become
structured errors; the classifier degrades to UNKNOWN instead of failing; actions are idempotent.

## Observability in 5 minutes
| Signal  | Question it answers                         | Where to look                      |
|---------|---------------------------------------------|------------------------------------|
| Trace   | "Why was THIS request slow / failing?"      | Jaeger  http://localhost:16686     |
| Metric  | "Is the system healthy over time?"          | Grafana http://localhost:3000      |
| Log     | "What did the code say?" (trace_id linked)  | Grafana -> Explore -> Loki         |

One request produces this trace tree:
```
POST /api/chat                          <- FastAPI auto-instrumentation
 '- assistant.handle_message
     |- service.classify -> chat <model> -> POST https://api...   <- LLM span -> httpx span
     |- tool.network_check
     |- service.summarize -> chat <model>
     '- service.advise   -> chat <model>
```
Debug workflow: user reports a problem -> they copy the **trace id** shown in the UI -> paste it in Jaeger.

Metrics emitted: `gen_ai.client.operation.duration`, `gen_ai.client.token.usage`, `assistant.llm.retries`,
`assistant.requests`, `assistant.request.duration`, `assistant.classifications`, `assistant.tool.calls`,
`assistant.tool.duration`, `assistant.confirmations`, `assistant.active_tickets`.
Alerts live in `deploy/alerts.yml` (LLM error rate, p95 latency, tool failures).

### Reuse telemetry in another app
1. Copy `backend/telemetry/` (it imports nothing from this project) and `pip install` the 5 `opentelemetry-*` packages.
2. Call `init_telemetry()` at startup, `shutdown_telemetry()` at exit, `instrument_fastapi(app)`.
3. Point `OTEL_EXPORTER_OTLP_ENDPOINT` at any OTLP backend (this Collector, Grafana Cloud, Datadog, Honeycomb...).

### Production checklist
- [ ] `TELEMETRY_CAPTURE_CONTENT=false` (prompts can contain personal data) - already the default
- [ ] Lower `TELEMETRY_SAMPLE_RATIO` (e.g. 0.1) under heavy traffic; keep error traces via Collector tail-sampling
- [ ] Never use ticket/user ids as metric attributes (cardinality explosion) - they belong on spans/logs only
- [ ] Put Prometheus/Grafana/Collector behind auth and a private network; change the Grafana admin password
- [ ] Add alert routing (Alertmanager -> Slack/PagerDuty) for `deploy/alerts.yml`
- [ ] Run the app with several workers behind a reverse proxy; move short-term memory to Redis when you scale out
- [ ] Keep API keys in a secret manager, not in `.env` files on servers
