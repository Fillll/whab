"""Offline branding for a fixed fictional institution; unknown names have no logo."""

import base64

from whab.plugins import BankLogos, LogoImage, Plugin, PluginContract

_PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC")


def resolve(institution):
    return "fictional-bank" if institution == "Fictional Bank" else None


def fetch(key):
    return LogoImage(_PNG, "image/png") if key == "fictional-bank" else None


plugin = Plugin("example-branding", "Fictional branding", bank_logos=BankLogos(resolve, fetch),
                contract=PluginContract(lifecycle="restart"))
