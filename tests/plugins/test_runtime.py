"""Public plugin contracts using fake installed entry points and fictional ledger data."""

from dataclasses import FrozenInstanceError, dataclass, replace

import pytest

from whab.plugins import (
    ENTRYPOINT_GROUP,
    AccountSnapshot,
    BudgetSnapshot,
    CategorySnapshot,
    ExportTable,
    Insight,
    Plugin,
    PluginError,
    TransactionSnapshot,
    copy_snapshot,
    load_plugins,
)


@dataclass
class FakeEntryPoint:
    name: str
    value: object
    group: str = ENTRYPOINT_GROUP
    loaded: int = 0

    def load(self):
        self.loaded += 1
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


@pytest.fixture
def snapshot():
    return BudgetSnapshot(
        workspace="demo",
        month="2026-10",
        accounts=(AccountSnapshot("wallet", "Fictional wallet", "depository", "checking", 20000),),
        categories=(CategorySnapshot("groceries", "Groceries", "Everyday", 10000, 1500, 8500),),
        transactions=(
            TransactionSnapshot(
                "purchase", "2026-10-01", "Example shop", 2000, "wallet", "groceries"
            ),
            TransactionSnapshot(
                "refund", "2026-10-02", "Example shop", -500, "wallet", "groceries"
            ),
            TransactionSnapshot("income", "2026-10-03", "Example employer", -10000, "wallet", None),
            TransactionSnapshot(
                "pending", "2026-10-04", "Example shop", 800, "wallet", None, pending=True
            ),
            TransactionSnapshot(
                "transfer", "2026-10-05", "Example transfer", 5000, "wallet", None, is_transfer=True
            ),
        ),
    )


def test_empty_configuration_does_not_discover_or_load_plugins(monkeypatch):
    def unexpected_discovery(**kwargs):
        pytest.fail("Disabled plugins must not even trigger metadata discovery")

    monkeypatch.setattr("whab.plugins.runtime.metadata.entry_points", unexpected_discovery)
    installed = FakeEntryPoint("example", RuntimeError("must never run"))
    assert load_plugins(()).describe() == ()
    assert load_plugins((), (installed,)).describe() == ()
    assert installed.loaded == 0


def test_only_explicitly_enabled_group_is_loaded(snapshot):
    enabled = FakeEntryPoint(
        "one", Plugin("one", "Enabled", insights=lambda _: (Insight("A", "1"),))
    )
    disabled = FakeEntryPoint("two", RuntimeError("must never run"))
    other_group = FakeEntryPoint("one", RuntimeError("must never run"), group="another.plugins")
    registry = load_plugins(("one",), (disabled, other_group, enabled))
    assert enabled.loaded == 1
    assert disabled.loaded == other_group.loaded == 0
    (description,) = registry.describe()
    assert description.id == "one"
    assert description.has_insights and not description.has_export
    assert registry.insights(snapshot)[0].insights == (Insight("A", "1"),)


@pytest.mark.parametrize(
    ("names", "code"),
    [
        ("example", "invalid_configuration"),
        (("example", "example"), "duplicate_configuration"),
        (("INVALID",), "invalid_configuration"),
        (("private/secret",), "invalid_configuration"),
    ],
)
def test_invalid_configuration_is_rejected_before_loading(names, code):
    entry = FakeEntryPoint("example", RuntimeError("must never run"))
    with pytest.raises(PluginError) as error:
        load_plugins(names, (entry,))
    assert error.value.code == code
    assert "private/secret" not in str(error.value)
    assert entry.loaded == 0


def test_missing_name_is_preflighted_before_any_plugin_load():
    first = FakeEntryPoint("first", Plugin("first", "First", insights=lambda _: ()))
    with pytest.raises(PluginError, match="not installed") as error:
        load_plugins(("first", "missing"), (first,))
    assert error.value.code == "missing"
    assert first.loaded == 0


def test_duplicate_entrypoints_fail_deterministically_before_loading():
    entries = (FakeEntryPoint("example", None), FakeEntryPoint("example", None))
    with pytest.raises(PluginError, match="registered more than once") as error:
        load_plugins(("example",), entries)
    assert error.value.code == "duplicate_entrypoint"
    assert all(entry.loaded == 0 for entry in entries)


