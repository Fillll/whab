"""Due-job durability, catch-up and serialization use fictional provider calls."""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from whab.plugins import BankAdapter, BankError, Plugin
from whab.services.bank_jobs import BankJobs


def runtime():
    plugin = Plugin("fictional", "Fictional bank", bank=BankAdapter(lambda _: None, 3600))
    class Runtime:
        _active = SimpleNamespace(services={"plugins": SimpleNamespace(bank_plugins=lambda: (plugin,))})
        @asynccontextmanager
        async def background(self):
            yield
    return Runtime()


def test_hourly_jobs_catch_up_once_serialize_and_reopen(tmp_path):
    async def run():
        now, calls = [10000], []
        async def sync():
            calls.append(now[0])
            await asyncio.sleep(0)
            return {"added": 1, "modified": 2, "removed": 0}
        banks = {"fictional": SimpleNamespace(sync=sync)}
        jobs = BankJobs(tmp_path, "sandbox", runtime(), banks, clock=lambda: now[0])
        first, second = await asyncio.gather(jobs.run("fictional", due=True), jobs.run("fictional", due=True))
        assert first["added"] == 1 and second["skipped"] and calls == [10000]
        reopened = BankJobs(tmp_path, "sandbox", runtime(), banks, clock=lambda: now[0])
        assert (await reopened.run_due())["fictional"]["skipped"]
        now[0] += 12 * 3600
        assert (await reopened.run_due())["fictional"]["ok"]
        assert len(calls) == 2
        assert reopened.status()["jobs"][0]["last_success"] == now[0]
        assert jobs.path.stat().st_mode & 0o077 == 0
    asyncio.run(run())


def test_failed_provider_records_only_safe_status_without_replaying(tmp_path):
    async def run():
        calls = []
        async def sync():
            calls.append(1)
            raise RuntimeError("fictional-private-provider-payload")
        jobs = BankJobs(tmp_path, "sandbox", runtime(), {"fictional": SimpleNamespace(sync=sync)}, clock=lambda: 10000)
        assert (await jobs.run_due())["fictional"] == {"ok": False, "error": "sync_failed"}
        assert (await jobs.run_due())["fictional"]["skipped"]
        assert calls == [1]
        assert "fictional-private-provider-payload" not in jobs.path.read_text()
        assert jobs.status()["jobs"][0]["last_success"] == 0
        with pytest.raises(BankError):
            await jobs.run("missing")
        await jobs.start()
        await jobs.close()
    asyncio.run(run())


def test_invalid_saved_status_fails_closed(tmp_path):
    path = tmp_path / "jobs" / "bank-sync-sandbox.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"fictional": {"token": "fictional-private"}}))
    with pytest.raises(BankError, match="Preserve"):
        BankJobs(tmp_path, "sandbox", runtime(), {})


def test_bank_schedule_policy_is_bounded():
    for interval in (False, 0, 59, 3600.0, 31 * 86400 + 1):
        with pytest.raises(ValueError):
            BankAdapter(lambda _: None, interval)
    assert BankAdapter(lambda _: None).sync_interval_seconds is None
