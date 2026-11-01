"""Machine-local provider connections shared through the public connection API."""

from whab.plugins import Capability, Interface, Plugin, PluginContract

from .api import settings_router

plugin = Plugin(id="ai-credentials", display_name="AI credentials",
                interface=Interface(factory=settings_router, local_settings=True),
                contract=PluginContract(provides=(Capability("ai.credentials"),), lifecycle="restart"))
