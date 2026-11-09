"""Safe category names, guarded single intents and verified saved output."""

import json
from contextlib import contextmanager

import httpx
import pytest
from whab_plugin_cli import console
from whab_plugin_cli.category_commands import resolve_category


@pytest.fixture
def category_transport(monkeypatch, tmp_path):
    def child(identifier, name, group, parent):
        return {
            "id": identifier,
            "name": name,
            "group": group,
            "parentId": parent,
            "currency": "USD",
            "target": 1234,
        }

    groups = [
        {
            "id": "finance",
            "name": "Finance",
            "children": [
                child("a", "Example 项目", "Finance", "finance"),
                child("b", "Same", "Finance", "finance"),
            ],
        },
        {
            "id": "home",
            "name": "Home",
            "children": [child("c", "Same", "Home", "home"), child("d", "a", "Home", "home")],
        },
        {"id": "projects", "name": "🧰 Проекты", "children": []},
    ]
    state = {"groups": groups, "revision": "r1", "failure": None, "posted": False}
    calls = []

    def respond(request):
        calls.append(request)
        if request.url.path.endswith("/budgets"):
            return httpx.Response(
                200, json={"budgets": [{"id": "fictional-id", "name": "Fictional бюджет"}]}
            )
        if request.method == "POST":
            state["posted"] = True
            if state["failure"] == "stale":
                return httpx.Response(409, json={"error": "stale"})
            body = json.loads(request.content)
            p = body["payload"]
            result = None
            if body["operation"] == "add_category_group":
                result = "new-group"
                groups.append({"id": result, "name": p["name"].strip(), "children": []})
            elif body["operation"] == "add_category":
                result = "new-child"
                parent = next(row for row in groups if row["id"] == p["parentId"])
                parent["children"].append(
                    {
                        **child(result, p["name"].strip(), parent["name"], parent["id"]),
                        "target": p["target"],
                        "currency": p["currency"],
                    }
                )
            else:
                source = next(
                    group
                    for group in groups
                    if any(row["id"] == p["categoryId"] for row in group["children"])
                )
                row = next(row for row in source["children"] if row["id"] == p["categoryId"])
                source["children"].remove(row)
                parent = next(group for group in groups if group["id"] == p["parentId"])
                parent["children"].append(
                    {**row, "group": parent["name"], "parentId": parent["id"]}
                )
            state["revision"] = "r2"
            if state["failure"] == "lost":
                raise httpx.ReadTimeout("private transport details")
            return httpx.Response(
                200,
                json={
                    "budgetId": "foreign" if state["failure"] == "wrong-write" else "fictional-id",
                    "revision": "r2",
                    "data": None
                    if state["failure"] == "invalid-id"
                    else "missing"
                    if state["failure"] == "missing-id"
                    else result,
                },
            )
        if state["posted"] and state["failure"] == "lost-verification":
            raise httpx.ReadError("private transport details")
        if state["posted"] and state["failure"] == "changed-result":
            groups[-1]["children"][-1]["target"] = 999
        return httpx.Response(
            200,
            json={
                "budgetId": "foreign" if state["failure"] == "wrong-read" else "fictional-id",
                "revision": "r3"
                if state["posted"] and state["failure"] == "changed-revision"
                else state["revision"],
                "data": groups,
            },
        )

    @contextmanager
    def connection(*args, **kwargs):
        with httpx.Client(
            base_url="https://localhost", transport=httpx.MockTransport(respond)
        ) as client:
            yield client

    monkeypatch.setattr(console, "local_connection", connection)
    monkeypatch.setattr(console.Path, "home", lambda: tmp_path)
    return state, calls


def run(*args):
    return console.main(["Fictional бюджет", "category", *args])


def test_list_all_filtered_tables_and_budget_id(category_transport, capsys):
    assert run("list") == 0
    assert len(json.loads(capsys.readouterr().out)["data"]) == 3
    assert console.main(["fictional-id", "category", "list", "--parent", " ПРОЕКТЫ "]) == 1
    assert "Candidates" in capsys.readouterr().err
    assert run("list", "--parent", "🧰 проекты", "--format", "table") == 0
    assert "🧰 Проекты" in capsys.readouterr().out
    assert run("list", "--parent", "finance", "--format", "table") == 0
    assert "Finance / Example 项目" in capsys.readouterr().out
    assert all(call.method == "GET" for call in category_transport[1])


