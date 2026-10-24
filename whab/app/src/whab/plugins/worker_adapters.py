"""Typed data adapters for live optional hooks; no router or ledger authority."""

import json
from dataclasses import asdict
from pathlib import Path

from .bank_adapter import BankAdapter
from .contracts import Capability, PluginContract, Requirement, contract_for
from .interface_adapter import interface_record, read_interface_record
from .remote_client_adapter import (
    RemoteLedgerClient,
    client_record,
    read_client_record,
    supports_remote_client,
)
from .sdk import (
    BankLogos,
    Insight,
    PluginAction,
    WorkspaceImport,
    valid_merchant_logo_key,
    valid_plugin_name,
)
from .ui_worker import adapter_info as ui_info
from .ui_worker import dispatch as dispatch_ui
from .ui_worker import supports_live_visual
from .ui_worker import validate_adapter_info as validate_ui_info
from .wire import MAX_PAYLOAD_BYTES, WireError, _unique, _value
from .worker_resources import capture_resources, resource_bundle

LIVE_CAPABILITIES = frozenset({"reporting.insights", "reporting.export", "branding.bank", "branding.merchant", "ingestion.file", "categorization", "interface.api", "interface.ui", "ledger.read", "ledger.mutate", "ingestion.bank", "lifecycle.background"})
OPERATIONS = frozenset({"bank.resolve", "bank.domain", "bank.fetch", "merchant.fetch", "cache.configure", "settings.read", "settings.act", "file.parse", "file.resources", "category.call", "interface.describe", "ui.resources", "ui.render", "client.describe", "provider.call", "provider.resources"})
BINARY_OPERATIONS = frozenset({"bank.fetch", "merchant.fetch", "file.parse", "file.resources", "category.call", "ui.resources", "ui.render", "provider.call", "provider.resources"})


def supports_live_plugin(plugin):
    if plugin.ui is not None:
        return supports_live_visual(plugin)
    if type(plugin.storage) is RemoteLedgerClient:
        return supports_remote_client(plugin)
    contract = contract_for(plugin)
    capabilities = {row.name for row in contract.provides}
    if type(plugin.bank) is BankAdapter:
        return (
            plugin.contract is not None and contract.lifecycle == "live" and contract.authority == "local"
            and {'ingestion.bank', 'lifecycle.background'} <= capabilities
            and capabilities <= {'ingestion.bank', 'lifecycle.background', 'branding.bank', 'branding.merchant'}
            and all(row.version == 1 for row in contract.provides)
            and contract.state_version == 1 and not contract.state_upgrades
            and all(getattr(plugin, name) is None for name in (
                'bind', 'migrate_state', 'budget_connection', 'settings', 'insights', 'export', 'categorization',
                'storage', 'ui', 'file_import', 'interface', 'branding_factory',
            ))
        )
    if plugin.categorization is not None:
        return (
            plugin.contract is not None and contract.lifecycle == "live"
            and capabilities == {"categorization"} and all(row.version == 1 for row in contract.provides)
            and contract.state_version == 1 and not contract.state_upgrades
            and all(getattr(plugin, name) is None for name in (
                "bind", "migrate_state", "budget_connection", "settings", "insights", "export",
                "bank", "bank_ui", "storage", "ui", "bank_logos", "merchant_logos", "file_import", "interface", "branding_factory",
            ))
        )
    return (
        plugin.contract is not None and contract.lifecycle == "live"
        and bool(capabilities) and capabilities <= LIVE_CAPABILITIES - {'categorization', 'ingestion.bank', 'lifecycle.background'}
        and all(row.version == 1 for row in contract.provides)
        and contract.state_version == 1 and not contract.state_upgrades
        and (plugin.branding_factory is None or capabilities <= {"branding.bank", "branding.merchant"})
        and all(getattr(plugin, name) is None for name in (
            "bind", "migrate_state", "budget_connection", "categorization", "bank", "bank_ui", "storage", "ui",
        ))
        and (plugin.interface is None or plugin.interface.adapter is not None)
        and (plugin.settings is None or (plugin.file_import is None and (plugin.bank_logos is not None or plugin.merchant_logos is not None)))
    )


