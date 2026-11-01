"""LLM categorization uses the independent AI credentials public API."""

from whab.plugins import Categorization, Interface, Plugin, PluginContract, Requirement

from .engine import Engine
from .routes import router

plugin = Plugin(id="llm-categorization", display_name="AI transaction categorization",
                categorization=Categorization(Engine), interface=Interface(factory=router, local_settings=True),
                contract=PluginContract(requires=(Requirement("ai.credentials"),), lifecycle="restart"))
