"""Synthetic export fixtures verify normalization without personal budget data."""

import csv
import io
import stat
import zipfile
from dataclasses import FrozenInstanceError

import pytest
from whab_plugin_ynab import parser
from whab_plugin_ynab.parser import BUDGET_HEADERS, REGISTER_HEADERS, parse_dump

from whab.domain.errors import StoreError


def budget(**changes):
    return {
        "Month": "Oct 2026", "Category Group/Category": "Living: Food",
        "Category Group": "Living", "Category": "Food", "Assigned": "$100.00",
        "Activity": "-$12.34", "Available": "$87.66", **changes,
    }


def transaction(**changes):
    return {
        "Account": "Checking", "Flag": "Blue", "Date": "2026-10-01", "Payee": "Fixture shop",
        "Category Group/Category": "Living: Food", "Category Group": "Living", "Category": "Food",
        "Memo": "Synthetic memo", "Outflow": "$12.34", "Inflow": "$0.00", "Cleared": "Cleared",
        **changes,
    }


def table(headers, rows, *, delimiter="\t", encoding="utf-8"):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, headers, delimiter=delimiter)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode(encoding)


def archive(*, budgets=None, transactions=None, delimiter="\t", encoding="utf-8", extra=()):
    stream = io.BytesIO()
    extension = "tsv" if delimiter == "\t" else "csv"
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as exported:
        exported.writestr(
            f"Export/Budget.{extension}",
            table(BUDGET_HEADERS, [budget()] if budgets is None else budgets,
                  delimiter=delimiter, encoding=encoding),
        )
        exported.writestr(
            f"Export/Register.{extension}",
            table(REGISTER_HEADERS, [transaction()] if transactions is None else transactions,
                  delimiter=delimiter, encoding=encoding),
        )
        for name, contents in extra:
            exported.writestr(name, contents)
    return stream.getvalue()


@pytest.mark.parametrize("delimiter", ["\t", ","])
@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16"])
def test_csv_tsv_and_unicode_encodings_normalize_to_frozen_exact_cent_records(delimiter, encoding):
    data = archive(delimiter=delimiter, encoding=encoding)
    dump = parse_dump(data)
    account, = dump.accounts
    category, = dump.categories
    imported, = dump.transactions
    assigned, = dump.assignments
    assert account.name == "Checking"
    assert account.balance_cents == -1234
    assert category.name == "Food"
    assert category.group == "Living"
    assert imported.account_id == account.id
    assert imported.category_id == category.id == assigned.category_id
    assert imported.date == "2026-10-01"
    assert imported.amount_cents == 1234
    assert (imported.payee, imported.memo, imported.flag) == (
        "Fixture shop", "Synthetic memo", "Blue",
    )
    assert imported.cleared == "cleared"
    assert not imported.is_transfer
    assert not imported.is_split
    assert imported.transfer_account_id is None
    assert (assigned.month, assigned.assigned_cents, assigned.activity_cents,
            assigned.available_cents) == ("2026-10", 10000, -1234, 8766)
    assert dump.warnings == parser.WARNINGS
    with pytest.raises(FrozenInstanceError):
        account.name = "Changed"


def test_duplicate_and_split_rows_are_retained_and_ids_survive_archive_reordering():
    duplicate = transaction()
    split = transaction(**{"Category Group/Category": "-- Split --", "Category Group": "",
                           "Category": "", "Outflow": "0", "Memo": "First line\nSecond line"})
    different = transaction(Payee="Other fixture", Inflow="$4.56", Outflow="")
    rows = [duplicate, split, duplicate, different]
    first = parse_dump(archive(transactions=rows))
    second = parse_dump(archive(transactions=list(reversed(rows))))
    assert len(first.transactions) == 4
    assert len({r.id for r in first.transactions}) == 4
    assert {r.id for r in first.transactions} == {r.id for r in second.transactions}
    assert first.accounts == second.accounts
    assert first.transactions[1].is_split
    assert first.transactions[1].amount_cents == 0
    assert first.transactions[1].memo == "First line\nSecond line"
    assert first.transactions[1].category_id is None


