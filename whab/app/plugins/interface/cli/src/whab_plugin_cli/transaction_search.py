"""Complete read-only transaction searches over public ledger queries."""

import argparse
import re
from datetime import date

from whab.plugins import format_money
from whab.plugins.documentation import operation

from .transaction_commands import checked
from .transaction_reads import terminal_text


def calendar_date(value):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise argparse.ArgumentTypeError("Use a real calendar date in YYYY-MM-DD form.")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as failure:
        raise argparse.ArgumentTypeError("Use a real calendar date in YYYY-MM-DD form.") from failure


def merchant_text(value):
    if not value.strip():
        raise argparse.ArgumentTypeError("Merchant text must not be empty or only whitespace.")
    return value


def add_search_parser(commands, catalog):
    document = next(row["documentation"] for row in catalog["plugins"] if row["id"] == "cli")
    guide = operation(document, "local", "transactions.list")["summary"]
    listing = commands.add_parser(
        "list", help="Search all nondeleted transactions, including reviewed and categorized history",
        description="Read complete transaction matches without changing categories or review status.",
        epilog=guide, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    listing.add_argument("--since", type=calendar_date, help="Inclusive earliest date, YYYY-MM-DD; default: no lower bound")
    listing.add_argument("--until", type=calendar_date, help="Inclusive latest date, YYYY-MM-DD; default: no upper bound")
    listing.add_argument("--month", help="Restrict the public history read to YYYY-MM; combines with date bounds")
    listing.add_argument("--merchant", action="append", type=merchant_text, default=[],
                         help="Case-insensitive literal merchant substring; repeat to match ANY supplied text. No fuzzy matches, regex, aliases or lender inference")
    listing.add_argument("--format", choices=("json", "table"), default="json", help="Output format (default: json); JSON retains all public fields")


def validate_search(args, error):
    if args.since and args.until and args.since > args.until:
        raise error("--since must be on or before --until (both bounds are inclusive).")


def execute_search(connection, identifier, prefix, args, exchange, error):
    params = {"workspace": "personal", **({"month": args.month} if args.month else {})}
    transactions = checked(exchange(connection, "GET", prefix + "/queries/transactions", params=params), identifier, error)
    metadata = {}
    for name in ("accounts", "categories"):
        result = checked(exchange(connection, "GET", prefix + "/queries/" + name, params={"workspace": "personal"}), identifier, error)
        if result["revision"] != transactions["revision"]:
            raise error("WHAB changed while reading transaction/account/category names. Run the read again; no partial results were returned.")
        metadata[name] = {row["id"]: row for row in result["data"]}
    needles = [text.casefold() for text in args.merchant]
    rows = []
    for row in transactions["data"]:
        if (row.get("deleted") or args.since and row["date"] < args.since
                or args.until and row["date"] > args.until
                or needles and not any(text in row["merchant"].casefold() for text in needles)):
            continue
        account = metadata["accounts"].get(row["accountId"], {})
        category = metadata["categories"].get(row.get("categoryId"), {})
        rows.append({**row, "accountName": account.get("name", row["accountId"]),
                     "categoryName": category.get("name"), "categoryGroup": category.get("group")})
    rows.sort(key=lambda row: (row["date"], row["id"]), reverse=True)
    return {"budgetId": identifier, "revision": transactions["revision"], "complete": True,
            "total": len(rows), "returned": len(rows),
            "filters": {"since": args.since, "until": args.until, "month": args.month,
                        "merchant": args.merchant, "merchantMatch": "case-insensitive-literal-substring-any"},
            "data": rows}


def search_table(result):
    lines = [f"Budget: {terminal_text(result['budgetId'], 200)}",
             f"Transactions: {result['total']} matched, {result['returned']} shown (complete)"]
    if not result["data"]:
        return "\n".join([*lines, "No matching transactions."])
    rows = [["Transaction ID", "Date", "Merchant", "Account", "Amount", "Currency", "Category", "Status", "Review"]]
    for row in result["data"]:
        category = (f"{row['categoryGroup']} / {row['categoryName']}" if row["categoryName"] is not None
                    else row["categoryId"] if row["categoryId"] is not None else "Uncategorized")
        rows.append([row["id"], row["date"], row["merchant"], row["accountName"],
                     format_money(row["amount"], row["currency"]), row["currency"], category,
                     "Pending" if row["pending"] else "Posted", "Reviewed" if row["reviewed"] else "Unreviewed"])
    # IDs are never shortened. JSON retains complete names, notes and annotations.
    rows = [[terminal_text(value, limit)
             for value, limit in zip(row, (None, 10, 36, 28, 22, 8, 36, 7, 10), strict=True)]
            for row in rows]
    widths = [max(len(row[column]) for row in rows) for column in range(9)]
    lines.append("Positive amounts are outflows; negative amounts are inflows/refunds. Names may be shortened; JSON retains full fields. No currencies are combined.")
    lines += ["  ".join(value.rjust(width) if column == 4 else value.ljust(width)
                        for column, (value, width) in enumerate(zip(row, widths, strict=True))) for row in rows]
    return "\n".join(lines)
