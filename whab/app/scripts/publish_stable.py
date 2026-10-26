"""Publish a declared version only from successful official scheduled certification."""

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "Fillll/whab"
PREFIX = f"/repos/{REPOSITORY}"
VERSION = r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
CERTIFICATION_JOBS = {
    "Certification (Python 3.14)", "Certification (Python 3.15)",
    "Reusable external plugin workflow / contracts", "macOS launcher certification",
}
CI_CHECKS = {"CI (Python 3.14)", "CI (Python 3.15)"}
PUBLISH_JOB = "Publish certified stable release"


class GitHubError(RuntimeError):
    def __init__(self, status):
        self.status = status
        super().__init__(f"GitHub request failed (HTTP {status}); no existing tag or release was changed.")


class GitHub:
    def request(self, method, path, payload=None, *, optional=False):
        if not path.startswith(PREFIX + "/"):
            raise ValueError("Only the official WHAB repository is supported.")
        token = os.environ.get("GH_TOKEN")
        if method != "GET" and not token:
            raise ValueError("Publishing requires the release job's scoped GH_TOKEN.")
        headers = {"Accept": "application/vnd.github+json", "Content-Type": "application/json", "User-Agent": "WHAB-nightly-release",
                   "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request("https://api.github.com" + path, method=method, headers=headers,
                                         data=json.dumps(payload).encode() if payload is not None else None)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as error:
            if optional and method == "GET" and error.code == 404:
                return None
            raise GitHubError(error.code) from None
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("GitHub response exceeded the supported bound.")
        return json.loads(raw)


def commit(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError("An exact lowercase source commit is required.")
    return value


def version_tuple(version):
    match = re.fullmatch(VERSION, version)
    if match is None:
        raise ValueError("Stable releases require a canonical X.Y.Z app version.")
    return tuple(map(int, match.groups()))


def declared_version(path):
    values = [node.value for node in ast.parse(path.read_text()).body if isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets)]
    if len(values) != 1:
        raise ValueError("The app must declare exactly one literal version.")
    version = ast.literal_eval(values[0])
    if not isinstance(version, str):
        raise ValueError("The app version must be a string.")
    version_tuple(version)
    return version


def pages(api, path, key):
    result = []
    for page in range(1, 101):
        response = api.request("GET", f"{PREFIX}{path}?per_page=100&page={page}")
        rows = response[key]
        result.extend(rows)
        if len(result) >= response["total_count"]:
            return result
        if not rows:
            break
    raise ValueError("Incomplete GitHub validation evidence.")


def verify_green(api, sha, run_id):
    run = api.request("GET", f"{PREFIX}/actions/runs/{run_id}")
    if (run["head_sha"] != sha or run["event"] != "schedule" or run["head_branch"] != "main"
            or run["path"] != ".github/workflows/certification.yml"
            or run["repository"]["full_name"] != REPOSITORY):
        raise ValueError("Only this official scheduled certification can publish its exact source commit.")
    jobs = pages(api, f"/actions/runs/{run_id}/jobs", "jobs")
    completed = {row["name"] for row in jobs if row["status"] == "completed" and row["conclusion"] == "success"}
    if not CERTIFICATION_JOBS <= completed or any(
        row["name"] != PUBLISH_JOB and (row["status"] != "completed" or row["conclusion"] != "success")
        for row in jobs
    ):
        raise ValueError("Every certification job must complete successfully before stable publication.")
    latest = {}
    for row in pages(api, f"/commits/{sha}/check-runs", "check_runs"):
        if row["name"] in CI_CHECKS and row.get("head_sha") == sha and row.get("app", {}).get("slug") == "github-actions":
            if row["id"] > latest.get(row["name"], {}).get("id", -1):
                latest[row["name"]] = row
    if not CI_CHECKS <= latest.keys() or any(
        row["status"] != "completed" or row["conclusion"] != "success" for row in latest.values()
    ):
        raise ValueError("Both required Python CI checks must be green on the certified commit.")


