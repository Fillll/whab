"""Require originating pull-request links in every tracked product changelog."""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SECTION = re.compile(r"^##[ \t]+(.+?)[ \t]*$", re.MULTILINE)
PR_LINK = re.compile(
    r"(?<!!)\[[^\]\n]+\]\(https://github\.com/Fillll/whab/pull/[1-9][0-9]*\)"
)


def tracked_changelogs(root: Path) -> tuple[Path, ...]:
    """Include every owner and future plugin, without scanning runtime files."""
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, text=True, check=True
    )
    return tuple(
        root / name
        for name in sorted(result.stdout.split("\0"))
        if name and Path(name).name == "CHANGELOG.md"
    )


def validate_changelog(text: str) -> list[str]:
    """Check each version separately; empty Unreleased sections need no PR yet."""
    sections = tuple(SECTION.finditer(text))
    if not sections:
        return ["1: no changelog sections found (expected ## headings)"]
    errors = []
    for index, section in enumerate(sections):
        end = sections[index + 1].start() if index + 1 < len(sections) else len(text)
        body = text[section.end():end]
        heading = section.group(1)
        if heading.casefold() == "unreleased" and not body.strip():
            continue
        if not PR_LINK.search(body):
            line = text.count("\n", 0, section.start()) + 1
            errors.append(
                f"{line}: {heading}: add an originating PR Markdown link "
                "such as [#123](https://github.com/Fillll/whab/pull/123)"
            )
    return errors


def main() -> int:
    try:
        paths = tracked_changelogs(ROOT)
        if not paths:
            raise ValueError("No tracked CHANGELOG.md files were found.")
        errors = [
            f"{path.relative_to(ROOT)}:{error}"
            for path in paths
            for error in validate_changelog(path.read_text(encoding="utf-8"))
        ]
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        print(f"Changelog PR check failed: {error}", file=sys.stderr)
        return 1
    if errors:
        print("Changelog PR check failed:\n" + "\n".join(errors), file=sys.stderr)
        return 1
    print(f"All {len(paths)} tracked changelogs include originating PR links.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
