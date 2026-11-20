# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "httpx>=0.27",
#   "opentelemetry-sdk>=1.27",
#   "opentelemetry-exporter-otlp-proto-http>=1.27",
# ]
# ///
"""A two-turn tool-using agent that traces turns and tools and lets the gateway trace LLM calls.

Each turn is an `invoke_agent` span. Tool executions are `execute_tool` spans. LLM calls go
through agentgateway with a `traceparent` header, so the gateway's `chat` spans nest under the
turn. All spans go to the same Weave project, which renders them as one conversation.
"""

import json
import os
import uuid

import httpx
from opentelemetry import propagate
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

AGENT_NAME = "travel-helper"
MODEL = os.environ.get("MODEL", "coreweave/moonshotai/Kimi-K2.6")
GATEWAY_URL = (
    f"http://localhost:{os.environ.get('GATEWAY_PORT', '4000')}/v1/chat/completions"
)
SYSTEM_PROMPT = (
    "You are a terse travel assistant. Use the weather tool when asked about weather."
)
WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}
QUESTIONS = [
    "What's the weather in Lisbon right now? One sentence.",
    "And in Porto? One sentence.",
]

exporter = OTLPSpanExporter(
    endpoint="https://trace.wandb.ai/agents/otel/v1/traces",
    headers={"wandb-api-key": os.environ["WANDB_API_KEY"]},
)
provider = TracerProvider(
    resource=Resource(
        {
            "service.name": AGENT_NAME,
            "wandb.entity": os.environ["WANDB_ENTITY"],
            "wandb.project": os.environ["WANDB_PROJECT"],
        }
    )
)
provider.add_span_processor(BatchSpanProcessor(exporter))
tracer = provider.get_tracer("agentgateway-weave-demo")


def main() -> None:
    conversation_id = f"conv-{uuid.uuid4().hex[:8]}"
    history = [{"role": "system", "content": SYSTEM_PROMPT}]

    for question in QUESTIONS:
        answer = run_turn(conversation_id, history, question)
        print(f"> {question}\n  {answer}")

    provider.shutdown()
    print(f"conversation {conversation_id}")


def run_turn(conversation_id: str, history: list[dict], question: str) -> str:
    """Run one user turn: call the model, execute any tool calls, and return the final answer."""
    history.append({"role": "user", "content": question})

    with tracer.start_as_current_span(f"invoke_agent {AGENT_NAME}") as turn:
        turn.set_attributes(
            {
                "gen_ai.operation.name": "invoke_agent",
                "gen_ai.agent.name": AGENT_NAME,
                "gen_ai.conversation.id": conversation_id,
                "gen_ai.input.messages": json.dumps([text_message("user", question)]),
            }
        )

        message = chat(conversation_id, history)
        while message.get("tool_calls"):
            history.append(message)
            for call in message["tool_calls"]:
                history.append(execute_tool(call))
            message = chat(conversation_id, history)

        answer = message["content"]
        history.append({"role": "assistant", "content": answer})
        turn.set_attribute(
            "gen_ai.output.messages", json.dumps([text_message("assistant", answer)])
        )

    return answer


def chat(conversation_id: str, messages: list[dict]) -> dict:
    """Call the model through agentgateway, forwarding the current trace context."""
    headers = {"x-conversation-id": conversation_id, "x-agent-name": AGENT_NAME}
    propagate.inject(headers)

    body = {
        "model": MODEL,
        "max_tokens": 800,
        "messages": messages,
        "tools": [WEATHER_TOOL],
    }
    response = httpx.post(GATEWAY_URL, json=body, headers=headers, timeout=120)
    response.raise_for_status()

    return response.json()["choices"][0]["message"]


def execute_tool(call: dict) -> dict:
    """Run one tool call inside an `execute_tool` span and return the tool message."""
    name = call["function"]["name"]
    arguments = call["function"]["arguments"]

    with tracer.start_as_current_span(f"execute_tool {name}") as span:
        # Set before running the tool, so a failed call still shows which tool and arguments.
        span.set_attributes(
            {
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": name,
                "gen_ai.tool.call.id": call["id"],
                "gen_ai.tool.call.arguments": arguments,
            }
        )
        result = get_weather(**json.loads(arguments))
        span.set_attribute("gen_ai.tool.call.result", result)

    return {"role": "tool", "tool_call_id": call["id"], "content": result}


def get_weather(city: str) -> str:
    """Return canned weather so the demo is deterministic."""
    return json.dumps({"city": city, "condition": "sunny", "temperature_c": 21})


def text_message(role: str, text: str) -> dict:
    """Build a GenAI semantic-convention message with one text part."""
    return {"role": role, "parts": [{"type": "text", "content": text}]}


if __name__ == "__main__":
    main()