def test_transfers_only_detected_for_uncategorized_rows_and_income_stays_uncategorized():
    bare = {"Category Group/Category": "", "Category Group": "", "Category": ""}
    rows = [
        transaction(**bare, Payee="Transfer : Savings"),
        transaction(Account="Savings", **bare, Payee="Transfer : Checking", Outflow="0", Inflow="12.34"),
        transaction(Payee="Transfer : Savings"),
        transaction(**{"Category Group/Category": "Inflow: Ready to Assign", "Category Group": "Inflow",
                       "Category": "Ready to Assign", "Payee": "Transfer : Employer"}),
        transaction(**{"Category Group/Category": "Inflow: To be Budgeted", "Category Group": "Inflow",
                       "Category": "To be Budgeted"}),
        transaction(**bare, Payee="Transfer : ", Cleared="Reconciled"),
        transaction(**bare, Payee="", Flag="", Memo="", Cleared="Uncleared"),
        transaction(**{"Category Group/Category": "Split", "Category Group": "", "Category": ""}),
    ]
    dump = parse_dump(archive(transactions=rows))
    accounts = {a.name: a.id for a in dump.accounts}
    assert dump.transactions[0].is_transfer
    assert dump.transactions[0].transfer_account_id == accounts["Savings"]
    assert dump.transactions[1].transfer_account_id == accounts["Checking"]
    assert not dump.transactions[2].is_transfer
    assert not dump.transactions[3].is_transfer
    assert dump.transactions[3].category_id is None
    assert dump.transactions[4].category_id is None
    assert dump.transactions[5].is_transfer
    assert dump.transactions[5].transfer_account_id is None
    assert dump.transactions[5].cleared == "reconciled"
    assert dump.transactions[6].cleared == "uncleared"
    assert dump.transactions[7].is_split
    assert len(dump.categories) == 1


@pytest.mark.parametrize(("amount", "expected"), [
    ("", 0), ("0", 0), ("$1,234.56", 123456), ("USD 1.2", 120),
    ("(USD 1.00)", -100), ("-$1.00", -100), ("$-1.00", -100),
    (" 12 ", 1200), ("10000000000", 1_000_000_000_000),
])
def test_usd_amounts_are_exact_without_float_rounding(amount, expected):
    dump = parse_dump(archive(transactions=[transaction(Outflow=amount)]))
    assert dump.transactions[0].amount_cents == expected


@pytest.mark.parametrize("amount", [
    "€1.00", "CAD 1.00", "1.001", "NaN", "Infinity", "1e3", "1,23.00", "+$1.00",
    "--1.00", "(-$1.00)", "(1.00", "10000000000.01", "1" * 65,
])
def test_foreign_ambiguous_or_oversized_amounts_fail_with_sanitized_errors(amount):
    with pytest.raises(StoreError, match="YNAB amount") as error:
        parse_dump(archive(transactions=[transaction(Outflow=amount)]))
    assert amount not in str(error.value)


def test_budget_month_and_us_date_and_empty_tables_are_supported():
    dump = parse_dump(archive(
        budgets=[budget(Month="2026-01")],
        transactions=[transaction(Date="01/31/2026")],
    ))
    assert dump.assignments[0].month == "2026-01"
    assert dump.transactions[0].date == "2026-01-31"
    empty = parse_dump(archive(budgets=[], transactions=[], extra=[("Notes.txt", b"ignored"), ("Folder/", b"")]))
    assert empty.accounts == empty.categories == empty.transactions == empty.assignments == ()


@pytest.mark.parametrize("value", ["2026-13", "0000-01", "Nop 2026", "January 2026", "Oct 0000"])
def test_invalid_months_fail(value):
    with pytest.raises(StoreError, match="budget month"):
        parse_dump(archive(budgets=[budget(Month=value)]))


@pytest.mark.parametrize("value", ["2026-02-30", "02/30/2026", "31/01/2026", "2026-1-01", "tomorrow"])
def test_invalid_dates_fail(value):
    with pytest.raises(StoreError, match="transaction date"):
        parse_dump(archive(transactions=[transaction(Date=value)]))


@pytest.mark.parametrize("changes", [
    {"Account": " "}, {"Account": "a" * 201}, {"Memo": "a" * 10001},
    {"Flag": "a" * 201}, {"Payee": "a" * 1001}, {"Category Group/Category": "a" * 501},
    {"Category": "", "Category Group/Category": ""},
    {"Category": "", "Category Group": "", "Category Group/Category": "broken"},
    {"Category Group": ""}, {"Category Group/Category": "mismatch"}, {"Cleared": "private status"},
])
def test_invalid_record_text_and_category_fields_fail_without_echoing_source(changes):
    with pytest.raises(StoreError, match="YNAB") as error:
        parse_dump(archive(transactions=[transaction(**changes)]))
    assert "private status" not in str(error.value)