def adapter_info(plugin):
    bank = plugin.bank_logos
    settings = plugin.settings
    return {"bank_logos": None if bank is None else {
                "domain": bank.resolve_domain is not None, "cache": bank.configure_cache is not None,
                "provider_name": bank.provider_name, "provider_url": bank.provider_url,
            },
            "merchant_logos": plugin.merchant_logos is not None,
            "file_import": None if plugin.file_import is None else {
                "ui": plugin.file_import.templates is not None or plugin.file_import.resources is not None,
            },
            "settings": None if settings is None else {"actions": [asdict(row) for row in settings.actions]},
            "interface": None if plugin.interface is None or plugin.interface.adapter is None else interface_record(plugin.interface.adapter),
            "ui": ui_info(plugin.ui),
            "categorization": plugin.categorization is not None,
            "remote_client": client_record(plugin.storage) if type(plugin.storage) is RemoteLedgerClient else None,
            "bank": {"ui": plugin.bank_ui is not None} if type(plugin.bank) is BankAdapter else None}


def contract_info(plugin):
    contract = contract_for(plugin)
    return {"requires": [asdict(row) for row in contract.requires], "conflicts": list(contract.conflicts),
            "authority": contract.authority, "state_version": contract.state_version,
            "state_upgrades": [list(edge) for edge in contract.state_upgrades], "lifecycle": contract.lifecycle,
            "legacy": plugin.contract is None}


def read_contract(value, capabilities):
    if type(value) is not dict or set(value) != {"requires", "conflicts", "authority", "state_version", "state_upgrades", "lifecycle", "legacy"} or type(value["legacy"]) is not bool:
        raise WireError()
    if type(value["requires"]) is not list or len(value["requires"]) > 100 or type(value["conflicts"]) is not list or type(value["state_upgrades"]) is not list:
        raise WireError()
    if any(type(row) is not dict or set(row) != {"name", "min_version", "max_version"} for row in value["requires"]):
        raise WireError()
    if any(type(edge) is not list for edge in value["state_upgrades"]):
        raise WireError()
    return PluginContract(provides=tuple(Capability(**row, composition="singleton" if row["name"] in {"interface.ui", "ledger.read", "ledger.mutate"} else "additive") for row in capabilities),
                          requires=tuple(Requirement(**row) for row in value["requires"]), conflicts=tuple(value["conflicts"]),
                          authority=value["authority"], state_version=value["state_version"],
                          state_upgrades=tuple(tuple(edge) for edge in value["state_upgrades"]), lifecycle=value["lifecycle"])


def validate_adapter_info(value):
    if type(value) is not dict or set(value) != {"bank_logos", "merchant_logos", "file_import", "settings", "interface", "ui", "categorization", "remote_client", "bank"}:
        raise WireError()
    if value["remote_client"] is not None:
        read_client_record(value["remote_client"])
        if value["merchant_logos"] is not False or any(value[name] is not None for name in ("bank_logos", "file_import", "settings", "interface", "ui", "bank")) or value["categorization"] is not False:
            raise WireError()
    if value["ui"] is not None:
        validate_ui_info(value["ui"])
        if any(value[name] is not None for name in ("bank_logos", "file_import", "settings", "interface", "bank", "remote_client")) or value["merchant_logos"] or value["categorization"]:
            raise WireError()
    if value["interface"] is not None:
        read_interface_record(value["interface"])
    if type(value["merchant_logos"]) is not bool or type(value["categorization"]) is not bool:
        raise WireError()
    provider = value['bank']
    if provider is not None and (type(provider) is not dict or provider.keys() != {'ui'} or type(provider['ui']) is not bool):
        raise WireError()
    bank = value["bank_logos"]
    if bank is not None:
        if type(bank) is not dict or set(bank) != {"domain", "cache", "provider_name", "provider_url"} or type(bank["domain"]) is not bool or type(bank["cache"]) is not bool:
            raise WireError()
        BankLogos(lambda _: None, lambda _: None, provider_name=bank["provider_name"], provider_url=bank["provider_url"])
    parser = value["file_import"]
    if parser is not None and (type(parser) is not dict or set(parser) != {"ui"} or type(parser["ui"]) is not bool):
        raise WireError()
    settings = value["settings"]
    if settings is not None:
        if type(settings) is not dict or set(settings) != {"actions"} or type(settings["actions"]) is not list or len(settings["actions"]) > 50:
            raise WireError()
        actions = tuple(PluginAction(**row) for row in settings["actions"] if type(row) is dict)
        if len(actions) != len(settings["actions"]) or len({row.id for row in actions}) != len(actions):
            raise WireError()
    return value


