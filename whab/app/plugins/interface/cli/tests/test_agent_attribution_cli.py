"""Agent attribution is explicit and never inferred from CLI use."""

import json

import pytest
from whab_plugin_cli import client


def arguments(parameters, *extra):
    return client.parser().parse_args(
        [
            "--url",
            "https://localhost:5173",
            "--budget",
            "fictional",
            "command",
            "edit_transaction",
            "--parameters",
            json.dumps(parameters),
            "--revision",
            "fictional-revision",
            "--request-id",
            "fictional-intent",
            *extra,
        ]
    )


def test_plain_cli_category_write_does_not_claim_an_agent():
    args = arguments({"transactionId": "transaction", "categoryId": "groceries"})
    _, _, _, kwargs = client.request(args, "x" * 32)
    assert "categoryAgent" not in kwargs["json"]["payload"]


@pytest.mark.parametrize("existing", [False, True])
def test_explicit_flag_adds_only_the_agent_annotation(existing):
    values = {"transactionId": "transaction", "categoryId": "groceries"}
    if existing:
        values["categoryAgent"] = "Codex"
    args = arguments(values, "--agent", "Codex")
    _, _, _, kwargs = client.request(args, "x" * 32)
    assert kwargs["json"]["payload"] == {**values, "categoryAgent": "Codex"}
    assert kwargs["json"]["expected_revision"] == "fictional-revision"
    assert kwargs["json"]["request_id"] == "fictional-intent"


@pytest.mark.parametrize(
    "parameters",
    [
        {"transactionId": "transaction", "reviewed": True},
        {"transactionId": "transaction", "categoryId": "groceries", "categoryAgent": "Other"},
    ],
)
def test_flag_cannot_annotate_review_only_or_conflicting_intents(parameters):
    args = arguments(parameters, "--agent", "Codex")
    with pytest.raises(client.ConfigError):
        client.request(args, "x" * 32)


def test_agent_flag_is_rejected_for_an_unrelated_command():
    args = arguments({"name": "Fictional", "type": "depository", "balance": 0}, "--agent", "Codex")
    args.operation = "add_account"
    with pytest.raises(client.ConfigError):
        client.request(args, "x" * 32)


@pytest.mark.parametrize("label", ["", " bad", "bad\nlabel", "x" * 81])
def test_invalid_flag_label_is_rejected_by_offline_argument_validation(label):
    with pytest.raises(SystemExit) as error:
        arguments({"transactionId": "transaction", "categoryId": "groceries"}, "--agent", label)
    assert error.value.code == 2
