#!/usr/bin/env python3
"""Build a native WHAB.app; private runtimes and developer environments are never copied."""

import argparse
import json
import os
import plistlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

LAUNCHER = Path(__file__).resolve().parents[1]
REPO = LAUNCHER.parents[2]
APP = REPO / "whab" / "app"
SOURCE_ROOT_FILES = {"pyproject.toml", "README.md", "LICENSE"}
FORBIDDEN_NAMES = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules"}
FORBIDDEN_SUFFIXES = {".pyc", ".pyo", ".sqlite", ".sqlite3", ".db", ".whab", ".pem", ".key", ".log"}


def run(command, **kwargs):
    return subprocess.run([str(item) for item in command], check=True, **kwargs)


def clean_environment():
    environment = os.environ.copy()
    for key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__"):
        environment.pop(key, None)
    environment.update(PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
    return environment


def source_allowed(relative):
    """Allow only buildable source, without following arbitrary filesystem trees."""
    if any(part in FORBIDDEN_NAMES or part.endswith(".egg-info") for part in relative.parts):
        return False
    if relative == Path(".env.example"):
        return True
    if any(part.startswith(".env") for part in relative.parts):
        return False
    if relative.suffix.lower() in FORBIDDEN_SUFFIXES:
        return False
    if relative.parts[0] in SOURCE_ROOT_FILES and len(relative.parts) == 1:
        return True
    if relative in {Path("scripts/install.py"), Path("scripts/setup.py")}:
        return True
    if relative.parts[0] == "src":
        return True
    if relative.parts[0] == "plugins" and len(relative.parts) >= 4:
        return (relative.parts[3] == "src" or
                (len(relative.parts) == 4 and relative.parts[3] in
                 {"pyproject.toml", "README.md", "LICENSE", "CHANGELOG.md"}))
    return False


def copy_seed(destination):
    # Git's source inventory respects repository ignores and includes a reorganization
    # before staging. Copy only an additional narrow application-source allowlist.
    result = run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard",
                  "--", "whab/app"], cwd=REPO, stdout=subprocess.PIPE)
    paths = set(result.stdout.decode().split("\0")) - {""}
    if not paths:
        raise RuntimeError("No WHAB application source found; build from the repository checkout.")
    for entry in sorted(paths):
        source = REPO / entry
        relative = source.relative_to(APP)
        if not source_allowed(relative) or not source.exists():
            continue
        if source.is_symlink() or not source.is_file():
            raise RuntimeError(f"Source must be a regular file: {relative}")
        target = destination / "whab" / "app" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    if not (destination / "whab/app/pyproject.toml").is_file():
        raise RuntimeError("Source seed is incomplete.")
    commit = run(["git", "rev-parse", "HEAD"], cwd=REPO, stdout=subprocess.PIPE).stdout.decode().strip()
    # A local source seed is useful offline but is never represented as CI verified.
    version = re.search(r'__version__\s*=\s*"([^\"]+)"', (APP / "src/whab/__init__.py").read_text())
    if version is None:
        raise RuntimeError("Could not read WHAB's source version.")
    dirty = bool(run(["git", "status", "--porcelain"], cwd=REPO,
                     stdout=subprocess.PIPE).stdout)
    (destination / "build.json").write_text(json.dumps({
        "commit": "development" if dirty else commit, "version": version.group(1), "tested": False,
        "development": dirty,
    }, indent=2) + "\n")


def python_metadata(python):
    code = ("import json,sys,sysconfig; print(json.dumps({'version':list(sys.version_info[:3]),"
            "'prefix':sys.base_prefix,'framework':sysconfig.get_config_var('PYTHONFRAMEWORK'),"
            "'executable':sys.executable}))")
    result = run([python, "-I", "-c", code], stdout=subprocess.PIPE, env=clean_environment())
    metadata = json.loads(result.stdout)
    if metadata["version"][:2] != [3, 14]:
        raise RuntimeError("Build with a relocatable Python 3.14 interpreter.")
    prefix = Path(metadata["prefix"]).resolve()
    if (metadata["framework"] or str(prefix).startswith(("/System/", "/usr/", "/Library/", "/opt/homebrew/"))
            or not (prefix / "bin/python3.14").is_file()
            or not (prefix / "lib/libpython3.14.dylib").is_file()):
        raise RuntimeError("Use a clean python-build-standalone installation, not macOS, Homebrew or framework Python.")
    return prefix


def copy_python(python, destination):
    prefix = python_metadata(python)
    (destination / "bin").mkdir(parents=True)
    (destination / "lib").mkdir()
    shutil.copy2(prefix / "bin/python3.14", destination / "bin/python3.14")
    for alias in ("python", "python3"):
        (destination / "bin" / alias).symlink_to("python3.14")
    shutil.copy2(prefix / "lib/libpython3.14.dylib", destination / "lib/libpython3.14.dylib")

    def ignore(directory, names):
        if Path(directory).name == "site-packages":
            # A base installation may have local tooling. Only pip may be carried over;
            # application packages always come from the deliberately built wheelhouse.
            return [name for name in names if name != "pip" and not name.startswith("pip-")]
        return [name for name in names if name == "__pycache__" or name.endswith((".pyc", ".pyo"))]

    shutil.copytree(prefix / "lib/python3.14", destination / "lib/python3.14", ignore=ignore)
    run([destination / "bin/python3.14", "-I", "-c",
         "import ssl,sqlite3,venv,sys; assert sys.version_info[:2] == (3,14); print(sys.version.split()[0])"],
        env=clean_environment())


