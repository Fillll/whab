"""Bounded, pure normalization of YNAB Budget/Register export archives.

YNAB's export omits currency metadata; callers must obtain an explicit USD
confirmation before importing these records. No archive member is extracted.
"""

import csv
import hashlib
import io
import json
import re
import stat
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import PurePosixPath

from whab.domain.errors import StoreError
from whab.domain.money import MAX_CENTS

MAX_ARCHIVE_BYTES = 25 * 1024 * 1024
MAX_MEMBER_BYTES = 25 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_MEMBERS = 32
MAX_ROWS = 250_000
BUDGET_HEADERS = (
    "Month", "Category Group/Category", "Category Group", "Category", "Assigned", "Activity",
    "Available",
)
REGISTER_HEADERS = (
    "Account", "Flag", "Date", "Payee", "Category Group/Category", "Category Group", "Category",
    "Memo", "Outflow", "Inflow", "Cleared",
)
WARNINGS = (
    "YNAB exports do not identify currency. Confirm this budget uses USD before importing.",
    "Account balances are calculated from exported transactions; account types and closed status "
    "are absent from the export.",
    "Targets, scheduled transactions, account metadata and historical category identities are "
    "absent from the export. Renamed categories cannot be matched automatically.",
)


@dataclass(frozen=True)
class YnabAccount:
    id: str
    name: str
    balance_cents: int


@dataclass(frozen=True)
class YnabCategory:
    id: str
    name: str
    group: str


@dataclass(frozen=True)
class YnabTransaction:
    id: str
    account_id: str
    date: str
    amount_cents: int
    payee: str
    memo: str
    category_id: str | None
    cleared: str
    flag: str
    is_transfer: bool
    transfer_account_id: str | None
    is_split: bool


@dataclass(frozen=True)
class YnabAssignment:
    id: str
    month: str
    category_id: str
    assigned_cents: int
    activity_cents: int
    available_cents: int


@dataclass(frozen=True)
class YnabDump:
    accounts: tuple[YnabAccount, ...]
    categories: tuple[YnabCategory, ...]
    transactions: tuple[YnabTransaction, ...]
    assignments: tuple[YnabAssignment, ...]
    warnings: tuple[str, ...]


def _identifier(kind: str, *values: str) -> str:
    encoded = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return f"ynab-{kind}-{hashlib.sha256(encoded).hexdigest()[:32]}"


def _amount(value: str) -> int:
    value = value.strip()
    if len(value) > 64:
        raise StoreError("Invalid YNAB amount.")
    if not value:
        return 0
    parentheses = value.startswith("(") and value.endswith(")")
    if parentheses:
        value = value[1:-1]
    matched = re.fullmatch(
        r"(?P<sign>-?)(?:USD\s*|\$)?(?P<inner>-?)"
        r"(?P<number>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?)",
        value,
    )
    if not matched:
        raise StoreError("Invalid YNAB amount. Only USD amounts with at most two decimals are supported.")
    negative_signs = bool(matched["sign"]) + bool(matched["inner"]) + parentheses
    if negative_signs > 1:
        raise StoreError("Invalid YNAB amount.")
    whole, _, fractional = matched["number"].replace(",", "").partition(".")
    result = int(whole) * 100 + int(fractional.ljust(2, "0") or "0")
    if result > MAX_CENTS:
        raise StoreError("YNAB amount exceeds the supported range.")
    return -result if negative_signs else result


def _label(value: str, *, required: bool = False, limit: int = 200) -> str:
    if len(value) > limit or (required and not value.strip()):
        raise StoreError("Invalid YNAB text field.")
    return value


def _month(value: str) -> str:
    if re.fullmatch(r"\d{4}-\d{2}", value):
        normalized = value
    else:
        names = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
        matched = re.fullmatch(r"([A-Za-z]{3}) (\d{4})", value)
        if not matched or matched[1] not in names:
            raise StoreError("Invalid YNAB budget month.")
        normalized = f"{matched[2]}-{names.index(matched[1]) + 1:02d}"
    try:
        date.fromisoformat(f"{normalized}-01")
    except ValueError:
        raise StoreError("Invalid YNAB budget month.") from None
    return normalized


def _date(value: str) -> str:
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value).isoformat()
        if re.fullmatch(r"\d{2}/\d{2}/\d{4}", value):
            return datetime.strptime(value, "%m/%d/%Y").date().isoformat()
    except ValueError:
        pass
    raise StoreError("Invalid YNAB transaction date. Use ISO dates or MM/DD/YYYY.")


def _table(data: bytes) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    text = data.decode(encoding)
    if "\x00" in text:
        raise StoreError("Invalid YNAB table encoding.")
    first_line = text.partition("\n")[0]
    delimiter = "\t" if "\t" in first_line else ","
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
    headers = tuple(reader.fieldnames or ())
    if headers not in (BUDGET_HEADERS, REGISTER_HEADERS):
        raise StoreError("ZIP must contain one YNAB Budget table and one YNAB Register table.")
    result = []
    for row in reader:
        if len(result) >= MAX_ROWS:
            raise StoreError("YNAB table contains too many rows.")
        if None in row or None in row.values():
            raise StoreError("YNAB table has an invalid row shape.")
        result.append(row)
    return headers, result


