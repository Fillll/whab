"""Provider-declared periodic synchronization through existing host admission."""

import asyncio
import json
import math
from datetime import datetime
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

        self.policy_path = Path(home) / "plugin-workers" / f"bank-sync-policy-{environment}.json"
        try:
            if self.policy_path.stat().st_size > 16 * 1024:
                raise ValueError
            self.overrides = json.loads(self.policy_path.read_bytes())
            if type(self.overrides) is not dict or len(self.overrides) > 100:
                raise ValueError
            from whab.plugins.sdk import valid_plugin_name
            for plugin, interval in self.overrides.items():
                if not valid_plugin_name(plugin) or interval not in (None, 3600, 10800, 21600) or type(interval) not in (int, type(None)):
                    raise ValueError
        except FileNotFoundError:
            self.overrides = {}
        except (OSError, ValueError, TypeError):
            raise BankError("Saved bank update policy is invalid. Preserve it before repairing.") from None

    def policies(self):
        return {plugin.id: self.overrides.get(plugin.id, plugin.bank.sync_interval_seconds)
                for plugin in self.runtime._active.services["plugins"].bank_plugins()
                if type(plugin.bank) is BankAdapter and plugin.bank.sync_interval_seconds is not None}

    def status(self):
        return {"jobs": [{"plugin": plugin, "interval_seconds": interval,
                          **self.record(plugin, interval)}
                         for plugin, interval in self.policies().items()]}

    def automatic(self, plugin):
        policies = self.policies()
        return plugin not in policies or policies[plugin] is not None

    async def configure(self, plugin, interval):
        if plugin not in self.policies():
            raise BankError("The selected bank plugin has no update schedule.")
        if type(interval) not in (int, type(None)) or interval not in (None, 3600, 10800, 21600):
            raise BankError("Choose manual updates or an interval of 1, 3 or 6 hours.")
        async with self.lock:
            candidate = {**self.overrides, plugin: interval}
            atomic_write(self.policy_path, json.dumps(candidate).encode())
            self.overrides = candidate
            record = self.records.get(plugin)
            if record is not None:
                record['next_due'] = self.next_boundary(self.clock(), interval) if interval else 0
                atomic_write(self.path, json.dumps(self.records, allow_nan=False).encode())
        return self.status()

    @staticmethod
    def next_boundary(at, interval):
        # launchd's hourly calendar tick and the in-host timer share the same
        # local clock phase, including zones with fractional-hour UTC offsets.
        offset = datetime.fromtimestamp(at).astimezone().utcoffset().total_seconds()
        return ((at + offset) // interval + 1) * interval - offset

    def record(self, plugin, interval):
        record = self.records.get(plugin, {"last_attempt": 0, "last_success": 0, "next_due": 0, "error": None})
        # Existing 2.56.0 records used attempt+interval. Bring their effective due
        # time forward without changing their last attempt/success or native data.
        if interval is None:
            return {**record, "next_due": 0}
        if plugin in self.overrides:
            return {**record, "next_due": self.next_boundary(record["last_attempt"], interval) if record["last_attempt"] else 0}
        return {**record, "next_due": min(record["next_due"], self.next_boundary(record["last_attempt"], interval))}

    async def run(self, plugin, *, due=False):
        async with self.lock:
            async with self.runtime.background():
                bank = self.banks.get(plugin)
                if bank is None:
                    raise BankError("The selected bank plugin is not enabled.")
                interval = self.policies().get(plugin, 3600)
                if due and interval is None:
                    return {"ok": True, "skipped": True}
                now = self.clock()
                record = self.record(plugin, interval)
                if due and now < record["next_due"]:
                    return {"ok": True, "skipped": True}
                # Publish the attempt before external work. A crash cannot create
                # an immediate retry storm or change native uncertainty fencing.
                record = {**record, "last_attempt": now, "next_due": self.next_boundary(now, interval) if interval else 0}
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
