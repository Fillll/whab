"""Deterministic presentation ordering over complete public register selections."""

from whab.domain.errors import StoreError

FIELDS = {"date", "account", "merchant", "category", "note", "amount", "currency", "reviewed"}


def normalize_sort(values):
    if type(values) not in (list, tuple) or len(values) > len(FIELDS):
        raise StoreError("Choose at most eight distinct transaction sort columns.")
    result = []
    for value in values:
        if type(value) is not str:
            raise StoreError("Transaction sorts must use column:asc or column:desc.")
        field, separator, direction = value.partition(":")
        if not separator or field not in FIELDS or direction not in {"asc", "desc"}:
            raise StoreError("Invalid transaction sort; use a supported column:asc or column:desc.")
        if field in {item[0] for item in result}:
            raise StoreError("A transaction sort column may appear only once.")
        result.append((field, direction))
    return result


def sort_transactions(rows, accounts, categories, ordering):
    result = sorted(rows, key=lambda row: (row["date"], row["id"]), reverse=True)
    if not ordering:
        return result
    account_names = {row["id"]: row["name"].casefold() for row in accounts}
    category_names = {row["id"]: f"{row.get('group', '')} > {row['name']}".casefold() for row in categories}
    currencies = {row["id"]: row.get("currency", "USD") for row in accounts}

    def value(row, field):
        if field == "account":
            return account_names.get(row["accountId"], "disconnected account")
        if field == "category":
            return category_names.get(row["categoryId"], "uncategorized")
        if field == "amount":
            return -row["amount"]  # Sort the signed amount shown in the register.
        if field == "currency":
            return currencies[row["accountId"]]
        result = row[field]
        return result.casefold() if type(result) is str else result

    for field, direction in reversed(ordering):
        result.sort(key=lambda row: value(row, field), reverse=direction == "desc")
    return result
