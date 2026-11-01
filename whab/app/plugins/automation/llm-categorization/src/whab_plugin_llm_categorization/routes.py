"""Category metadata and the compatible legacy credential-settings alias."""
from whab_plugin_ai_credentials.api import settings_router

from .engine import INSTRUCTIONS


def router(context):
    return settings_router(context, route_id="llm-categorization", prompt_rules=INSTRUCTIONS)
