"""The agent nodes that make up the Wayfarer graph.

Each module here is one specialist agent with a single responsibility. They
communicate only through the shared :class:`~app.graph.state.AgentState`, never
by calling each other, which is what makes the pipeline reorderable and each
node independently testable.
"""

from .clarifier import clarifier_node
from .destination_scout import destination_scout_node
from .intake import intake_node
from .itinerary_planner import itinerary_planner_node
from .plan_validator import plan_validator_node
from .presenter import presenter_node
from .query_analyst import query_analyst_node
from .recommender import recommender_node
from .selection import selection_node
from .weather_analyst import weather_analyst_node

__all__ = [
    "clarifier_node",
    "destination_scout_node",
    "intake_node",
    "itinerary_planner_node",
    "plan_validator_node",
    "presenter_node",
    "query_analyst_node",
    "recommender_node",
    "selection_node",
    "weather_analyst_node",
]