def test_budget_rejects_missing_categories_and_duplicate_assignments():
    empty = {"Category Group/Category": "", "Category Group": "", "Category": ""}
    with pytest.raises(StoreError, match="expense category"):
        parse_dump(archive(budgets=[budget(**empty)]))
    with pytest.raises(StoreError, match="duplicate month/category"):
        parse_dump(archive(budgets=[budget(), budget()]))


def test_transaction_net_amount_and_account_running_balance_are_bounded():
    with pytest.raises(StoreError, match="transaction exceeds"):
        parse_dump(archive(transactions=[transaction(Outflow="10000000000", Inflow="-0.01")]))
    with pytest.raises(StoreError, match="account balance exceeds"):
        parse_dump(archive(transactions=[transaction(Outflow="10000000000"), transaction(Outflow="0.01")]))


@pytest.mark.parametrize("data", [b"", b"private filename.zip", "not bytes"])
def test_invalid_zip_data_is_safely_rejected(data):
    with pytest.raises(StoreError, match="YNAB") as error:
        parse_dump(data)
    assert "private filename" not in str(error.value)


def test_archive_byte_member_count_and_expansion_limits(monkeypatch):
    data = archive()
    monkeypatch.setattr(parser, "MAX_ARCHIVE_BYTES", len(data) - 1)
    with pytest.raises(StoreError, match="upload limit"):
        parse_dump(data)
    monkeypatch.setattr(parser, "MAX_ARCHIVE_BYTES", len(data))
    monkeypatch.setattr(parser, "MAX_MEMBERS", 1)
    with pytest.raises(StoreError, match="archive limits"):
        parse_dump(data)
    monkeypatch.setattr(parser, "MAX_MEMBERS", 2)
    monkeypatch.setattr(parser, "MAX_UNCOMPRESSED_BYTES", 1)
    with pytest.raises(StoreError, match="archive limits"):
        parse_dump(data)


@pytest.mark.parametrize("member", ["/private.txt", "../private.tsv", "..\\private.tsv"])
def test_unsafe_archive_names_are_rejected_without_extraction(member):
    with pytest.raises(StoreError, match="unsafe member"):
        parse_dump(archive(extra=[(member, b"secret")]))


def test_symlink_archive_member_is_rejected():
    link = zipfile.ZipInfo("link.tsv")
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(StoreError, match="unsafe member"):
        parse_dump(archive(extra=[(link, b"private-target")]))


def test_encrypted_archive_member_is_rejected(monkeypatch):
    original = zipfile.ZipFile.infolist

    def encrypted(archive):
        members = original(archive)
        members[0].flag_bits |= 1
        return members

    data = archive()
    monkeypatch.setattr(zipfile.ZipFile, "infolist", encrypted)
    with pytest.raises(StoreError, match="encrypted or oversized"):
        parse_dump(data)


def test_member_declared_size_and_actual_read_are_bounded(monkeypatch):
    data = archive()
    monkeypatch.setattr(parser, "MAX_MEMBER_BYTES", 1)
    with pytest.raises(StoreError, match="encrypted or oversized"):
        parse_dump(data)
    monkeypatch.setattr(parser, "MAX_MEMBER_BYTES", 4096)
    monkeypatch.setattr(zipfile.ZipExtFile, "read", lambda self, size: b"x" * size)
    with pytest.raises(StoreError, match="oversized member"):
        parse_dump(data)


def test_row_limit_rejects_excess_rows(monkeypatch):
    monkeypatch.setattr(parser, "MAX_ROWS", 1)
    with pytest.raises(StoreError, match="too many rows"):
        parse_dump(archive(transactions=[transaction(), transaction()]))


@pytest.mark.parametrize("raw", [
    b"", b"Wrong,Headers\n1,2\n", b"\xffinvalid", b"\x00invalid",
    table(REGISTER_HEADERS, []) + b"one\ttwo\n",
    table(REGISTER_HEADERS, [transaction()]) + b"\t" * len(REGISTER_HEADERS) + b"extra\n",
    table(REGISTER_HEADERS, []) + b'"unclosed\n',
])
def test_bad_table_encoding_headers_and_row_shapes_are_sanitized(raw):
    with pytest.raises(StoreError, match="YNAB"):
        parse_dump(archive(extra=[("invalid.tsv", raw)]))


def test_missing_or_duplicate_tables_are_rejected():
    with pytest.raises(StoreError, match="duplicate Budget or Register"):
        parse_dump(archive(extra=[("second.tsv", table(REGISTER_HEADERS, []))]))
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as exported:
        exported.writestr("Budget.tsv", table(BUDGET_HEADERS, []))
    with pytest.raises(StoreError, match="one YNAB Budget table and one YNAB Register"):
        parse_dump(stream.getvalue())
