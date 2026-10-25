"""Stable publication must prove exact-source certification before any external writes."""

import copy
import importlib.util
import json
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

SHA = "a" * 40
OTHER = "b" * 40
PREFIX = "/repos/Fillll/whab"


@pytest.fixture
def publisher():
    path = Path(__file__).resolve().parents[1] / "scripts/publish_stable.py"
    spec = importlib.util.spec_from_file_location("whab_stable_publication", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeGitHub:
    def __init__(self, publisher):
        self.run = {"head_sha": SHA, "event": "schedule", "head_branch": "main",
                    "path": ".github/workflows/certification.yml", "repository": {"full_name": "Fillll/whab"}}
        self.jobs = [{"name": name, "status": "completed", "conclusion": "success"}
                     for name in sorted(publisher.CERTIFICATION_JOBS)]
        self.checks = [{"id": i, "name": name, "head_sha": SHA, "app": {"slug": "github-actions"},
                        "status": "completed", "conclusion": "success"}
                       for i, name in enumerate(sorted(publisher.CI_CHECKS), 1)]
        self.main = SHA
        self.tag = None
        self.release = None
        self.latest = {"id": 1, "tag_name": "v1.2.0", "draft": False, "prerelease": False}
        self.writes = []

    def request(self, method, path, payload=None, *, optional=False):
        route = urlsplit(path).path.removeprefix(PREFIX)
        if method == "POST":
            self.writes.append((route, copy.deepcopy(payload)))
            if route == "/git/refs":
                self.tag = {"type": "commit", "sha": payload["sha"]}
                return {"object": self.tag}
            if route == "/releases":
                self.release = {**payload, "id": 2, "html_url": "https://github.com/Fillll/whab/releases/tag/" + payload["tag_name"]}
                self.latest = self.release
                return self.release
            raise AssertionError(route)
        if route == "/actions/runs/123":
            return self.run
        if route == "/actions/runs/123/jobs":
            return {"jobs": self.jobs, "total_count": len(self.jobs)}
        if route == f"/commits/{SHA}/check-runs":
            return {"check_runs": self.checks, "total_count": len(self.checks)}
        if route == "/git/ref/heads/main":
            return {"object": {"type": "commit", "sha": self.main}}
        if route == "/git/ref/tags/v2.41.0":
            return {"object": self.tag} if self.tag is not None else None
        if route == "/releases/tags/v2.41.0":
            return self.release
        if route == "/releases/latest":
            return self.latest
        raise AssertionError(route)


def call(publisher, api, **kwargs):
    return publisher.publish(api, sha=SHA, run_id="123", version="2.41.0", **kwargs)


def test_green_nightly_creates_an_exact_tag_and_visible_latest_stable(publisher):
    api = FakeGitHub(publisher)
    result = call(publisher, api, write=True)
    assert result["status"] == "published" and result["commit"] == SHA
    assert api.writes[0] == ("/git/refs", {"ref": "refs/tags/v2.41.0", "sha": SHA})
    payload = api.writes[1][1]
    assert payload["target_commitish"] == SHA
    assert payload["draft"] is payload["prerelease"] is False
    assert payload["make_latest"] == "true" and payload["generate_release_notes"] is True
    assert "actions/runs/123" in payload["body"]


def test_dry_run_never_creates_a_tag_or_release(publisher):
    api = FakeGitHub(publisher)
    assert call(publisher, api)["status"] == "dry-run"
    assert not api.writes


@pytest.mark.parametrize(("field", "value"), [
    ("head_sha", OTHER), ("event", "push"), ("event", "workflow_dispatch"),
    ("head_branch", "feature"), ("path", ".github/workflows/ci.yml"),
    ("repository", {"full_name": "someone/whab"}),
])
def test_unrelated_runs_cannot_publish(publisher, field, value):
    api = FakeGitHub(publisher)
    api.run[field] = value
    with pytest.raises(ValueError, match="official scheduled"):
        call(publisher, api, write=True)
    assert not api.writes


@pytest.mark.parametrize("status", ["failure", "cancelled", "skipped", "timed_out", "pending", "missing"])
def test_incomplete_certification_cannot_create_release_state(publisher, status):
    api = FakeGitHub(publisher)
    if status == "missing":
        api.jobs.pop()
    else:
        api.jobs[0]["conclusion"] = status
    with pytest.raises(ValueError, match="Every certification job"):
        call(publisher, api, write=True)
    assert not api.writes


@pytest.mark.parametrize("fault", ["missing", "failed-rerun", "wrong-sha", "wrong-app", "in-progress"])
def test_stable_launcher_ci_requirements_cannot_be_bypassed(publisher, fault):
    api = FakeGitHub(publisher)
    if fault == "missing":
        api.checks.pop()
    elif fault == "failed-rerun":
        api.checks.append({**api.checks[0], "id": 999, "conclusion": "failure"})
    elif fault == "wrong-sha":
        api.checks[0]["head_sha"] = OTHER
    elif fault == "wrong-app":
        api.checks[0]["app"] = {"slug": "different-app"}
    else:
        api.checks[0]["status"] = "in_progress"
    with pytest.raises(ValueError, match="Both required Python CI"):
        call(publisher, api, write=True)
    assert not api.writes


def test_superseded_main_cannot_become_the_new_stable(publisher):
    api = FakeGitHub(publisher)
    api.main = OTHER
    assert call(publisher, api, write=True)["status"] == "superseded"
    assert not api.writes


def test_retries_and_newer_commits_with_an_existing_version_never_replace_it(publisher):
    api = FakeGitHub(publisher)
    call(publisher, api, write=True)
    writes = copy.deepcopy(api.writes)
    assert call(publisher, api, write=True)["status"] == "already-released"
    assert api.writes == writes
    api.tag = {"type": "commit", "sha": OTHER}
    assert call(publisher, api, write=True)["commit"] == OTHER
    assert api.writes == writes


def test_interrupted_tag_creation_recovers_without_retagging(publisher):
    api = FakeGitHub(publisher)
    api.tag = {"type": "commit", "sha": SHA}
    assert call(publisher, api, write=True)["status"] == "published"
    assert [path for path, _ in api.writes] == ["/releases"]


@pytest.mark.parametrize("target", [SHA, OTHER])
def test_tag_creation_races_only_accept_the_identical_certified_commit(publisher, target):
    api = FakeGitHub(publisher)
    original = api.request

    def request(method, path, payload=None, **kwargs):
        if method == "POST" and path.endswith("/git/refs"):
            api.tag = {"type": "commit", "sha": target}
            raise publisher.GitHubError(422)
        return original(method, path, payload, **kwargs)

    api.request = request
    if target == SHA:
        assert call(publisher, api, write=True)["status"] == "published"
    else:
        with pytest.raises(publisher.GitHubError):
            call(publisher, api, write=True)
        assert not api.writes


def test_unrelated_failed_jobs_cannot_report_success(publisher):
    api = FakeGitHub(publisher)
    api.jobs.append({"name": "future-certification", "status": "completed", "conclusion": "failure"})
    with pytest.raises(ValueError, match="Every certification job"):
        call(publisher, api, write=True)
    assert not api.writes


@pytest.mark.parametrize("fault", ["foreign-tag", "draft", "prerelease", "downgrade"])
def test_collisions_and_existing_held_releases_are_left_untouched(publisher, fault):
    api = FakeGitHub(publisher)
    if fault == "foreign-tag":
        api.tag = {"type": "commit", "sha": OTHER}
    elif fault == "downgrade":
        api.latest["tag_name"] = "v3.0.0"
    else:
        api.tag = {"type": "commit", "sha": SHA}
        api.release = {"tag_name": "v2.41.0", "draft": fault == "draft", "prerelease": fault == "prerelease"}
    with pytest.raises(ValueError):
        call(publisher, api, write=True)
    assert not api.writes


@pytest.mark.parametrize("version", ["2.41.0rc1", "2.41.0+nightly", "02.41.0", "2.41", "../main", "-1.0.0"])
def test_only_declared_canonical_versions_can_be_released(publisher, version):
    api = FakeGitHub(publisher)
    with pytest.raises(ValueError):
        publisher.publish(api, sha=SHA, run_id="123", version=version, write=True)
    assert not api.writes


def test_version_is_read_without_executing_application_code(publisher, tmp_path):
    source = tmp_path / "version.py"
    source.write_text('raise RuntimeError("must not execute")\n__version__ = "2.41.0"\n')
    assert publisher.declared_version(source) == "2.41.0"


def test_later_pages_are_required_before_accepting_validation(publisher):
    calls = []

    def request(method, path):
        calls.append(path)
        if "page=2" in path:
            return {"rows": ["last"], "total_count": 101}
        return {"rows": list(range(100)), "total_count": 101}

    assert publisher.pages(SimpleNamespace(request=request), "/fictional", "rows")[-1] == "last"
    assert len(calls) == 2


def test_http_forbidden_is_not_mistaken_for_an_absent_release(publisher, monkeypatch):
    def denied(*args, **kwargs):
        raise urllib.error.HTTPError("https://api.github.com", 403, "Forbidden", {}, None)

    monkeypatch.setattr(publisher.urllib.request, "urlopen", denied)
    with pytest.raises(publisher.GitHubError) as error:
        publisher.GitHub().request("GET", PREFIX + "/releases/latest", optional=True)
    assert error.value.status == 403


def test_api_writes_require_a_scoped_token_and_use_json(publisher, monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    api = publisher.GitHub()
    with pytest.raises(ValueError, match="scoped GH_TOKEN"):
        api.request("POST", PREFIX + "/git/refs", {"sha": SHA})
    monkeypatch.setenv("GH_TOKEN", "fictional-release-token")
    seen = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, bound):
            return b"{}"

    def send(request, **kwargs):
        seen.append(request)
        return Response()

    monkeypatch.setattr(publisher.urllib.request, "urlopen", send)
    api.request("POST", PREFIX + "/git/refs", {"ref": "refs/tags/v2.41.0", "sha": SHA})
    assert json.loads(seen[0].data) == {"ref": "refs/tags/v2.41.0", "sha": SHA}
    assert seen[0].get_header("Content-type") == "application/json"
    assert seen[0].get_header("Authorization") == "Bearer fictional-release-token"


def test_workflow_gives_only_the_schedule_gated_publisher_write_permission():
    path = Path(__file__).resolve().parents[3] / ".github/workflows/certification.yml"
    source = path.read_text()
    readonly, publisher = source.split("  stable-release:\n", 1)
    assert "contents: write" not in readonly
    assert "github.event_name == 'schedule'" in publisher and "github.ref == 'refs/heads/main'" in publisher
    assert "needs: [checks, external-plugin-contracts, macos-launcher]" in publisher
    assert "contents: write" in publisher and "checks: read" in publisher and "actions: read" in publisher
    assert "ref: ${{ github.sha }}" in publisher and "persist-credentials: false" in publisher
    assert "--publish" in publisher
    assert "pull_request_target" not in source
