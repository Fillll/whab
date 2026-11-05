"""Provider-declared periodic synchronization through existing host admission."""

import asyncio
import json
import math
from pathlib import Path
from time import time

from whab.budgets.document import atomic_write
from whab.plugins import BankError
from whab.plugins.bank_adapter import BankAdapter


class BankJobs:
    def __init__(self, home, environment, runtime, banks, *, clock=time):
        self.path = Path(home) / "jobs" / f"bank-sync-{environment}.json"
        self.runtime, self.banks, self.clock = runtime, banks, clock
        self.lock, self.task = asyncio.Lock(), None
        try:
            if self.path.stat().st_size > 64 * 1024:
                raise ValueError
            self.records = json.loads(self.path.read_bytes())
            if type(self.records) is not dict or any(type(row) is not dict
                    or set(row) != {"last_attempt", "last_success", "next_due", "error"}
                    or any(type(row[key]) not in (int, float) or not math.isfinite(row[key]) or row[key] < 0
                           for key in ("last_attempt", "last_success", "next_due"))
                    or row["error"] not in (None, "sync_failed") for row in self.records.values()):
                raise ValueError
        except FileNotFoundError:
            self.records = {}
        except (OSError, ValueError, TypeError):
            raise BankError("Saved synchronization status is invalid. Preserve the job file and inspect it before continuing.") from None

    def policies(self):
        return {plugin.id: plugin.bank.sync_interval_seconds
                for plugin in self.runtime._active.services["plugins"].bank_plugins()
                if type(plugin.bank) is BankAdapter and plugin.bank.sync_interval_seconds is not None}

    def status(self):
        return {"jobs": [{"plugin": plugin, "interval_seconds": interval,
                          **self.records.get(plugin, {"last_attempt": 0, "last_success": 0, "next_due": 0, "error": None})}
                         for plugin, interval in self.policies().items()]}

    async def run(self, plugin, *, due=False):
        async with self.lock:
            async with self.runtime.background():
                bank = self.banks.get(plugin)
                if bank is None:
                    raise BankError("The selected bank plugin is not enabled.")
                interval = self.policies().get(plugin, 3600)
                now = self.clock()
                record = self.records.get(plugin, {"last_attempt": 0, "last_success": 0, "next_due": 0, "error": None})
                if due and now < record["next_due"]:
                    return {"ok": True, "skipped": True}
                # Publish the attempt before external work. A crash cannot create
                # an immediate retry storm or change native uncertainty fencing.
                record = {**record, "last_attempt": now, "next_due": now + interval}
                self.records[plugin] = record
                atomic_write(self.path, json.dumps(self.records, allow_nan=False).encode())
                try:
                    result = await bank.sync()
                    fields = ("added", "modified", "removed")
                    if type(result) is not dict or any(type(result.get(key)) is not int or result[key] < 0 for key in fields):
                        raise BankError("The bank returned an invalid synchronization result.")
                except Exception:
                    record["error"] = "sync_failed"
                    atomic_write(self.path, json.dumps(self.records, allow_nan=False).encode())
                    raise BankError("Bank synchronization failed. Inspect connection and recovery status before retrying.") from None
                record.update(last_success=self.clock(), error=None)
                atomic_write(self.path, json.dumps(self.records, allow_nan=False).encode())
                return {"ok": True, **{key: result[key] for key in fields}}

    async def run_due(self):
        results = {}
        for plugin in self.policies():
            try:
                results[plugin] = await self.run(plugin, due=True)
            except Exception:
                results[plugin] = {"ok": False, "error": "sync_failed"}
        return results

    async def start(self):
        async def run():
            while True:
                await self.run_due()
                await asyncio.sleep(60)
        self.task = asyncio.create_task(run())

    async def close(self):
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
