"""Full-file persistence benchmark using fictional public imports only."""

import argparse
import json
import statistics
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from whab_plugin_json import Store

from whab.plugins import WorkspaceImport


def fictional_import(count, month):
    return WorkspaceImport(
        accounts=({"id": "fictional-checking", "name": "Fictional checking", "institution": "Fictional Bank",
                   "type": "depository", "balance": 100000000, "source": "fictional-import",
                   "stale": False, "balanceUnknown": False},),
        categories=({"id": "fictional-category", "name": "Fictional essentials", "group": "Fictional", "target": 0},),
        transactions=tuple({
            "id": f"fictional-{index}", "accountId": "fictional-checking", "date": f"{month}-{index % 28 + 1:02d}",
            "merchant": f"Fictional merchant {index % 100}", "amount": 125 + index % 5000,
            "categoryId": "fictional-category", "pending": False, "reviewed": True,
            "source": "fictional-import", "note": "A fictional record for storage measurements.",
            "isTransfer": False, "inferredTransfer": False, "categoryEdited": True, "deleted": False,
        } for index in range(count)),
        assignments=({"month": month, "categoryId": "fictional-category", "assigned": 0},),
        budget_start_month=month,
    )


def measure(count, repeats):
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    data = fictional_import(count, month)
    with tempfile.TemporaryDirectory(prefix="whab-json-benchmark-") as home:
        path = Path(home) / "budget.json"
        store = Store(path, month=month)
        try:
            before = store.commit_evidence()["generation"]
            started = perf_counter()
            store.import_workspace("personal", "fictional-benchmark", f"records-{count}", data)
            imported = perf_counter() - started
            commits = store.commit_evidence()["generation"] - before
            elapsed = []
            for index in range(repeats):
                started = perf_counter()
                store.edit_transaction("personal", "fictional-0", note=f"Fictional edit {index}")
                elapsed.append(perf_counter() - started)
            size = path.stat().st_size
        finally:
            store.close()
        started = perf_counter()
        reopened = Store(path)
        reopened.close()
        reopen = perf_counter() - started
    return {
        "transactions": count, "bytes": size, "batch_commits": commits,
        "batch_ms": round(imported * 1000, 2), "edit_median_ms": round(statistics.median(elapsed) * 1000, 2),
        "edit_max_ms": round(max(elapsed) * 1000, 2), "reopen_ms": round(reopen * 1000, 2),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--counts", nargs="+", type=int, default=[100, 1000, 10000])
    parser.add_argument("--repeats", type=int, default=5)
    arguments = parser.parse_args()
    if arguments.repeats < 1 or any(not 1 <= count <= 250000 for count in arguments.counts):
        parser.error("Use positive repeats and 1–250000 fictional records.")
    print(json.dumps([measure(count, arguments.repeats) for count in arguments.counts], indent=2))


if __name__ == "__main__":
    main()
