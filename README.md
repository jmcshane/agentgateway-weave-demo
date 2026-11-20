# agentgateway → CoreWeave Inference → W&B Weave

A small, runnable setup that puts [agentgateway](https://github.com/agentgateway/agentgateway) in front of CoreWeave Serverless Inference and sends a trace for every LLM call to W&B Weave as agent spans, shown as conversations in Agent Lens: model, request params, token usage (including cache reads and reasoning), cost, prompt and response, and gateway/HTTP context.

Everything is configuration. There's no code in the request path besides the gateway.

```
client ──► agentgateway :4000 ──► api.inference.wandb.ai  (CoreWeave Serverless Inference)
                 │
                 └── OTLP/HTTP ──► trace.wandb.ai/agents/otel/v1/traces  (Weave agent spans)
```

## Requirements

- Docker with Compose
- `curl`, `jq`, `python3` (for the demo scripts)
- [`uv`](https://docs.astral.sh/uv/) (for `examples/agent.py`)
- A W&B API key. The same key authenticates to CoreWeave inference and to Weave.

## Run it

```bash
cp .env.example .env        # fill in WANDB_API_KEY and WANDB_ENTITY
docker compose up -d
./scripts/demo.sh           # three-turn conversation with Kimi K2.6
sleep 10
./scripts/verify.sh         # reads the newest conversation back from Weave
```

`verify.sh` prints the conversation's span tree with usage and Weave's cost:

```
conversation conv-1795196904
trace f2d2e10215816d319bd7b32904431a3f
  chat moonshotai/Kimi-K2.6  in=5029 cached=0 out=288 reasoning=258 cost=$0.004250930046339363
    POST api.inference.wandb.ai:443
trace 769d4b4e8caa2a0cae637befd8874376
  chat moonshotai/Kimi-K2.6  in=5032 cached=4992 out=87 reasoning=74 cost=$0.0010714700345033634
    POST api.inference.wandb.ai:443
trace b2fd69a1542d3b3bbe1bc4f749149ea3
  chat moonshotai/Kimi-K2.6  in=5028 cached=4992 out=143 reasoning=130 cost=$0.0012598300395438855
    POST api.inference.wandb.ai:443
```

The first turn is a cache miss; the next two read 4992 of ~5030 prompt tokens from cache, which cuts cost about 4x.

Then open `https://forge.coreweave.com/agent-lens/<entity>/<project>/conversations`.

Any OpenAI-compatible client works. Point it at `http://localhost:4000/v1` and prefix the model with `coreweave/`. These optional headers fill in more fields:

| Header | Span attribute |
|---|---|
| `x-conversation-id` | `gen_ai.conversation.id` (groups calls into a conversation) |
| `x-agent-name` | `gen_ai.agent.name` |
| `x-user-id` | `user.id` |

## How the config works

All of it is in [`config.yaml`](config.yaml) under `frontendPolicies.tracing`.

- **`resources`** apply to every span. `wandb.entity` and `wandb.project` route spans to the right Weave project. `service.name` names the gateway.
- **`attributes`** are CEL expressions evaluated per request and added to the request span. The agent-span endpoint reads the [GenAI semantic conventions](https://docs.coreweave.com/products/agent-lens/reference/semantic-conventions) directly and keeps every other attribute as a filterable custom attribute. agentgateway already sets the operation, provider, models, token usage, request params and gateway/HTTP context, so the config adds only conversation, agent, user, response ID, finish reason, and messages.
- **Cost** is computed by Weave from the model and token counts, with cache reads priced separately.
- **`filter`** exports only LLM calls, so health checks and other traffic don't add empty rows.

Prompt and response capture is the `gen_ai.input.messages` / `gen_ai.output.messages` / `gen_ai.system_instructions` lines. Remove them if the content shouldn't leave your network. `examples/agent.py` records the same content on its own spans.

## Known gaps

- **Most of this should be built into the gateway.** agentgateway doesn't emit the current GenAI message attributes on its own yet, so they're built by hand in CEL. Tracked in [agentgateway#4482](https://github.com/agentgateway/agentgateway/issues/4482) and [#3980](https://github.com/agentgateway/agentgateway/issues/3980).
- **Prompt messages are text only.** The gateway exposes prompt content to CEL as plain strings, so tool results, reasoning and images in the prompt don't make it into the trace. Tool calls in responses are captured from `llm.toolCalls`.
- **The response ID and finish reasons are parsed from the response body,** so they're missing for streaming responses and for clients that accept gzip (httpx and the OpenAI SDKs do).
- **Don't reuse a gateway key as a prefix.** Weave rejects a whole span when one key is both a value and the parent of another. agentgateway sets `gateway` as a string, so adding `gateway.name` drops every request span; the gateway logs `HttpTraceClient.PartialSuccess ... AttributePathConflictError`.
- **Each trace has a blank child span.** The gateway's upstream `POST api.inference.wandb.ai:443` span has no GenAI attributes. The `attributes` block only applies to the request span.
- **Prompt cache hits vary.** Repeating the same long prefix usually hits the cache on CoreWeave, but not every time, even for identical requests.
- **Incoming `traceparent` headers hide calls.** If a client sends a `traceparent` but doesn't export its own spans to the same Weave project, the gateway's call has a parent Weave never receives. It then isn't treated as a turn. Clients either export their spans to Weave too, as `examples/agent.py` does, or don't send `traceparent`.
- **The image is a nightly build.** The `coreweave` provider landed after agentgateway v1.7.0, so `compose.yaml` pins nightly `v0.0.0-alpha.cec2c4ee`. Switch to the next release once it's out.

## Agent turns: `examples/agent.py`

The gateway's spans are LLM calls only. To see them inside agent turns, the app traces each turn (as an `invoke_agent` span) and each tool run (as `execute_tool`), exports to the same Weave project, and sends `traceparent` through the gateway so the gateway's call nests under the turn. [`examples/agent.py`](examples/agent.py) does this with plain OpenTelemetry:

```bash
set -a; source .env; set +a
uv run examples/agent.py    # two-turn tool-using conversation
./scripts/verify.sh
```

```
conversation conv-c50e5ece
trace 14d3a47ee9ddf384c6d947707ca235ca
  invoke_agent
    chat moonshotai/Kimi-K2.6  in=102 cached=0 out=48 reasoning=29 cost=$0.00022998000474672153
      POST api.inference.wandb.ai:443
    execute_tool get_weather
    chat moonshotai/Kimi-K2.6  in=191 cached=96 out=35 reasoning=23 cost=$0.00019550000405388346
      POST api.inference.wandb.ai:443
trace 6f991f12d724112329ba2b5775aa53c3
  invoke_agent
    ...
```