def tag_commit(api, tag):
    reference = api.request("GET", f"{PREFIX}/git/ref/tags/{tag}", optional=True)
    if reference is None:
        return None
    value = reference["object"]
    for _ in range(8):
        if value["type"] == "commit":
            return commit(value["sha"])
        if value["type"] != "tag":
            break
        value = api.request("GET", f"{PREFIX}/git/tags/{commit(value['sha'])}")["object"]
    raise ValueError("The release tag must resolve to an exact commit.")


def publish(api, *, sha, run_id, version, write=False):
    commit(sha)
    version_tuple(version)
    if not str(run_id).isdigit() or int(run_id) < 1:
        raise ValueError("A positive certification run ID is required.")
    verify_green(api, sha, run_id)
    head = api.request("GET", f"{PREFIX}/git/ref/heads/main")["object"]
    if head.get("type") != "commit" or head.get("sha") != sha:
        return {"status": "superseded", "reason": "main advanced; a newer certification owns publication"}
    tag = "v" + version
    existing = api.request("GET", f"{PREFIX}/releases/tags/{tag}", optional=True)
    target = tag_commit(api, tag)
    if existing is not None:
        if existing.get("draft") or existing.get("prerelease") or existing.get("tag_name") != tag or target is None:
            raise ValueError("An existing draft, prerelease or inconsistent release needs maintainer review.")
        return {"status": "already-released", "tag": tag, "commit": target,
                "reason": "Declared versions are released once; existing tags/releases are never replaced."}
    if target is not None and target != sha:
        raise ValueError("The version tag already names another commit; bump the declared version instead of moving it.")
    latest = api.request("GET", f"{PREFIX}/releases/latest", optional=True)
    if latest is not None:
        if not latest["tag_name"].startswith("v") or version_tuple(latest["tag_name"][1:]) >= version_tuple(version):
            raise ValueError("The candidate must be newer than the latest stable release.")
    if not write:
        return {"status": "dry-run", "tag": tag, "commit": sha, "run_id": run_id}
    if target is None:
        try:
            api.request("POST", f"{PREFIX}/git/refs", {"ref": f"refs/tags/{tag}", "sha": sha})
        except GitHubError as error:
            if error.status not in {409, 422} or tag_commit(api, tag) != sha:
                raise
    # A concurrent writer must never cause this release to use another artifact.
    if tag_commit(api, tag) != sha:
        raise ValueError("The tag does not match the certified commit; publication stopped.")
    release = api.request("POST", f"{PREFIX}/releases", {
        "tag_name": tag, "target_commitish": sha, "name": f"WHAB {version}",
        "body": f"Certified source: `{sha}`.\n\n[Successful nightly certification](https://github.com/{REPOSITORY}/actions/runs/{run_id}).",
        "draft": False, "prerelease": False, "make_latest": "true", "generate_release_notes": True,
    })
    visible = api.request("GET", f"{PREFIX}/releases/tags/{tag}")
    if (visible["id"] != release["id"] or visible.get("draft") or visible.get("prerelease")
            or visible.get("tag_name") != tag or tag_commit(api, tag) != sha
            or api.request("GET", f"{PREFIX}/releases/latest")["id"] != release["id"]):
        raise ValueError("Published release verification failed; inspect the existing release before retrying.")
    return {"status": "published", "tag": tag, "commit": sha, "url": visible["html_url"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--publish", action="store_true", help="Permit publication; the default is read-only validation.")
    args = parser.parse_args(argv)
    try:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        if head != commit(args.sha):
            raise ValueError("The checked-out source must match the certified commit.")
        subprocess.run(["git", "diff", "--quiet", "HEAD", "--"], cwd=ROOT, check=True)
        result = publish(GitHub(), sha=args.sha, run_id=args.run_id,
                         version=declared_version(ROOT / "src/whab/__init__.py"), write=args.publish)
        print(json.dumps(result))
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Stable publication stopped: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
