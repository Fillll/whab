"""SQLite plugin's deterministic sample ledger and household category template."""

from decimal import Decimal

from whab.domain.dates import shift_month, today

CATEGORY_TEMPLATE = [
    ("Rent", "Home", 260000),
    ("Groceries", "Everyday", 85000),
    ("Dining", "Everyday", 35000),
    ("Transport", "Everyday", 25000),
    ("Utilities", "Home", 30000),
    ("Health", "Life", 20000),
    ("Fun", "Life", 20000),
    ("Travel", "Goals", 50000),
    ("Shopping", "Life", 25000),
]


def build_initial_state(workspace: str, month: str, *, as_of_date=None) -> dict:
    selected_date = as_of_date or today()
    state = {
        "_id": workspace,
        "revision": 0,
        "schemaVersion": 1,
        "budgetStartMonth": shift_month(month, -5) if workspace == "demo" else month,
        "accounts": {},
        "categories": {},
        "transactions": {},
        "budget": {},
        "items": {},
    }
    state["categories"] = {
        name.lower(): {
            "id": name.lower(),
            "name": name,
            "group": group,
            "target": target,
            "currency": "USD",
        }
        for name, group, target in CATEGORY_TEMPLATE
    }
    if workspace != "demo":
        return state
    for account_id, name, kind, balance, mask in [
        ("checking", "Everyday checking", "depository", 700000, "4821"),
        ("savings", "Family savings", "depository", 240000, "9012"),
        ("credit", "Rewards card", "credit", 0, "3389"),
    ]:
        state["accounts"][account_id] = {
            "id": account_id,
            "name": name,
            "institution": "Demo Bank",
            "type": kind,
            "balance": balance,
            "currency": "USD",
            "source": "demo",
            "mask": mask,
            "stale": False,
        }
    merchants = [
        ("rent", "Monthly rent", 260000),
        ("groceries", "Whole Foods Market", 62000),
        ("dining", "The neighborhood café", 23500),
        ("transport", "BART & gas", 14500),
        ("utilities", "Power and internet", 27500),
        ("health", "Family pharmacy", 13000),
        ("fun", "Movie night", 8200),
        ("shopping", "Family essentials", 18000),
    ]
    for offset in range(-5, 1):
        seed_month = shift_month(month, offset)
        state["budget"][seed_month] = {
            name.lower(): target for name, _, target in CATEGORY_TEMPLATE
        }
        rows = [(f"salary-{seed_month}", "Family paychecks", -820000, None, 1)]
        rows += [
            (
                f"{category}-{seed_month}",
                merchant,
                amount
                if offset == 0
                else int(Decimal(amount) * (Decimal(1) + Decimal(offset) * Decimal(".025"))),
                category,
                min(index + 2, int(selected_date[8:]) if offset == 0 else 20),
            )
            for index, (category, merchant, amount) in enumerate(merchants)
        ]
        for transaction_id, merchant, amount, category, day in rows:
            state["transactions"][transaction_id] = {
                "id": transaction_id,
                "accountId": "checking",
                "date": f"{seed_month}-{day:02d}",
                "merchant": merchant,
                "merchantLogo": None,
                "amount": amount,
                "categoryId": category,
                "pending": False,
                "reviewed": True,
                "source": "demo",
                "note": "",
                "isTransfer": False,
                "inferredTransfer": False,
                "categoryEdited": False,
                "deleted": False,
            }
            state["accounts"]["checking"]["balance"] -= amount
    state["transactions"]["pending-demo"] = {
        "id": "pending-demo",
        "accountId": "credit",
        "date": selected_date,
        "merchant": "Weekend groceries",
        "merchantLogo": None,
        "amount": 6425,
        "categoryId": "groceries",
        "pending": True,
        "reviewed": False,
        "source": "demo",
        "note": "",
        "isTransfer": False,
        "inferredTransfer": False,
        "categoryEdited": False,
        "deleted": False,
    }
    return state
