"""Generation-resolved visual projection, templates and retained public assets."""

import hashlib
import mimetypes
import os
import re
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import HTMLResponse
from jinja2 import ChoiceLoader, DictLoader
from starlette.templating import Jinja2Templates

from whab import __version__
from whab.plugins.runtime import PluginError
from whab.plugins.ui_render import render_arguments
from whab.plugins.ui_resources import UIResourceArchive, VisualBundle, capture_visual
from whab.plugins.ui_selection import DISABLED_GENERATION
from whab.plugins.ui_worker import snapshot_ui

_HASH = re.compile(r"[0-9a-f]{64}\Z")


def _source_paths(root):
    pending, files, entries = [root], [], 0
    while pending:
        with os.scandir(pending.pop()) as directory:
            for entry in directory:
                entries += 1
                if entries > 4000 or entry.is_symlink():
                    raise ValueError()
                if entry.is_dir(follow_symlinks=False):
                    if entry.name != "__pycache__":
                        pending.append(Path(entry.path))
                elif entry.name.endswith(".py"):
                    if not entry.is_file(follow_symlinks=False) or len(files) >= 1000:
                        raise ValueError()
                    files.append(Path(entry.path))
    return sorted(files)


def native_code_identity(plugin):
    """Identify a native bootstrap; approved worker closures use their own hash.

    Same-version editable changes to the context builder's package change this
    cache identity too. This is resource coherence, not artifact approval.
    """
    import inspect

    digest = hashlib.sha256(("native-ui-v1\n" + __version__ + "\n" + plugin.id).encode("utf-8"))
    try:
        entries = tuple(entry for entry in metadata.entry_points(group="whab.plugins") if entry.name == plugin.id)
        if len(entries) == 1 and entries[0].dist is not None:
            digest.update((entries[0].value + "\n" + entries[0].dist.version).encode("utf-8"))
        source = inspect.getsourcefile(plugin.ui.build_context)
        if source is not None:
            root = Path(source).parent
            files, size = 0, 0
            for path in _source_paths(root):
                if path.is_symlink() or not path.is_file():
                    raise ValueError()
                files += 1
                if files > 1000 or path.stat().st_size > 2 * 1024 * 1024:
                    raise ValueError()
                with path.open("rb") as stream:
                    raw = stream.read(2 * 1024 * 1024 + 1)
                size += len(raw)
                if len(raw) > 2 * 1024 * 1024 or size > 10 * 1024 * 1024:
                    raise ValueError()
                digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\x00" + raw)
        else:
            # Injected host descriptors have no package metadata. Their resource
            # digest still distinguishes every retained template/asset byte.
            code = getattr(plugin.ui.build_context, "__code__", None)
            if code is not None:
                digest.update(code.co_code)
    except Exception:
        raise PluginError("The visual interface code identity could not be checked. Reinstall or prepare an approved worker.",
                          code="ui_code_unavailable", plugin_id=plugin.id) from None
    return digest.hexdigest()


@dataclass(frozen=True)
class VisualGeneration:
    plugin_id: str | None
    ui: object = None
    bundle: VisualBundle | None = None

    @property
    def generation(self):
        return self.bundle.generation if self.bundle else DISABLED_GENERATION

    @classmethod
    def prepare(cls, plugin, archive, *, code_identity=None):
        if plugin is None:
            return cls(None)
        ui = snapshot_ui(plugin.ui)
        bundle = archive.store(capture_visual(plugin.id, ui, code_identity or native_code_identity(plugin)))
        return cls(plugin.id, ui, bundle)


class LeasedVisualUI:
    """Stable route dependency; every field resolves in the request generation."""

    def __init__(self, runtime):
        self.runtime = runtime

    @property
    def available(self):
        with self.runtime.lease() as lease:
            return lease.service("visual").ui is not None

    @property
    def generation(self):
        with self.runtime.lease() as lease:
            return lease.service("visual").generation

    def __getattr__(self, name):
        if name not in {"budget_months", "lightweight_transactions", "lightweight_accounts", "transaction_page_size", "build_context"}:
            raise AttributeError(name)
        if name == "build_context":
            def build(*args, **kwargs):
                with self.runtime.lease() as lease:
                    selected = lease.service("visual").ui
                    if selected is None:
                        raise HTTPException(404, "A visual interface is unavailable.")
                    return selected.build_context(*args, **kwargs)
            return build
        with self.runtime.lease() as lease:
            selected = lease.service("visual").ui
            if selected is None:
                raise HTTPException(404, "A visual interface is unavailable.")
            return getattr(selected, name)


class VisualTemplates:
    def __init__(self, runtime, plugins):
        self.runtime, self.plugins = runtime, plugins

    def TemplateResponse(self, *, request, name, context):
        if name != "base.html":
            raise ValueError("Use the visual generation's root template.")
        with self.runtime.lease() as lease:
            visual = lease.service("visual")
            if visual.ui is None:
                raise HTTPException(404, "A visual interface is unavailable.")
            extensions = self.plugins.ui_templates()
            if visual.ui.render is not None:
                values = dict(context)
                inputs, routing = values.pop("_ui_inputs"), values.pop("_ui_routing")
                values.pop("url", None)
                values.pop("request", None)
                return HTMLResponse(visual.ui.render(render_arguments(inputs, values, routing, request, extensions)))
            templates = Jinja2Templates(directory=".")
            templates.env.loader = ChoiceLoader([
                DictLoader({path.removeprefix("templates/"): raw.decode("utf-8")
                            for path, raw in visual.bundle.resources.files.items() if path.startswith("templates/")}),
                DictLoader(extensions),
            ])
            return templates.TemplateResponse(request=request, name=name, context=context)


def create_resource_router(home):
    """Only exact retained generations; a missing old asset never serves new code."""
    archive = UIResourceArchive(home)
    router = APIRouter()

    def resource(generation, name):
        if type(generation) is not str or _HASH.fullmatch(generation) is None:
            raise HTTPException(404, "Visual resource not available")
        try:
            bundle = archive.load(generation)
        except PluginError:
            raise HTTPException(404, "Visual resource not available") from None
        raw = bundle.resources.files.get("static/" + name)
        if raw is None:
            raise HTTPException(404, "Visual resource not available")
        return Response(raw, media_type=mimetypes.guess_type(name)[0] or "application/octet-stream", headers={
            "Cache-Control": "public, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff",
        })

    @router.get("/ui/{generation}/static/{name:path}")
    def versioned(generation: str, name: str):
        return resource(generation, name)

    return router
