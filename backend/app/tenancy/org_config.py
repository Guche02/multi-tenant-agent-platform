"""Per-org settings: what the agent is, what it can do, and the org's policies.

Stored as JSON on the organization (organizations.config), seeded from seed/orgs/<slug>/org.yaml.
The agent's system prompt is filled in from these values, so a new org with an existing kind
of business needs only configuration and seed data, no code.
"""

from functools import cached_property
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

HexColour = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Which tools and workflow the agent gets: an online store, or an appointment business.
    toolset: Literal["orders", "bookings"]
    # One line about the business, used in the system prompt: "an online store selling ...".
    business: str
    # Shown when a customer opens the chat.
    greeting: str


class Branding(BaseModel):
    """How the org's entry page and chat look. Public: the sign-in page shows it."""

    model_config = ConfigDict(extra="forbid")

    # Buttons, links, the customer's message bubbles.
    primary: HexColour = "#2f5d8a"
    # Text on top of `primary`.
    on_primary: HexColour = "#ffffff"
    # A soft background for highlights: the entry page, the selected conversation.
    tint: HexColour = "#eef3f8"
    # One line under the org's name on its entry page.
    tagline: str = ""


class BookingPolicy(BaseModel):
    """Booking rules. The knowledge base's cancellation policy describes the same numbers."""

    model_config = ConfigDict(extra="forbid")

    slot_interval_minutes: int = 30  # sessions start on the hour and half hour
    min_notice_hours: int = 2
    max_days_ahead: int = 30
    free_cancellation_hours: int = 24
    late_cancellation_fee_percent: int = 50
    # Cancelling with less notice than this, or not turning up, costs the full price.
    no_show_hours: int = 2
    # Bookings can't be moved once they are this close.
    reschedule_cutoff_hours: int = 24


class OrgConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str
    currency: str
    language: str = "English"
    agent: AgentConfig
    branding: Branding = Branding()
    booking_policy: BookingPolicy | None = None

    @cached_property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)
