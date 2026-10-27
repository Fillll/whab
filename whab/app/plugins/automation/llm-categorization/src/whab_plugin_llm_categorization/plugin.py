"""LLM categorization owns its providers and local credential settings transport."""

from whab.plugins import Categorization, Interface, Plugin, PluginContract

from .engine import Engine
from .routes import router

plugin = Plugin(id="llm-categorization", display_name="LLM categorization",
                categorization=Categorization(Engine), interface=Interface(factory=router, local_settings=True),
                contract=PluginContract(lifecycle="restart"))