def build_wheelhouse(python, seed, destination):
    destination.mkdir()
    source = seed / "whab/app"
    plugins = sorted(project.parent for project in source.glob("plugins/*/*/pyproject.toml")
                     if project.parent.parent.name != "example")
    if len(plugins) < 7:
        raise RuntimeError("Source seed does not contain the seven maintained plugin packages.")
    run([python, "-I", "-m", "pip", "wheel", "--disable-pip-version-check", "--no-cache-dir",
         "--wheel-dir", destination, source, *plugins, "setuptools>=77.0.3", "wheel"],
        cwd=source, env=clean_environment())


def write_icon(iconset, destination):
    # Modern ICNS stores standard PNG representations in length-prefixed chunks.
    # Write those directly: iconutil is not present on every developer/CI image.
    representations = {
        "icp4": "icon_16x16.png", "icp5": "icon_32x32.png",
        "icp6": "icon_32x32@2x.png", "ic07": "icon_128x128.png",
        "ic08": "icon_256x256.png", "ic09": "icon_512x512.png",
        "ic10": "icon_512x512@2x.png", "ic11": "icon_16x16@2x.png",
        "ic12": "icon_32x32@2x.png", "ic13": "icon_128x128@2x.png",
        "ic14": "icon_256x256@2x.png",
    }
    chunks = []
    for kind, filename in representations.items():
        data = (iconset / filename).read_bytes()
        chunks.append(kind.encode("ascii") + struct.pack(">I", len(data) + 8) + data)
    payload = b"".join(chunks)
    destination.write_bytes(b"icns" + struct.pack(">I", len(payload) + 8) + payload)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable, help="Clean relocatable Python 3.14 interpreter (a venv using it is accepted).")
    parser.add_argument("--output", type=Path, default=LAUNCHER / "dist/WHAB.app")
    parser.add_argument("--skip-runtime", action="store_true", help="Build only the native shell for CI; output cannot launch WHAB.")
    parser.add_argument("--replace", action="store_true", help="Replace an existing output .app after the new bundle is complete.")
    args = parser.parse_args()
    output = args.output.expanduser().absolute()
    if output.suffix != ".app" or output.is_symlink():
        parser.error("--output must be a regular .app bundle path.")
    if output.exists() and not args.replace:
        parser.error("Output already exists; use --replace to rebuild it.")
    output.parent.mkdir(parents=True, exist_ok=True)
    version = (LAUNCHER / "VERSION").read_text().strip()
    with tempfile.TemporaryDirectory(prefix="whab-app-build-") as temporary:
        temporary = Path(temporary)
        environment = clean_environment()
        environment["CLANG_MODULE_CACHE_PATH"] = str(temporary / "clang-cache")
        environment["SWIFT_MODULECACHE_PATH"] = str(temporary / "swift-cache")
        scratch = temporary / "swift-build"
        run(["swift", "build", "--package-path", LAUNCHER, "--scratch-path", scratch,
             "--disable-sandbox", "-c", "release"], env=environment)
        bundle = temporary / "WHAB.app"
        resources = bundle / "Contents/Resources"
        executable = bundle / "Contents/MacOS/WHABLauncher"
        resources.mkdir(parents=True)
        executable.parent.mkdir()
        shutil.copy2(scratch / "release/WHABLauncher", executable)
        iconset = temporary / "WHAB.iconset"
        icon_generator = temporary / "generate-icon"
        run(["swiftc", "-framework", "AppKit", LAUNCHER / "scripts/icon.swift",
             "-o", icon_generator], env=environment)
        run([icon_generator, iconset], env=environment)
        write_icon(iconset, resources / "WHAB.icns")
        with (LAUNCHER / "Resources/Info.plist").open("rb") as stream:
            info = plistlib.load(stream)
        info.update(CFBundleShortVersionString=version, CFBundleVersion=version)
        with (bundle / "Contents/Info.plist").open("wb") as stream:
            plistlib.dump(info, stream)
        manager = resources / "manager"
        manager.mkdir()
        for name in ("launcher.py", "server.py"):
            shutil.copy2(LAUNCHER / "manager" / name, manager / name)
        seed = resources / "seed"
        seed.mkdir()
        copy_seed(seed)
        if not args.skip_runtime:
            copy_python(args.python, resources / "python")
            # Build all wheels with the relocated runtime to verify pip also relocates.
            build_wheelhouse(resources / "python/bin/python3.14", seed, resources / "wheelhouse")
        else:
            (resources / "DEVELOPMENT_ONLY").write_text("Native shell only. Rebuild without --skip-runtime to run WHAB.\n")
        # Sign nested native code before sealing the enclosing app. This local ad-hoc
        # signature is not a Developer ID signature or a notarized public release.
        if not args.skip_runtime:
            for native in sorted((resources / "python").rglob("*")):
                if native.is_file() and not native.is_symlink() and native.suffix in (".so", ".dylib"):
                    run(["codesign", "--force", "--sign", "-", native], stdout=subprocess.DEVNULL)
            run(["codesign", "--force", "--sign", "-", resources / "python/bin/python3.14"], stdout=subprocess.DEVNULL)
        run(["codesign", "--force", "--sign", "-", bundle], stdout=subprocess.DEVNULL)
        run(["codesign", "--verify", "--deep", "--strict", bundle])
        if output.exists():
            shutil.rmtree(output)
        shutil.copytree(bundle, output, symlinks=True)
    print(f"Built {output}" + (" (native shell only)" if args.skip_runtime else ""))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        sys.exit(f"Build failed: {error}")
