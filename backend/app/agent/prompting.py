"""
the template (prompts/system.md) is the same for every org. The parts that
differ are filled in per request from:
  - the org's config: name, business, language, timezone, currency;
  - the toolset: its workflow (prompts/<toolset>.md) and the list of its tools;
  - the moment: today's date and time in the org's timezone, for relative dates;
  - the customer: who is signed in.
"""

from datetime import datetime
from functools import cache
from pathlib import Path

from llama_index.core.tools import FunctionTool

from app.agent.base import AgentContext

PROMPTS_DIR = Path(__file__).parent / "prompts"

# What the customer's records are called, for the Boundaries section.
RECORDS = {"orders": "orders and chats", "bookings": "bookings and chats"}
CITATION_EXAMPLES = {
    "orders": "[Shipping and Delivery Policy › Delivery times]",
    "bookings": "[Cancellation and Late Arrival Policy › Cancellation fees]",
}


@cache
def _template(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def _tool_list(tools: list[FunctionTool]) -> str:
    return "\n".join(f"- {tool.metadata.name}: {tool.metadata.description}" for tool in tools)


def render_system_prompt(ctx: AgentContext, tools: list[FunctionTool], now: datetime | None = None) -> str:
    config = ctx.config
    toolset = config.agent.toolset
    now = (now or datetime.now(config.tz)).astimezone(config.tz)
    values = {
        "org_name": ctx.org_name,
        "business": config.agent.business,
        "language": config.language,
        "currency": config.currency,
        "timezone": config.timezone,
        "weekday": f"{now:%A}",
        "date": f"{now:%d %B %Y}",
        "time": f"{now:%H:%M}",
        "customer_email": ctx.customer_email,
        "tools": _tool_list(tools),
        "records": RECORDS[toolset],
    }
    values["workflow"] = _template(toolset).format_map(values).strip()
    return _template("system").format_map(values).strip()
