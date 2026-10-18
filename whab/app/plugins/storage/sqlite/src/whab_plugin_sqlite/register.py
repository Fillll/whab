"""Bounded public register snapshots with complete-selection counts."""


def transaction_register_page(
    store, workspace, start_date, end_date, currency, account_ids, search, review, page, page_size,
):
    """Filter before paging, without materializing another page's financial records."""
    with store._snapshot():
        store._require_workspace(workspace)
        accounts = store.accounts(workspace)
        categories = store.categories(workspace)
        conditions = ["t.workspace=?", "t.deleted=0", "a.currency=?"]
        arguments = [workspace, currency]
        if start_date is not None:
            conditions.append("t.date>=?")
            arguments.append(start_date)
        if end_date is not None:
            conditions.append("t.date<=?")
            arguments.append(end_date)
        if account_ids is not None:
            conditions.append("t.account_id IN (" + ",".join("?" for _ in account_ids) + ")")
            arguments.extend(account_ids)

        matches = []
        match_arguments = []
        query = search.strip().casefold()
        if query:
            # SQLite lower/LIKE do not preserve Python's Unicode casefold or
            # literal substring semantics (including percent and underscore).
            store.db.create_function("whab_casefold", 1, str.casefold, deterministic=True)
            matches.append("instr(whab_casefold(t.merchant || ' ' || t.note),?)>0")
            match_arguments.append(query)
        if review == "needs-review":
            matches.append("t.reviewed=0")
        elif review == "reviewed":
            matches.append("t.reviewed=1")

        scope = (
            " FROM transactions t JOIN accounts a "
            "ON a.workspace=t.workspace AND a.id=t.account_id WHERE "
            + " AND ".join(conditions)
        )
        matched_count = (
            "COALESCE(SUM(CASE WHEN " + " AND ".join(matches) + " THEN 1 ELSE 0 END),0)"
            if matches else "COUNT(*)"
        )
        count_scope = scope
        count_arguments = arguments
        if account_ids is None:
            # Resolve the currency's accounts once instead of joining each
            # transaction. Explicit account selections already make the JOIN
            # selective, so keep their existing query plan.
            count_scope = (
                " FROM transactions t WHERE " + " AND ".join([
                    "t.workspace=?", "t.deleted=0",
                    "t.account_id IN (SELECT id FROM accounts WHERE workspace=? AND currency=?)",
                    *conditions[3:],
                ])
            )
            count_arguments = [workspace, workspace, currency, *arguments[2:]]
        counts = store.db.execute(
            "SELECT COUNT(*) AS total,"
            "COALESCE(SUM(CASE WHEN t.reviewed=0 AND t.pending=0 THEN 1 ELSE 0 END),0) AS to_review,"
            + matched_count + " AS matched" + count_scope,
            [*match_arguments, *count_arguments],
        ).fetchone()
        total, count, to_review = counts["total"], counts["matched"], counts["to_review"]
        pages = max(1, (count + page_size - 1) // page_size)
        page = max(1, min(page, pages))
        offset = (page - 1) * page_size
        rows = store.db.execute(
            "SELECT t.id,t.account_id AS accountId,t.date,t.merchant,"
            "t.merchant_logo AS merchantLogo,t.amount,t.category_id AS categoryId,"
            "t.pending,t.reviewed,t.source,t.note,t.is_transfer AS isTransfer,"
            "t.inferred_transfer AS inferredTransfer,a.currency"
            + scope + (" AND " + " AND ".join(matches) if matches else "")
            + " ORDER BY t.date DESC,t.id DESC LIMIT ? OFFSET ?",
            [*arguments, *match_arguments, page_size, offset],
        )
        return {
            "accounts": accounts, "categories": categories,
            "transactions": [
                {**dict(row), **{key: bool(row[key]) for key in (
                    "pending", "reviewed", "isTransfer", "inferredTransfer",
                )}}
                for row in rows
            ],
            "transactionPage": {
                "total": total, "count": count, "page": page, "pages": pages,
                "start": offset + 1 if count else 0, "end": min(offset + page_size, count),
                "toReview": to_review,
            },
        }
