"""Check source Markdown links, anchors, fences and the supported-plugin inventory offline."""

import html
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

from plugin_metadata import source_inventory, validate_generated

ROOT = Path(__file__).resolve().parents[3]
PRIVATE_PARTS = {".venv", "node_modules", "data", "budgets", "tls", "cache", "work", "build", "dist", "_tmp", ".build"}
INLINE_LINK = re.compile(r"!?\[([^\]\n]*)\]\(\s*(<[^>\n]+>|(?:[^\s()\\]|\\.|\([^()\n]*\))+)(?:\s+[\"'][^\n]*[\"'])?\s*\)")
REFERENCE = re.compile(r"^ {0,3}\[([^\]\n]+)\]:\s*(<[^>\n]+>|\S+)", re.MULTILINE)
REFERENCE_USE = re.compile(r"!?\[([^\]\n]+)\]\[([^\]\n]*)\]")
INLINE_CODE = re.compile(r"(`+).*?\1")


def source_document(name: str) -> bool:
    """Limit new-file discovery to documentation locations, never runtime trees."""
    path = Path(name)
    parts = path.parts
    if path.suffix.lower() != ".md" or any(part in PRIVATE_PARTS for part in parts):
        return False
    if len(parts) == 1 or parts[0] == ".github":
        return True
    if parts[0] != "whab":
        return False
    if len(parts) == 2 or parts[1] == "docs":
        return True
    if parts[1] == "app":
        return len(parts) == 3 or (
            len(parts) >= 4 and parts[2] == "plugins"
            and (len(parts) <= 6 or parts[5] == "docs")
        )
    return parts[1] == "launchers" and (
        len(parts) <= 4 or parts[3] in {"manager", "docs"}
    )


def documentation_paths(root: Path) -> tuple[Path, ...]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--",
         ":(top,glob)*.md", ":(top,glob).github/**/*.md", ":(top,glob)whab/**/*.md"],
        cwd=root, capture_output=True, text=True, check=True,
    )
    return tuple(root / name for name in sorted(set(result.stdout.split("\0"))) if source_document(name))


def source_path(path: Path, root: Path) -> Path:
    """Reject symlink components and private targets before opening any source file."""
    root = root.resolve()
    message = "Source files must remain inside the repository, outside private/generated paths, and cannot use symlink components."
    try:
        relative = path.absolute().relative_to(root)
    except ValueError:
        raise ValueError(message) from None
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError(message)
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or any(part in PRIVATE_PARTS for part in resolved.relative_to(root).parts):
        raise ValueError(message)
    return resolved


def read_source_text(path: Path, root: Path) -> str:
    """Read only recognized Markdown or package metadata after path validation."""
    path = source_path(path, root)
    relative = path.relative_to(root.resolve())
    metadata = relative.as_posix() == "whab/app/pyproject.toml" or (
        len(relative.parts) == 6 and relative.parts[:3] == ("whab", "app", "plugins")
        and relative.name == "pyproject.toml"
    )
    if not source_document(relative.as_posix()) and not metadata:
        raise ValueError("Refusing to read a file outside recognized source documentation and package metadata.")
    return path.read_text(encoding="utf-8")


def prose(text: str) -> tuple[str, list[str]]:
    """Preserve line positions while excluding fenced examples and inline code."""
    lines = text.splitlines(keepends=True)
    fence = None
    output = []
    for number, line in enumerate(lines, 1):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line.rstrip("\n"))
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1] and not marker[2].strip():
                fence = None
            output.append("\n")
        elif marker:
            fence = (marker[1][0], len(marker[1]), number)
            output.append("\n")
        else:
            output.append(line)
    errors = [f"{fence[2]}: unclosed code fence"] if fence else []
    return "".join(output), errors