def import_bytes(data):
    data = data.validated_copy() if type(data) is WorkspaceImport else None
    if data is None:
        raise WireError()
    value = {name: [dict(row) for row in getattr(data, name)] for name in ("accounts", "categories", "transactions", "assignments")}
    value.update(budget_start_month=data.budget_start_month, warnings=list(data.warnings), currency=data.currency)
    _value(value, max_items=250000)
    raw = bytearray()
    for part in json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":")).iterencode(value):
        raw.extend(part.encode("utf-8"))
        if len(raw) > MAX_PAYLOAD_BYTES:
            raise WireError()
    return bytes(raw)


def read_import(raw):
    if type(raw) is not bytes or not raw or len(raw) > MAX_PAYLOAD_BYTES:
        raise WireError()
    value = json.loads(raw, object_pairs_hook=_unique)
    _value(value, max_items=250000)
    if type(value) is not dict or set(value) != {"accounts", "categories", "transactions", "assignments", "budget_start_month", "warnings", "currency"}:
        raise WireError()
    for name in ("accounts", "categories", "transactions", "assignments", "warnings"):
        if type(value[name]) is not list:
            raise WireError()
        value[name] = tuple(value[name])
    return WorkspaceImport(**value).validated_copy()


def dispatch(registry, plugin, operation, arguments, payload):
    if type(arguments) is not dict:
        raise WireError()
    if operation in {"ui.resources", "ui.render"}:
        return dispatch_ui(plugin.ui, operation, arguments, payload)
    if operation == "file.parse":
        if arguments or plugin.file_import is None or not 0 < len(payload) <= 10 * 1024 * 1024:
            raise WireError()
        return {"format": "workspace-import-v1"}, import_bytes(plugin.file_import.parse(payload))
    if payload:
        raise WireError()
    if operation == "client.describe":
        if arguments or not supports_remote_client(plugin):
            raise WireError()
        return client_record(plugin.storage), b""
    if operation == "interface.describe":
        if arguments or plugin.interface is None or plugin.interface.adapter is None:
            raise WireError()
        return interface_record(plugin.interface.adapter), b""
    if operation == "file.resources":
        if arguments or plugin.file_import is None:
            raise WireError()
        return resource_bundle(capture_resources(plugin.file_import))
    if operation == "cache.configure":
        if set(arguments) != {"directory"} or plugin.bank_logos is None or plugin.bank_logos.configure_cache is None:
            raise WireError()
        directory = arguments["directory"]
        if type(directory) is not str or not 1 <= len(directory) <= 4096 or "\x00" in directory or not Path(directory).is_absolute():
            raise WireError()
        plugin.bank_logos.configure_cache(Path(directory))
        return None, b""
    if operation == "settings.read":
        if arguments or plugin.settings is None:
            raise WireError()
        output = registry.settings(plugin.id)
        return {"insights": [asdict(row) for row in output["insights"]], "actions": [asdict(row) for row in output["actions"]]}, b""
    if operation == "settings.act":
        if set(arguments) != {"action"} or plugin.settings is None:
            raise WireError()
        return registry.settings_action(plugin.id, arguments["action"]), b""
    if operation in {"bank.resolve", "bank.domain"}:
        if set(arguments) != {"text"} or plugin.bank_logos is None:
            raise WireError()
        text = arguments["text"]
        if type(text) is not str or not 0 < len(text) <= (200 if operation == "bank.resolve" else 253):
            raise WireError()
        output = registry.bank_logo(text) if operation == "bank.resolve" else registry.bank_logo("", website="https://" + text)
        return output[1] if output is not None else None, b""
    if operation in {"bank.fetch", "merchant.fetch"}:
        if set(arguments) != {"key"} or not (valid_plugin_name(arguments["key"]) if operation == "bank.fetch" else valid_merchant_logo_key(arguments["key"])):
            raise WireError()
        output = (registry.logo_image if operation == "bank.fetch" else registry.merchant_logo_image)(plugin.id, arguments["key"])
        return {"media_type": output.media_type if output else None}, output.data if output else b""
    raise WireError()


def read_settings(value):
    if type(value) is not dict or set(value) != {"insights", "actions"} or type(value["insights"]) is not list or len(value["insights"]) > 50 or type(value["actions"]) is not list or len(value["actions"]) > 50:
        raise WireError()
    return {"insights": tuple(Insight(**row) for row in value["insights"]),
            "actions": tuple(PluginAction(**row) for row in value["actions"])}
