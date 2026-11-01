"""Independent subscription classification, separate from budget categorization."""

from whab.plugins import Capability, Interface, Plugin, PluginContract

from .routes import router

plugin = Plugin(
    id="subscriptions",
    display_name="Subscriptions",
    interface=Interface(factory=router, local_settings=True),
    contract=PluginContract(provides=(Capability("subscriptions.labels"),), lifecycle="restart"),
)
