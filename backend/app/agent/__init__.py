from app.agent.base import Agent
from app.agent.placeholder import PlaceholderAgent
from app.db.models import Organization


def get_agent(org: Organization) -> Agent:
    """The agent for an org. Each org will get its own (orders, bookings); for now, a placeholder."""
    return PlaceholderAgent()
