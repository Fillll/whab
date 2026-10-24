"""Exact local wheel evidence for already installed, explicitly selected code."""

import hashlib
import re
import zipfile
from email.parser import BytesParser
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement


def normalized(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(65536):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_closure(result, dependencies, required):
    pending = [normalized(name) for name in required]
    visited = set()
    while pending:
        key = pending.pop()
        if key in visited:
            continue
        if key not in result or len(visited) >= 128:
            raise ValueError()
        visited.add(key)
        requirements = dependencies[key]
        if len(requirements) > 512:
            raise ValueError()
        for text in requirements:
            if len(text) > 2048:
                raise ValueError()
            requirement = Requirement(text)
            if requirement.marker is not None and not requirement.marker.evaluate({"extra": ""}):
                continue
            if requirement.url or requirement.extras:
                raise ValueError()
            name = normalized(requirement.name)
            if name not in result or not requirement.specifier.contains(result[name]["version"], prereleases=True):
                raise ValueError()
            pending.append(name)


def installed_artifacts(wheelhouse, required):
    """Match installed files to supplied wheel bytes before plugin imports.

    This does not install or approve packages. Editable sources cannot count as
    verified wheels. The caller explicitly supplies a trusted local wheelhouse.
    """
    result = {}
    dependencies = {}
    try:
        for path in sorted(Path(wheelhouse).glob("*.whl")):
            if path.is_symlink() or path.stat().st_size > 100 * 1024 * 1024:
                raise ValueError()
            with zipfile.ZipFile(path) as wheel:
                entries = wheel.infolist()
                if len(entries) > 20000 or sum(entry.file_size for entry in entries) > 300 * 1024 * 1024:
                    raise ValueError()
                names = [entry.filename for entry in entries]
                if len(names) != len(set(names)):
                    raise ValueError()
                metadata_name, = (name for name in names if name.count("/") == 1 and name.endswith(".dist-info/METADATA"))
                prefix = metadata_name.removesuffix("METADATA")
                if not {prefix + "WHEEL", prefix + "RECORD"} <= set(names):
                    raise ValueError()
                if wheel.getinfo(metadata_name).file_size > 1024 * 1024:
                    raise ValueError()
                fields = BytesParser().parsebytes(wheel.read(metadata_name))
                name, version = fields["Name"], fields["Version"]
                if len(fields.get_all("Name", [])) != 1 or len(fields.get_all("Version", [])) != 1 or type(name) is not str or type(version) is not str:
                    raise ValueError()
                try:
                    distribution = metadata.distribution(name)
                except metadata.PackageNotFoundError:
                    continue
                if distribution.version != version:
                    continue
                key = normalized(name)
                if key in result:
                    raise ValueError()
                root = Path(distribution.locate_file("")).resolve()
                for entry in entries:
                    relative = entry.filename
                    parts = relative.rstrip("/").split("/")
                    if any(part in {"", ".", ".."} for part in parts) or "\\" in relative or ":" in relative:
                        raise ValueError()
                    if entry.is_dir() or relative == prefix + "RECORD":
                        continue
                    if ".data/" in relative:
                        _, scheme, relative = relative.split("/", 2)
                        if scheme not in {"purelib", "platlib"}:
                            raise ValueError()
                    installed = Path(distribution.locate_file(relative)).resolve()
                    if not installed.is_relative_to(root) or _hash(installed) != hashlib.sha256(wheel.read(entry)).hexdigest():
                        raise ValueError()
                result[key] = {"distribution": key, "version": version, "filename": path.name, "sha256": _hash(path)}
                dependencies[key] = fields.get_all("Requires-Dist", [])
        _validate_closure(result, dependencies, required)
    except Exception:
        raise ValueError("Installed artifacts do not match the supplied local wheels. Use a clean wheel installation and preserve the exact wheelhouse.") from None
    return [result[key] for key in sorted(result)]
