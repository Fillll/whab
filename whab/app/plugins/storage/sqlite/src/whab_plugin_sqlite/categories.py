"""Read additive category metadata without changing legacy record digests."""


def category_record(row):
    row = dict(row)
    return {"id": row["id"], "name": row["name"], "group": row["group_name"],
            "target": row["target"], "currency": row["currency"],
            **({"systemKind": row["system_kind"]} if row.get("system_kind") is not None else {})}