def test_api_mismatch_is_actionable_and_rejected():
    entry = FakeEntryPoint(
        "example", Plugin("example", "Example", api_version=2, insights=lambda _: ())
    )
    with pytest.raises(PluginError, match="unsupported API version") as error:
        load_plugins(("example",), (entry,))
    assert error.value.code == "incompatible_api"


def test_descriptor_ids_must_match_entrypoint_names():
    entries = (
        FakeEntryPoint("one", Plugin("one", "One", insights=lambda _: ())),
        FakeEntryPoint("two", Plugin("one", "Duplicate ID", insights=lambda _: ())),
    )
    with pytest.raises(PluginError) as error:
        load_plugins(("one", "two"), entries)
    assert error.value.code == "id_mismatch"


@pytest.mark.parametrize("value", [None, {"id": "example"}, lambda: None])
def test_only_typed_descriptors_are_supported(value):
    with pytest.raises(PluginError, match="Plugin descriptor") as error:
        load_plugins(("example",), (FakeEntryPoint("example", value),))
    assert error.value.code == "invalid_descriptor"


def test_load_and_discovery_errors_do_not_expose_exception_content(monkeypatch):
    secret_marker = "private-fixture-token-and-payee"
    with pytest.raises(PluginError) as error:
        load_plugins(("example",), (FakeEntryPoint("example", RuntimeError(secret_marker)),))
    assert secret_marker not in str(error.value)
    assert error.value.code == "load_failed"
    assert error.value.__suppress_context__

    def broken_discovery(**kwargs):
        raise RuntimeError(secret_marker)

    monkeypatch.setattr("whab.plugins.runtime.metadata.entry_points", broken_discovery)
    with pytest.raises(PluginError) as error:
        load_plugins(("example",))
    assert secret_marker not in str(error.value)
    assert error.value.code == "discovery_failed"


def test_snapshot_is_recursively_frozen_and_has_no_secret_runtime_objects(snapshot):
    with pytest.raises(FrozenInstanceError):
        snapshot.workspace = "personal"
    with pytest.raises(FrozenInstanceError):
        snapshot.accounts[0].balance_cents = 0
    with pytest.raises(TypeError):
        snapshot.transactions[0] = snapshot.transactions[1]
    for forbidden in ("store", "settings", "access_token", "items", "notes", "state"):
        assert not hasattr(snapshot, forbidden)
        assert not hasattr(snapshot.accounts[0], forbidden)
        assert not hasattr(snapshot.transactions[0], forbidden)
    fresh = copy_snapshot(snapshot)
    assert fresh == snapshot and fresh is not snapshot
    assert fresh.accounts[0] is not snapshot.accounts[0]
    assert fresh.transactions[0] is not snapshot.transactions[0]


def test_snapshot_rejects_cross_month_unknown_account_or_category(snapshot):
    for transaction in (
        replace(snapshot.transactions[0], date="2026-09-30"),
        replace(snapshot.transactions[0], account_id="other-workspace-account"),
        replace(snapshot.transactions[0], category_id="other-workspace-category"),
    ):
        with pytest.raises(ValueError):
            replace(snapshot, transactions=(transaction,))
    with pytest.raises(ValueError):
        replace(snapshot, workspace="all")
    with pytest.raises(ValueError):
        replace(snapshot, currency="EUR")
    with pytest.raises(ValueError):
        replace(snapshot, transactions=(snapshot.transactions[0], snapshot.transactions[0]))


def test_snapshot_refuses_mutable_collections_or_noninteger_money(snapshot):
    with pytest.raises(ValueError):
        replace(snapshot, accounts=list(snapshot.accounts))
    for amount in (True, 10.25, "1250"):
        with pytest.raises(ValueError):
            replace(snapshot.transactions[0], amount_cents=amount)


def test_each_hook_receives_fresh_scoped_snapshot_even_if_trusted_code_bypasses_frozen(snapshot):
    seen = []

    def first_hook(data):
        seen.append(data)
        object.__setattr__(data.accounts[0], "name", "poisoned fixture")
        return (Insight("First", "ok"),)

    def second_hook(data):
        seen.append(data)
        assert data.accounts[0].name == "Fictional wallet"
        assert data.workspace == "demo" and data.month == "2026-10"
        return (Insight("Second", "ok"),)

    registry = load_plugins(
        ("one", "two"),
        (
            FakeEntryPoint(
                "one",
                Plugin(
                    "one",
                    "One",
                    insights=first_hook,
                    export=lambda data: ExportTable(("name",), ((data.accounts[0].name,),)),
                ),
            ),
            FakeEntryPoint("two", Plugin("two", "Two", insights=second_hook)),
        ),
    )
    results = registry.insights(snapshot)
    assert all(result.error is None for result in results)
    assert seen[0] is not seen[1] and seen[1] is not snapshot
    assert snapshot.accounts[0].name == "Fictional wallet"
    assert registry.export("one", snapshot).rows == (("Fictional wallet",),)


