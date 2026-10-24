"""Read the curated, packaged data catalog without discovering or loading plugins."""

import json
import re
from datetime import date
from importlib.resources import files
from urllib.parse import urlsplit

MAX_CATALOG_BYTES = 2 * 1024 * 1024
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[a-zA-Z0-9.+-]*)\Z")
_NAME = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")


class CatalogError(ValueError):
    """A fixed diagnostic never includes underlying metadata or filesystem paths."""

    def __init__(self):
        super().__init__("The curated plugin catalog is unavailable or invalid. Check this WHAB installation.")


def _require(condition):
    if not condition:
        raise CatalogError()


def _text(value, *, limit=1000):
    _require(type(value) is str and 0 < len(value) <= limit and "\x00" not in value)
    value.encode("utf-8")
    return value


def _exact(value, keys):
    _require(type(value) is dict and value.keys() == set(keys))


def _positive(value):
    _require(type(value) is int and 1 <= value <= 65535)


def _url(value):
    value = _text(value, limit=2000)
    address = urlsplit(value)
    _require(address.scheme == "https" and address.netloc == "github.com" and address.username is None and address.password is None and not address.query)


def _date(value):
    _require(type(value) is str and date.fromisoformat(value).isoformat() == value)


def validate_catalog(value):
    """Validate a detached data payload. No metadata discovery or hooks run."""
    try:
        _exact(value, {"format_version", "host_version", "publisher", "plugins", "verifications"})
        _require(type(value["format_version"]) is int and value["format_version"] == 1)
        _require(_VERSION.fullmatch(_text(value["host_version"])) is not None)
        publisher = value["publisher"]
        _exact(publisher, {"maintainer", "repository", "documentation_ref", "security", "security_policy", "security_verified_date", "support_policy"})
        for name in ("maintainer", "support_policy"):
            _text(publisher[name], limit=2000)
        for name in ("repository", "security", "security_policy"):
            _url(publisher[name])
        _require(re.fullmatch(r"[0-9a-f]{40}", _text(publisher["documentation_ref"])) is not None)
        _date(publisher["security_verified_date"])
        _require(type(value["plugins"]) is list and 1 <= len(value["plugins"]) <= 100)
        identifiers, distributions = set(), set()
        for row in value["plugins"]:
            _exact(row, {"id", "display_name", "api_version", "capabilities", "requires", "conflicts", "lifecycle", "legacy", "authority",
                         "state_version", "state_upgrades", "live_support", "distribution", "version", "entry_point",
                         "requires_python", "requires_host", "support", "source", "maintainer", "repository", "security", "documentation", "verifications"})
            _require(_NAME.fullmatch(_text(row["id"], limit=64)) is not None and row["id"] not in identifiers)
            distribution = re.sub(r"[-_.]+", "-", _text(row["distribution"])).lower()
            _require(_NAME.fullmatch(distribution) is not None and distribution not in distributions)
            identifiers.add(row["id"])
            distributions.add(distribution)
            _require(_VERSION.fullmatch(_text(row["version"])) is not None)
            for name in ("display_name", "requires_python", "requires_host", "source", "maintainer"):
                _text(row[name])
            _require(row["source"].startswith("whab/app/plugins/") and ".." not in row["source"].split("/") and "\\" not in row["source"])
            for name in ("repository", "security", "documentation"):
                _url(row[name])
            _positive(row["api_version"])
            _positive(row["state_version"])
            _exact(row["entry_point"], {"group", "name", "value"})
            _require(row["entry_point"]["group"] == "whab.plugins" and row["entry_point"]["name"] == row["id"])
            _require(re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", _text(row["entry_point"]["value"])) is not None)
            _require(type(row["capabilities"]) is list and len(row["capabilities"]) <= 100)
            capability_names = set()
            for capability in row["capabilities"]:
                _exact(capability, {"name", "version", "composition"})
                name = _text(capability["name"])
                _require(_NAME.fullmatch(name) is not None and name not in capability_names)
                capability_names.add(name)
                _positive(capability["version"])
                _require(capability["composition"] in {"singleton", "additive"})
            _require(type(row["requires"]) is list and len(row["requires"]) <= 100)
            requirement_names = set()
            for requirement in row["requires"]:
                _require(type(requirement) is dict and requirement.keys() in ({"name", "min_version"}, {"name", "min_version", "max_version"}))
                name = _text(requirement["name"])
                _require(_NAME.fullmatch(name) is not None and name not in requirement_names)
                requirement_names.add(name)
                _positive(requirement["min_version"])
                if "max_version" in requirement:
                    _positive(requirement["max_version"])
                    _require(requirement["min_version"] <= requirement["max_version"])
            _require(type(row["conflicts"]) is list and len(row["conflicts"]) <= 100 and len(row["conflicts"]) == len(set(row["conflicts"])))
            for name in row["conflicts"]:
                _require(_NAME.fullmatch(_text(name)) is not None and name not in capability_names)
            _require(row["lifecycle"] in {"live", "restart"} and type(row["legacy"]) is bool and row["authority"] in {"local", "remote", "unknown"})
            _require(type(row["state_upgrades"]) is list and len(row["state_upgrades"]) <= 100)
            for edge in row["state_upgrades"]:
                _require(type(edge) is list and len(edge) == 2)
                _positive(edge[0])
                _positive(edge[1])
                _require(edge[0] < edge[1] <= row["state_version"])
            if row["lifecycle"] == "live":
                _require(not row["legacy"] and row["live_support"] == "stateless-report-worker" and row["state_version"] == 1
                         and not row["state_upgrades"] and bool(capability_names) and capability_names <= {"reporting.insights", "reporting.export"})
            else:
                _require(row["live_support"] == "restart-required")
            _require(row["support"] in {"maintained", "teaching"})
            _require(type(row["verifications"]) is list and len(row["verifications"]) <= 100)
        _require(type(value["verifications"]) is list and len(value["verifications"]) <= 100)
        evidence_ids = set()
        for evidence in value["verifications"]:
            _exact(evidence, {"id", "date", "host_version", "source_commit", "selection", "artifacts", "tests", "pull_requests", "scope", "method"})
            identifier = _text(evidence["id"])
            _require(_NAME.fullmatch(identifier) is not None and identifier not in evidence_ids)
            evidence_ids.add(identifier)
            _date(evidence["date"])
            _require(_VERSION.fullmatch(_text(evidence["host_version"])) is not None)
            _require(re.fullmatch(r"[0-9a-f]{40}", _text(evidence["source_commit"])) is not None)
            _require(type(evidence["selection"]) is list and bool(evidence["selection"]) and len(evidence["selection"]) == len(set(evidence["selection"])) and set(evidence["selection"]) <= identifiers)
            _require(type(evidence["artifacts"]) is dict and 1 <= len(evidence["artifacts"]) <= 100)
            for distribution, version in evidence["artifacts"].items():
                _require(_NAME.fullmatch(_text(distribution)) is not None)
                _require(_VERSION.fullmatch(_text(version)) is not None)
            _require(evidence["artifacts"].get("whab") == evidence["host_version"])
            for identifier in evidence["selection"]:
                row = next(row for row in value["plugins"] if row["id"] == identifier)
                _require(row["distribution"] in evidence["artifacts"])
            for name in ("tests", "pull_requests"):
                _require(type(evidence[name]) is list and 1 <= len(evidence[name]) <= 100)
                for item in evidence[name]:
                    _text(item, limit=2000)
                    if name == "pull_requests":
                        _url(item)
                        _require(re.fullmatch(r"https://github.com/Fillll/whab/pull/[1-9][0-9]*", item) is not None)
            _require(evidence["method"] in {"installed-wheel", "source-tests"})
            _text(evidence["scope"], limit=4000)
        for row in value["plugins"]:
            _require(len(row["verifications"]) == len(set(row["verifications"])) and set(row["verifications"]) <= evidence_ids)
            for identifier in row["verifications"]:
                evidence = next(item for item in value["verifications"] if item["id"] == identifier)
                _require(evidence["artifacts"].get(row["distribution"]) == row["version"])
        # JSON roundtrip guarantees callers cannot mutate a retained resource.
        return json.loads(json.dumps(value, allow_nan=False))
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise CatalogError() from None


def catalog_snapshot():
    """Return the packaged curated catalog; never discover, import or install code."""
    try:
        raw = files("whab.plugins").joinpath("catalog.json").read_bytes()
        _require(0 < len(raw) <= MAX_CATALOG_BYTES)

        def unique(pairs):
            result = {}
            for key, value in pairs:
                _require(key not in result)
                result[key] = value
            return result

        return validate_catalog(json.loads(raw.decode("utf-8"), object_pairs_hook=unique))
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
        raise CatalogError() from None