def anchors(text: str) -> set[str]:
    """Use GitHub-style heading slugs, including duplicate-heading suffixes."""
    result = set(re.findall(r"\b(?:id|name)=[\"']([^\"']+)[\"']", text))
    lines = text.splitlines()
    for index, line in enumerate(lines):
        heading = re.match(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        title = heading[1] if heading else None
        if index and re.fullmatch(r" {0,3}(?:=+|-+)\s*", line) and lines[index - 1].strip():
            title = lines[index - 1].strip()
        if title is None:
            continue
        title = INLINE_LINK.sub(lambda match: match[1], title)
        title = html.unescape(re.sub(r"<[^>]*>", "", title)).strip().lower()
        slug = re.sub(r"[^\w\s-]", "", title)
        slug = re.sub(r"\s", "-", slug)
        candidate = slug
        suffix = 0
        while candidate in result:
            suffix += 1
            candidate = f"{slug}-{suffix}"
        result.add(candidate)
    return result


def validate_document(path: Path, root: Path, texts: dict[Path, str]) -> list[str]:
    text, errors = prose(texts[path])
    # Code examples often deliberately show placeholder or nonexistent links.
    link_text = INLINE_CODE.sub("", text)
    matches = [(match.start(), match[2]) for match in INLINE_LINK.finditer(link_text)]
    definitions = tuple(REFERENCE.finditer(link_text))
    matches.extend((match.start(), match[2]) for match in definitions)
    labels = {" ".join(match[1].casefold().split()) for match in definitions}
    for reference in REFERENCE_USE.finditer(link_text):
        label = " ".join((reference[2] or reference[1]).casefold().split())
        if label not in labels:
            line = link_text.count("\n", 0, reference.start()) + 1
            errors.append(f"{line}: undefined Markdown link reference: {label}")
    for offset, raw in sorted(matches):
        destination = raw.removeprefix("<").removesuffix(">")
        destination = re.sub(r"\\([()\[\] ])", r"\1", destination)
        address = urlsplit(destination)
        if address.scheme or address.netloc:
            continue
        line = link_text.count("\n", 0, offset) + 1
        target = (root / unquote(address.path).lstrip("/") if address.path.startswith("/") else path.parent / unquote(address.path)) if address.path else path
        target = target.resolve()
        if not target.is_relative_to(root.resolve()):
            errors.append(f"{line}: local link escapes the repository: {destination}")
        elif not target.exists():
            errors.append(f"{line}: missing local link target: {destination}")
        elif address.fragment and target.suffix.lower() == ".md":
            if target not in texts:
                # Do not read linked private or arbitrary untracked files.
                errors.append(f"{line}: Markdown target is outside source documentation: {destination}")
                continue
            target_prose, _ = prose(texts[target])
            if unquote(address.fragment) not in anchors(target_prose):
                errors.append(f"{line}: missing Markdown anchor: {destination}")
    return errors


def normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def validate_inventory(root: Path) -> list[str]:
    application = root / "whab/app"
    plugins = {row["id"]: normalized(row["distribution"]) for row in source_inventory(root) if row["support"] == "maintained"}
    inventory_path = application / "plugins/README.md"
    inventory = read_source_text(inventory_path, root)
    section = re.search(r"^## Supported plugins\s*\n(.*?)(?=^## |\Z)", inventory, re.MULTILINE | re.DOTALL)
    if not section:
        return ["whab/app/plugins/README.md: missing canonical Supported plugins section"]
    rows = {}
    for line in section[1].splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip().strip("`") for cell in line.strip("|").split("|")]
        if len(cells) == 3 and cells[0] not in {"Plugin ID", "---"} and not re.fullmatch(r"[-: ]+", cells[0]):
            if cells[0] in rows:
                return [f"whab/app/plugins/README.md: duplicate inventory ID {cells[0]}"]
            rows[cells[0]] = normalized(cells[1])
    errors = []
    if rows != plugins:
        errors.append(
            "whab/app/plugins/README.md: Supported plugins table differs from bundled metadata "
            f"(expected {plugins}; documented {rows})"
        )
    return errors


def main() -> int:
    try:
        paths = documentation_paths(ROOT)
        if not paths:
            raise ValueError("No source Markdown documents were found.")
        paths = tuple(source_path(path, ROOT) for path in paths)
        texts = {path: read_source_text(path, ROOT) for path in paths if path.exists()}
        errors = [
            f"{path.relative_to(ROOT)}:{error}"
            for path in texts
            for error in validate_document(path, ROOT, texts)
        ]
        errors.extend(validate_inventory(ROOT))
        errors.extend(validate_generated(ROOT))
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError) as error:
        print(f"Documentation check failed: {error}", file=sys.stderr)
        return 1
    if errors:
        print("Documentation check failed:\n" + "\n".join(errors), file=sys.stderr)
        return 1
    print(f"All {len(texts)} source Markdown documents and the supported-plugin inventory passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
