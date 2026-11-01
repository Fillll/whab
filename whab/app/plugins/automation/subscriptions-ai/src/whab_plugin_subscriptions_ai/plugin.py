"""Optional AI companion; manual subscription tools remain independently usable."""

from fastapi import APIRouter
from whab_plugin_subscriptions.api import install_ai

from whab.plugins import Interface, Plugin, PluginContract, Requirement

from .jobs import Jobs


def router(context):
    install_ai(context.service, Jobs)
    return APIRouter()


plugin = Plugin(
    id="subscriptions-ai", display_name="AI subscription classification",
    interface=Interface(factory=router, local_settings=True),
    contract=PluginContract(requires=(Requirement("subscriptions.labels"), Requirement("ai.credentials")), lifecycle="restart"),
)