def test_failed_insights_are_contained_and_other_plugins_keep_working(snapshot, caplog):
    def broken(data):
        raise RuntimeError("private-fixture-token-and-payee")

    registry = load_plugins(
        ("broken", "healthy"),
        (
            FakeEntryPoint("broken", Plugin("broken", "Broken", insights=broken)),
            FakeEntryPoint(
                "healthy",
                Plugin("healthy", "Healthy", insights=lambda data: (Insight("Month", data.month),)),
            ),
        ),
    )
    failed, healthy = registry.insights(snapshot)
    assert failed.insights == () and failed.error
    assert healthy.insights == (Insight("Month", "2026-10"),) and healthy.error is None
    assert "private-fixture" not in repr(failed)
    assert "private-fixture" not in caplog.text


@pytest.mark.parametrize(
    "output", [[], ("arbitrary HTML",), tuple(Insight("A", "B") for _ in range(51))]
)
def test_invalid_insight_outputs_are_contained(snapshot, output):
    registry = load_plugins(
        ("example",),
        (FakeEntryPoint("example", Plugin("example", "Example", insights=lambda data: output)),),
    )
    assert registry.insights(snapshot)[0].error


def test_html_aware_strings_cannot_bypass_host_escaping():
    class HtmlString(str):
        def __html__(self):
            return "<script>fixture()</script>"

    with pytest.raises(ValueError):
        Insight("Heading", HtmlString("value"))
    with pytest.raises(ValueError):
        ExportTable(("heading",), ((HtmlString("value"),),))


def test_export_returns_structured_primitives_and_host_owns_formula_escaping(snapshot):
    table = ExportTable(("payee", "amount_cents"), (("=SUM(1,2)", -1500),))
    registry = load_plugins(
        ("example",),
        (FakeEntryPoint("example", Plugin("example", "Example", export=lambda data: table)),),
    )
    output = registry.export("example", snapshot)
    assert output == table and output is not table
    assert output.rows[0][1] == -1500
    assert output.rows[0][0].startswith(
        "="
    )  # Host performs CSV formula protection, never the plugin.


@pytest.mark.parametrize("output", ["/tmp/private.csv", {"columns": ["A"]}, (("A",), ((1,),))])
def test_unstructured_export_results_are_rejected_without_exception_details(snapshot, output):
    registry = load_plugins(
        ("example",),
        (FakeEntryPoint("example", Plugin("example", "Example", export=lambda data: output)),),
    )
    with pytest.raises(PluginError, match="could not be generated") as error:
        registry.export("example", snapshot)
    assert error.value.code == "hook_failed"
    assert "private.csv" not in str(error.value)


def test_failed_export_has_sanitized_message_and_unknown_exports_are_unavailable(snapshot):
    def broken(data):
        raise RuntimeError("private-fixture-token-and-payee")

    registry = load_plugins(
        ("example",), (FakeEntryPoint("example", Plugin("example", "Example", export=broken)),)
    )
    with pytest.raises(PluginError) as error:
        registry.export("example", snapshot)
    assert "private-fixture" not in str(error.value)
    assert error.value.__suppress_context__
    for name in ("disabled", "../../private"):
        with pytest.raises(PluginError) as error:
            registry.export(name, snapshot)
        assert error.value.code == "unavailable"


@pytest.mark.parametrize(
    ("columns", "rows"),
    [
        ([], ()),
        ((), ()),
        (("A",), [[1]]),
        (("A",), ((1, 2),)),
        (("A",), ((1.25,),)),
        (("A",), (({},),)),
        (("A",), ((object(),),)),
    ],
)
def test_export_table_rejects_malformed_shapes_and_nonprimitive_cells(columns, rows):
    with pytest.raises(ValueError):
        ExportTable(columns, rows)
