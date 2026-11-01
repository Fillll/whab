"""Independent CLI interface registration; transport logic lives in client.py."""

from fastapi import APIRouter

from whab.plugins import Interface, Plugin, PluginContract


def router(context):
    routes = APIRouter()

    @routes.get("/api/plugin-settings/cli")
    def status():
        return {"command": "whab-cli", "protocol": "remote/v1"}

    return routes


plugin = Plugin(
    id="cli", display_name="Command line interface",
    interface=Interface(factory=router, local_settings=True),
    contract=PluginContract(lifecycle="restart"),
)