@pytest.mark.parametrize(
    "arguments,operation,expected",
    [
        (
            ["group", "create", " Fictional new ", "--format", "table"],
            "add_category_group",
            {"name": " Fictional new "},
        ),
        (
            [
                "create",
                "Fictional new",
                "--parent",
                "🧰 Проекты",
                "--target",
                "234",
                "--currency",
                "JPY",
            ],
            "add_category",
            {"name": "Fictional new", "parentId": "projects", "target": 234, "currency": "JPY"},
        ),
        (
            ["move", "finance / example 项目", "--parent", "projects", "--format", "table"],
            "move_category",
            {"categoryId": "a", "parentId": "projects"},
        ),
        (
            ["move", "a", "--parent", "🧰 проекты"],
            "move_category",
            {"categoryId": "a", "parentId": "projects"},
        ),
    ],
)
def test_successful_verified_single_intents(
    category_transport, capsys, arguments, operation, expected
):
    assert run(*arguments) == 0
    result = capsys.readouterr().out
    if "--format" not in arguments:
        saved = json.loads(result)
        assert (
            saved["budgetId"] == "fictional-id" and saved["revision"] == "r2" and saved["requestId"]
        )
    else:
        assert "Budget: Fictional бюджет (fictional-id)" in result
    posts = [call for call in category_transport[1] if call.method == "POST"]
    assert len(posts) == 1
    intent = json.loads(posts[0].content)
    assert (
        intent["operation"] == operation
        and intent["payload"] == expected
        and intent["expected_revision"] == "r1"
    )
    assert len(intent["request_id"]) == 36


@pytest.mark.parametrize(
    "arguments",
    [
        ["group", "create", "FINANCE"],
        ["create", "same", "--parent", "Finance"],
        ["move", "Same", "--parent", "projects"],
        ["move", "missing", "--parent", "projects"],
        ["move", "a", "--parent", "a"],
        ["list", "--parent", "missing"],
    ],
)
def test_missing_ambiguous_and_duplicate_names_do_not_write(category_transport, capsys, arguments):
    assert run(*arguments) == 1
    message = json.loads(capsys.readouterr().err)["error"]
    assert "Candidates" in message or "already exists" in message
    assert not any(call.method == "POST" for call in category_transport[1])


def test_ambiguous_parents_and_destination_duplicates(category_transport, capsys):
    state, calls = category_transport
    state["groups"].append({"id": "other-finance", "name": "finance", "children": []})
    assert run("create", "New", "--parent", "Finance") == 1
    assert "ambiguous" in capsys.readouterr().err
    state["groups"][-1]["children"].append(
        {
            "id": "duplicate",
            "name": "Example 项目",
            "parentId": "other-finance",
            "group": "finance",
            "target": 0,
            "currency": "USD",
        }
    )
    assert run("move", "a", "--parent", "other-finance") == 1
    assert "destination" in capsys.readouterr().err
    assert not any(call.method == "POST" for call in calls)


@pytest.mark.parametrize(
    "failure",
    [
        "stale",
        "lost",
        "wrong-write",
        "changed-revision",
        "changed-result",
        "lost-verification",
        "invalid-id",
        "missing-id",
    ],
)
def test_uncertain_or_changed_responses_never_replay(category_transport, capsys, failure):
    state, calls = category_transport
    state["failure"] = failure
    assert run("create", "Fictional", "--parent", "projects") == 1
    message = json.loads(capsys.readouterr().err)["error"]
    assert (
        "No automatic retry" in message
        and "expected_revision" in message
        and "request_id" in message
    )
    assert "private transport details" not in message
    assert len([call for call in calls if call.method == "POST"]) == 1


def test_foreign_initial_budget_is_refused(category_transport, capsys):
    category_transport[0]["failure"] = "wrong-read"
    assert run("list") == 1
    assert "another budget" in capsys.readouterr().err


def test_invalid_identity_is_a_controlled_error():
    with pytest.raises(ValueError, match="invalid category identity"):
        resolve_category([], None, ValueError)


@pytest.mark.parametrize(
    "arguments", [[], ["list"], ["group"], ["group", "create"], ["create"], ["move"]]
)
def test_detailed_offline_help_without_profile_or_host(monkeypatch, tmp_path, capsys, arguments):
    monkeypatch.setattr(console.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(
        console, "local_connection", lambda *a, **k: pytest.fail("Help must remain offline")
    )
    with pytest.raises(SystemExit) as stop:
        console.main(
            [
                "--profile",
                "/missing/fictional-profile",
                "Fictional budget",
                "category",
                *arguments,
                "--help",
            ]
        )
    assert stop.value.code == 0
    help_text = capsys.readouterr().out
    for phrase in [
        "category group create",
        "Group / Name",
        "No fuzzy",
        "monthly assignments",
        "No bank",
        "uncertain",
        "no manual reorder",
    ]:
        assert phrase in help_text
