"""Independent subscription classification, separate from budget categorization."""

from whab.plugins import Interface, Plugin, PluginContract, Requirement

from .routes import router

plugin = Plugin(
    id="subscriptions",
    display_name="Subscriptions",
    interface=Interface(factory=router, local_settings=True),
    contract=PluginContract(requires=(Requirement("ai.credentials"),), lifecycle="restart"),
)
