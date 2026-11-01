"""Public, transport-independent feature operations supplied by enabled plugins."""

import re
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True, slots=True)
class FeatureOperations:
    """Callbacks receive (workspace, action, parameters); queries are read-only.

    Registered by a restart-scoped interface factory on its budget's service.
    Commands execute under the public ledger revision/retry boundary. Only public
    feature data belongs here; credentials and provider configuration stay local.
    """

    queries: tuple[str, ...] = ()
    commands: tuple[str, ...] = ()
    query: Callable | None = None
    command: Callable | None = None
    adapter: object = None

    def __post_init__(self):
        for names, callback in ((self.queries, self.query), (self.commands, self.command)):
            if (type(names) is not tuple or len(names) > 32
                    or any(type(name) is not str or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name) is None for name in names)
                    or len(set(names)) != len(names) or bool(names) != callable(callback)):
                raise ValueError("Feature operations require bounded unique names and matching callbacks.")
        if not self.queries and not self.commands:
            raise ValueError("A feature must expose at least one operation.")