def _tables(data: bytes) -> dict[tuple[str, ...], list[dict[str, str]]]:
    if not isinstance(data, bytes) or not data or len(data) > MAX_ARCHIVE_BYTES:
        raise StoreError("YNAB ZIP is empty or exceeds the upload limit.")
    result = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = archive.infolist()
        if len(members) > MAX_MEMBERS or sum(m.file_size for m in members) > MAX_UNCOMPRESSED_BYTES:
            raise StoreError("YNAB ZIP exceeds the archive limits.")
        for member in members:
            path = PurePosixPath(member.filename.replace("\\", "/"))
            if path.is_absolute() or ".." in path.parts or stat.S_ISLNK(member.external_attr >> 16):
                raise StoreError("YNAB ZIP contains an unsafe member.")
            if member.flag_bits & 1 or member.file_size > MAX_MEMBER_BYTES:
                raise StoreError("YNAB ZIP contains an encrypted or oversized member.")
            if member.is_dir() or path.suffix.lower() not in {".csv", ".tsv"}:
                continue
            with archive.open(member) as stream:
                contents = stream.read(MAX_MEMBER_BYTES + 1)
            if len(contents) > MAX_MEMBER_BYTES:
                raise StoreError("YNAB ZIP contains an oversized member.")
            headers, rows = _table(contents)
            if headers in result:
                raise StoreError("YNAB ZIP contains duplicate Budget or Register tables.")
            result[headers] = rows
    if len(result) != 2:
        raise StoreError("ZIP must contain one YNAB Budget table and one YNAB Register table.")
    return result


def _category(row: dict[str, str], categories: dict[str, YnabCategory]) -> str | None:
    group = _label(row["Category Group"])
    name = _label(row["Category"])
    combined = _label(row["Category Group/Category"], limit=500)
    if combined in {"Inflow: Ready to Assign", "Inflow: To be Budgeted", "Split", "-- Split --"}:
        return None
    if not name:
        if group or combined:
            raise StoreError("YNAB category fields are inconsistent.")
        return None
    if not group or combined != f"{group}: {name}":
        raise StoreError("YNAB category fields are inconsistent.")
    identifier = _identifier("category", group, name)
    categories[identifier] = YnabCategory(identifier, name, group)
    return identifier


def parse_dump(data: bytes) -> YnabDump:
    """Parse an export without filesystem writes or access to any live ledger."""
    try:
        tables = _tables(data)
        categories: dict[str, YnabCategory] = {}
        assignments = []
        assignment_keys = set()
        for row in tables[BUDGET_HEADERS]:
            category_id = _category(row, categories)
            if category_id is None:
                raise StoreError("YNAB Budget rows must identify an expense category.")
            month = _month(row["Month"])
            key = (month, category_id)
            if key in assignment_keys:
                raise StoreError("YNAB Budget contains duplicate month/category assignments.")
            assignment_keys.add(key)
            assignments.append(YnabAssignment(
                _identifier("assignment", *key), month, category_id, _amount(row["Assigned"]),
                _amount(row["Activity"]), _amount(row["Available"]),
            ))
        balances: Counter[str] = Counter()
        account_ids = {}
        transactions = []
        duplicates: Counter[tuple[str, ...]] = Counter()
        for row in tables[REGISTER_HEADERS]:
            account = _label(row["Account"], required=True)
            account_id = _identifier("account", account)
            account_ids[account] = account_id
            amount = _amount(row["Outflow"]) - _amount(row["Inflow"])
            if abs(amount) > MAX_CENTS:
                raise StoreError("YNAB transaction exceeds the supported range.")
            balances[account] -= amount
            if abs(balances[account]) > MAX_CENTS:
                raise StoreError("YNAB account balance exceeds the supported range.")
            category_id = _category(row, categories)
            payee = _label(row["Payee"], limit=1000)
            memo = _label(row["Memo"], limit=10_000)
            flag = _label(row["Flag"])
            cleared = row["Cleared"].lower()
            if cleared not in {"uncleared", "cleared", "reconciled"}:
                raise StoreError("Invalid YNAB cleared status.")
            is_split = row["Category Group/Category"] in {"Split", "-- Split --"}
            is_transfer = not row["Category Group/Category"] and payee.startswith("Transfer : ")
            target = payee.removeprefix("Transfer : ") if is_transfer else ""
            values = tuple(row[header] for header in REGISTER_HEADERS)
            duplicates[values] += 1
            transactions.append(YnabTransaction(
                _identifier("transaction", *values, str(duplicates[values])), account_id,
                _date(row["Date"]), amount, payee, memo, category_id, cleared, flag, is_transfer,
                _identifier("account", target) if target else None, is_split,
            ))
        accounts = tuple(
            YnabAccount(account_ids[name], name, balances[name]) for name in sorted(account_ids)
        )
        return YnabDump(
            accounts, tuple(categories.values()), tuple(transactions), tuple(assignments), WARNINGS,
        )
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, NotImplementedError, OSError,
            EOFError, UnicodeError, csv.Error, OverflowError):
        raise StoreError("Invalid or unsupported YNAB ZIP export.") from None
