"""Controlled handovers preserve coherent operations and attributable recovery."""

import asyncio
from threading import Event
from types import SimpleNamespace

import pytest

from whab.budgets.runtime import (
    BudgetRuntime,
    RuntimeCandidate,
    RuntimeResource,
    RuntimeTransitionError,
)
from whab.services.ledger import LedgerService


def service(value):
    return SimpleNamespace(read=lambda: value)


def test_host_shutdown_retires_both_uncertain_generations_without_deciding_authority():
    async def scenario():
        closed = []
        active = RuntimeResource("first", lambda: closed.append("active"))
        prepared = RuntimeResource("second", lambda: closed.append("candidate"))
        runtime = BudgetRuntime("fictional-budget", {"report": service("old")}, (active,))

        def candidate(preparation):
            preparation.own(prepared)
            return RuntimeCandidate({"report": service("new")}, (prepared,))

        def uncertain(*_):
            raise OSError("synthetic lost activation response")

        with pytest.raises(RuntimeTransitionError, match="handover"):
            await runtime.transition(candidate, commit=uncertain)
        assert not closed and runtime.status()["health"] == "recovery_required"
        await runtime.shutdown()
        await runtime.shutdown()  # Shutdown is idempotent, never a new decision.
        assert closed == ["candidate", "active"]
        assert runtime.status()["admission"] == "closed"
        assert runtime.status()["health"] == "recovery_required"
        assert runtime.status()["generation"] == 1
        with pytest.raises(RuntimeTransitionError):
            async with runtime.request():
                pass
    asyncio.run(scenario())


def test_operations_keep_one_generation_and_other_budgets_remain_available():
    async def scenario():
        retired = []
        first = RuntimeResource("report", lambda: retired.append("first"))
        runtime = BudgetRuntime("first-budget", {"report": service("first")}, (first,))
        other = BudgetRuntime("other-budget", {"report": service("other")})
        handle = runtime.handle("report")
        entered, finish, waiting = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def operation():
            async with runtime.request() as lease:
                assert lease.generation.number == 1
                assert handle.call("read") == "first"
                entered.set()
                await finish.wait()
                assert await handle.acall("read") == "first"
                with runtime.lease() as nested:
                    assert nested is lease
                assert retired == []

        async def next_operation():
            waiting.set()
            return await handle.acall("read")

        task = asyncio.create_task(operation())
        await entered.wait()
        transition = asyncio.create_task(runtime.transition(
            lambda prep: RuntimeCandidate({"report": service("second")}), plugin_id="report",
        ))
        status = await runtime.wait_for_phase("draining")
        assert status["pending"]["from_generation"] == 1
        assert status["pending"]["to_generation"] == 2
        assert status["active_leases"] == 1
        assert status["admission"] == "gated"
        queued = asyncio.create_task(next_operation())
        await waiting.wait()
        assert not queued.done()
        assert await other.handle("report").acall("read") == "other"
        finish.set()
        await task
        assert await transition == 2
        assert await queued == "second"
        assert retired == ["first"]
        assert runtime.status()["active_leases"] == 0

    asyncio.run(scenario())


def test_stable_ledger_keeps_revision_lock_and_exact_retry_receipts(store):
    async def scenario():
        ledger = LedgerService(store, budget_id="fictional")
        lock = ledger._lock
        runtime = BudgetRuntime("fictional", {"ledger": ledger, "report": service("first")})
        handle = runtime.handle("ledger")
        original = ledger.revision
        arguments = ("add_account", "personal", {"name": "Cash", "type": "depository", "balance": 0})
        response = handle.call("command", *arguments, request_id="one", expected_revision=original)
        revision = ledger.revision
        await runtime.transition(lambda prep: RuntimeCandidate({**prep.active.services, "report": service("second")}))
        assert ledger._lock is lock
        assert ledger.revision == revision
        assert handle.call("command", *arguments, request_id="one", expected_revision=original) == response
        assert len(handle.call("accounts", "personal")) == 1

    asyncio.run(scenario())


def test_independent_background_work_holds_its_own_lease():
    async def scenario():
        runtime = BudgetRuntime("budget", {"report": service("first")})
        entered, finish = asyncio.Event(), asyncio.Event()

        async def background():
            async with runtime.background() as lease:
                entered.set()
                await finish.wait()
                assert lease.service("report").read() == "first"

        async with runtime.request():
            task = asyncio.create_task(background())
            await entered.wait()
            assert runtime.status()["active_leases"] == 2
        assert runtime.status()["active_leases"] == 1
        transition = asyncio.create_task(runtime.transition(lambda _: RuntimeCandidate({"report": service("second")})))
        await runtime.wait_for_phase("draining")
        finish.set()
        await task
        await transition
        assert runtime.handle("report").call("read") == "second"

    asyncio.run(scenario())


def test_copied_thread_context_uses_the_request_generation():
    async def scenario():
        runtime = BudgetRuntime("budget", {"report": service("first")})
        async with runtime.request() as outer:
            def read():
                with runtime.lease() as inner:
                    assert inner is outer
                    assert runtime.status()["active_leases"] == 1
                    return runtime.handle("report").call("read")
            assert await asyncio.to_thread(read) == "first"
        assert runtime.status()["active_leases"] == 0

    asyncio.run(scenario())


def test_synchronous_thread_work_drains_without_being_cancelled():
    async def scenario():
        runtime = BudgetRuntime("budget", {"report": service("first")})
        entered, finish = Event(), Event()

        def operation():
            with runtime.lease() as lease:
                entered.set()
                assert finish.wait(5)
                return lease.service("report").read()

        work = asyncio.create_task(asyncio.to_thread(operation))
        assert await asyncio.to_thread(entered.wait, 5)
        change = asyncio.create_task(runtime.transition(lambda _: RuntimeCandidate({"report": service("second")})))
        await runtime.wait_for_phase("draining")
        finish.set()
        assert await work == "first"
        assert await change == 2
        assert runtime.handle("report").call("read") == "second"

    asyncio.run(scenario())


def test_expired_inherited_scope_cannot_access_a_retired_service():
    async def scenario():
        runtime = BudgetRuntime("budget", {"report": service("first")})
        release = asyncio.Event()

        async def stale_child():
            await release.wait()
            with pytest.raises(RuntimeTransitionError) as caught:
                runtime.handle("report").call("read")
            assert caught.value.code == "lease_expired"
            async with runtime.background() as independent:
                assert independent.service("report").read() == "second"

        async with runtime.request() as lease:
            child = asyncio.create_task(stale_child())
        with pytest.raises(RuntimeTransitionError) as caught:
            lease.service("report")
        assert caught.value.code == "lease_expired"
        await runtime.transition(lambda _: RuntimeCandidate({"report": service("second")}))
        release.set()
        await child

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["prepare", "readiness"])
def test_failed_candidate_keeps_active_generation_and_cleans_staged_resources(stage):
    async def scenario():
        retired = []
        active = RuntimeResource("storage", lambda: retired.append("active"))
        runtime = BudgetRuntime("budget", {"report": service("first")}, (active,))

        def prepare(prep):
            prep.own(active)
            resource = prep.own(RuntimeResource("report", lambda: retired.append("first-staged")))
            assert prep.own(resource) is resource
            prep.own(RuntimeResource("report", lambda: retired.append("second-staged")))
            if stage == "prepare":
                raise ValueError("synthetic private detail")
            return prep.candidate({"report": service("second")})

        async def readiness(_):
            raise ValueError("synthetic private detail")

        with pytest.raises(RuntimeTransitionError) as caught:
            await runtime.transition(prepare, readiness=readiness, plugin_id="report")
        assert caught.value.code == f"{stage}_failed"
        assert caught.value.plugin_id == "report"
        assert "synthetic private detail" not in str(caught.value)
        status = runtime.status()
        assert status["generation"] == 1
        assert status["admission"] == "open"
        assert status["error"]["plugin_id"] == "report"
        assert "synthetic private detail" not in str(status)
        assert retired == ["second-staged", "first-staged"]
        assert runtime.handle("report").call("read") == "first"

    asyncio.run(scenario())


def test_invalid_candidate_and_negative_readiness_are_rejected_before_commit():
    async def scenario():
        runtime = BudgetRuntime("budget", {})
        commits = []
        with pytest.raises(RuntimeTransitionError) as caught:
            await runtime.transition(lambda _: object(), commit=lambda *_: commits.append(True))
        assert caught.value.code == "prepare_failed"
        with pytest.raises(RuntimeTransitionError) as caught:
            await runtime.transition(lambda _: RuntimeCandidate({}), readiness=lambda _: False,
                                     commit=lambda *_: commits.append(True))
        assert caught.value.code == "readiness_failed"
        assert commits == []
        assert runtime.status()["generation"] == 1

    asyncio.run(scenario())


def test_drain_timeout_resumes_existing_work_and_never_commits():
    async def scenario():
        runtime = BudgetRuntime("budget", {"report": service("first")})
        entered, finish = asyncio.Event(), asyncio.Event()
        retired, commits = [], []

        async def operation():
            async with runtime.request() as lease:
                entered.set()
                await finish.wait()
                assert lease.service("report").read() == "first"

        task = asyncio.create_task(operation())
        await entered.wait()

        def prepare(prep):
            prep.own(RuntimeResource("report", lambda: retired.append("candidate")))
            return prep.candidate({"report": service("second")})

        with pytest.raises(RuntimeTransitionError) as caught:
            await runtime.transition(prepare, drain_timeout=0, plugin_id="report",
                                     commit=lambda *_: commits.append(True))
        assert caught.value.code == "drain_timeout"
        assert not task.done()
        assert runtime.status()["active_leases"] == 1
        assert runtime.status()["admission"] == "open"
        assert commits == [] and retired == ["candidate"]
        finish.set()
        await task
        assert runtime.handle("report").call("read") == "first"

    asyncio.run(scenario())


def test_admission_deadlines_and_sync_event_loop_access_do_not_block_drain():
    async def scenario():
        runtime = BudgetRuntime("budget", {"report": service("first")})
        entered, finish = asyncio.Event(), asyncio.Event()

        async def operation():
            async with runtime.request():
                entered.set()
                await finish.wait()

        task = asyncio.create_task(operation())
        await entered.wait()
        change = asyncio.create_task(runtime.transition(lambda _: RuntimeCandidate({})))
        await runtime.wait_for_phase("draining")
        with pytest.raises(RuntimeTransitionError) as caught:
            async with runtime.request(timeout=0):
                pytest.fail("Gated admission must not succeed")
        assert caught.value.code == "admission_timeout"
        with pytest.raises(RuntimeTransitionError) as caught:
            runtime.handle("report").call("read")
        assert caught.value.code == "runtime_paused"

        def timed_out():
            with pytest.raises(RuntimeTransitionError) as caught:
                with runtime.lease(timeout=0):
                    pytest.fail("Gated admission must not succeed")
            assert caught.value.code == "admission_timeout"

        await asyncio.to_thread(timed_out)
        finish.set()
        await task
        await change

    asyncio.run(scenario())


def test_concurrent_transitions_are_serialized_before_preparation():
    async def scenario():
        runtime = BudgetRuntime("budget", {"report": service("first")})
        preparing, finish, second_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
        observed = []

        async def first(prep):
            observed.append(prep.active.number)
            preparing.set()
            await finish.wait()
            return RuntimeCandidate({"report": service("second")})

        async def second():
            second_started.set()
            return await runtime.transition(lambda prep: (
                observed.append(prep.active.number) or RuntimeCandidate({"report": service("third")})
            ))

        one = asyncio.create_task(runtime.transition(first))
        await preparing.wait()
        two = asyncio.create_task(second())
        await second_started.wait()
        assert observed == [1]
        finish.set()
        assert await one == 2
        assert await two == 3
        assert observed == [1, 2]
        assert runtime.handle("report").call("read") == "third"

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["preparing", "committing"])
def test_disconnected_transition_caller_does_not_cancel_handover(stage):
    async def scenario():
        runtime = BudgetRuntime("budget", {"report": service("first")})
        reached, finish = asyncio.Event(), asyncio.Event()

        async def prepare(_):
            if stage == "preparing":
                reached.set()
                await finish.wait()
            return RuntimeCandidate({"report": service("second")})

        async def commit(candidate, number):
            assert number == 2 and candidate.services["report"].read() == "second"
            if stage == "committing":
                reached.set()
                await finish.wait()

        task = asyncio.create_task(runtime.transition(prepare, commit=commit))
        await reached.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert runtime.status()["phase"] == stage
        assert runtime.status()["generation"] == 1
        finish.set()
        await runtime.wait_for_phase("active")
        assert runtime.status()["generation"] == 2
        assert runtime.handle("report").call("read") == "second"

    asyncio.run(scenario())


@pytest.mark.parametrize("authority", ["active", "candidate"])
def test_uncertain_commit_requires_validated_authority_and_preserves_both_candidates(authority):
    async def scenario():
        retired = []
        active = RuntimeResource("storage", lambda: retired.append("active"))
        candidate = RuntimeResource("storage", lambda: retired.append("candidate"))
        runtime = BudgetRuntime("budget", {"report": service("first")}, (active,))

        def commit(*_):
            raise OSError("synthetic credential")

        with pytest.raises(RuntimeTransitionError) as caught:
            await runtime.transition(lambda _: RuntimeCandidate({"report": service("second")}, (candidate,)),
                                     commit=commit, plugin_id="storage")
        assert caught.value.code == "commit_uncertain"
        assert runtime.status()["health"] == "recovery_required"
        assert runtime.status()["admission"] == "gated"
        assert retired == []
        assert "synthetic credential" not in str(runtime.status())
        with pytest.raises(RuntimeTransitionError) as caught:
            await runtime.transition(lambda _: RuntimeCandidate({}))
        assert caught.value.code == "recovery_required"
        with pytest.raises(RuntimeTransitionError) as caught:
            async with runtime.request(timeout=0):
                pytest.fail("Uncertain authority cannot admit operations")
        assert caught.value.code == "admission_timeout"
        with pytest.raises(RuntimeTransitionError) as caught:
            await runtime.recover(authority, lambda *_: False)
        assert caught.value.code == "recovery_failed"
        assert runtime.status()["admission"] == "gated"

        def validate(choice, old, new):
            assert choice == authority
            assert old.number == 1
            assert new.services["report"].read() == "second"
            return True

        assert await runtime.recover(authority, validate) == (1 if authority == "active" else 2)
        assert runtime.handle("report").call("read") == ("first" if authority == "active" else "second")
        assert retired == (["candidate"] if authority == "active" else ["active"])
        assert runtime.status()["admission"] == "open"
        assert runtime.status()["error"] is None

    asyncio.run(scenario())


def test_retirement_continues_in_reverse_order_after_failure_and_preserves_shared_resources():
    async def scenario():
        closed = []

        def broken():
            closed.append("broken")
            raise OSError("synthetic secret")

        shared = RuntimeResource("storage", lambda: closed.append("shared"))
        first = RuntimeResource("report", lambda: closed.append("first"))
        failing = RuntimeResource("branding", broken)
        runtime = BudgetRuntime("budget", {}, (shared, first, failing))
        assert await runtime.transition(lambda _: RuntimeCandidate({}, (shared,))) == 2
        assert closed == ["broken", "first"]
        status = runtime.status()
        assert status["phase"] == "active" and status["admission"] == "open"
        assert status["health"] == "degraded"
        assert status["cleanup_errors"][0]["plugin_id"] == "branding"
        assert status["cleanup_errors"][0]["code"] == "retirement_failed"
        assert "synthetic secret" not in str(status)
        await runtime.close()
        assert closed == ["broken", "first", "shared"]

    asyncio.run(scenario())


@pytest.mark.parametrize("fails", [False, True])
def test_slow_retirement_is_bounded_tracked_and_never_forcibly_cancelled(fails):
    async def scenario():
        entered, finish = asyncio.Event(), asyncio.Event()

        async def close():
            entered.set()
            await finish.wait()
            if fails:
                raise OSError("synthetic private detail")

        runtime = BudgetRuntime("budget", {"report": service("first")}, (RuntimeResource("report", close),))
        assert await runtime.transition(lambda _: RuntimeCandidate({"report": service("second")}),
                                        retirement_timeout=0) == 2
        await entered.wait()
        status = runtime.status()
        assert status["cleanup_pending"] == 1
        assert status["cleanup_errors"][0]["code"] == "retirement_timeout"
        assert runtime.handle("report").call("read") == "second"
        with pytest.raises(RuntimeTransitionError) as caught:
            await runtime.wait_cleanup(timeout=0)
        assert caught.value.code == "retirement_timeout"
        finish.set()
        status = await runtime.wait_cleanup()
        assert status["cleanup_pending"] == 0
        assert [row["code"] for row in status["cleanup_errors"]] == (
            ["retirement_timeout", "retirement_failed"] if fails else ["retirement_timeout"]
        )

    asyncio.run(scenario())


def test_shutdown_drains_and_rejects_new_work_without_retiring_active_operations():
    async def scenario():
        closed = []
        runtime = BudgetRuntime("budget", {}, (RuntimeResource("storage", lambda: closed.append(True)),))
        entered, finish = asyncio.Event(), asyncio.Event()

        async def operation():
            async with runtime.request():
                entered.set()
                await finish.wait()

        task = asyncio.create_task(operation())
        await entered.wait()
        with pytest.raises(RuntimeTransitionError) as caught:
            await runtime.close(drain_timeout=0)
        assert caught.value.code == "drain_timeout"
        assert runtime.status()["admission"] == "open" and not task.done()
        assert closed == []
        finish.set()
        await task
        await runtime.close()
        assert closed == [True]
        assert runtime.status()["phase"] == "closed"
        assert runtime.status()["admission"] == "closed"
        with pytest.raises(RuntimeTransitionError) as caught:
            async with runtime.request():
                pytest.fail("Closed runtime cannot admit operations")
        assert caught.value.code == "runtime_closed"
        with pytest.raises(RuntimeTransitionError) as caught:
            with runtime.lease():
                pytest.fail("Closed runtime cannot admit operations")
        assert caught.value.code == "runtime_closed"
        with pytest.raises(RuntimeTransitionError):
            await runtime.close()

    asyncio.run(scenario())


def test_transitions_inside_operations_fail_instead_of_deadlocking():
    async def scenario():
        runtime = BudgetRuntime("budget", {})
        async with runtime.request():
            for operation in (
                runtime.transition(lambda _: RuntimeCandidate({})),
                runtime.close(), runtime.recover("active", lambda *_: True),
            ):
                with pytest.raises(RuntimeTransitionError) as caught:
                    await operation
                assert caught.value.code == "transition_in_operation"

    asyncio.run(scenario())


def test_managed_handles_support_async_services_and_reject_missing_services():
    async def scenario():
        async def read():
            return "async"

        runtime = BudgetRuntime("budget", {"report": SimpleNamespace(read=read)})
        handle = runtime.handle("report")
        assert await handle.acall("read") == "async"
        with pytest.raises(RuntimeTransitionError) as caught:
            handle.call("read")
        assert caught.value.code == "async_service"
        with pytest.raises(RuntimeTransitionError) as caught:
            runtime.handle("missing").call("read")
        assert caught.value.code == "service_unavailable"

    asyncio.run(scenario())


@pytest.mark.parametrize("value", [-1, True, float("nan"), float("inf"), "1"])
def test_invalid_deadlines_fail_before_preparation(value):
    async def scenario():
        runtime = BudgetRuntime("budget", {})
        with pytest.raises(RuntimeTransitionError) as caught:
            await runtime.transition(lambda _: pytest.fail("Invalid deadline executed preparation"),
                                     drain_timeout=value)
        assert caught.value.code == "invalid_runtime"
        with pytest.raises(RuntimeTransitionError):
            with runtime.lease(timeout=value):
                pytest.fail("Invalid deadline admitted an operation")
        with pytest.raises(RuntimeTransitionError):
            async with runtime.request(timeout=value):
                pytest.fail("Invalid deadline admitted an operation")

    asyncio.run(scenario())


def test_candidate_and_host_inputs_are_validated_and_snapshots_are_detached():
    runtime = BudgetRuntime("budget", {})
    for name in ("_private", "bad-name", 1):
        with pytest.raises(RuntimeTransitionError):
            runtime.handle(name)
    for budget_id in ("", 1, "x" * 201, "bad\x00id", "\ud800"):
        with pytest.raises(RuntimeTransitionError):
            BudgetRuntime(budget_id, {})
    for values in ([], {"bad-name": object()}, {"_private": object()}):
        with pytest.raises(RuntimeTransitionError):
            RuntimeCandidate(values)
    with pytest.raises(RuntimeTransitionError):
        RuntimeResource("BAD", lambda: None)
    with pytest.raises(RuntimeTransitionError):
        RuntimeResource("report", None)
    resource = RuntimeResource("report", lambda: None)
    for resources in ([resource], (object(),), (resource, resource)):
        with pytest.raises(RuntimeTransitionError):
            RuntimeCandidate({}, resources)
    values = {"report": service("first")}
    candidate = RuntimeCandidate(values)
    values.clear()
    assert candidate.services["report"].read() == "first"
    with pytest.raises(TypeError):
        candidate.services["new"] = object()


def test_invalid_transition_recovery_and_observation_inputs():
    async def scenario():
        runtime = BudgetRuntime("budget", {})
        for arguments in (
            {"prepare": None},
            {"prepare": lambda _: RuntimeCandidate({}), "readiness": 1},
            {"prepare": lambda _: RuntimeCandidate({}), "finalize": 1},
            {"prepare": lambda _: RuntimeCandidate({}), "commit": 1},
            {"prepare": lambda _: RuntimeCandidate({}), "plugin_id": "BAD"},
        ):
            with pytest.raises(RuntimeTransitionError) as caught:
                await runtime.transition(**arguments)
            assert caught.value.code == "invalid_runtime"
        for authority, validate in (("other", lambda *_: True), ("active", None)):
            with pytest.raises(RuntimeTransitionError) as caught:
                await runtime.recover(authority, validate)
            assert caught.value.code == "invalid_runtime"
        with pytest.raises(RuntimeTransitionError) as caught:
            await runtime.recover("active", lambda *_: True)
        assert caught.value.code == "recovery_required"
        with pytest.raises(RuntimeTransitionError) as caught:
            await runtime.wait_for_phase("missing", timeout=0)
        assert caught.value.code == "admission_timeout"

    asyncio.run(scenario())


def test_finalization_uses_latest_drained_state_before_durable_commit():
    async def scenario():
        runtime = BudgetRuntime("budget", {"report": service("old")})
        entered, finish = asyncio.Event(), asyncio.Event()
        latest, observed = [], []

        async def operation():
            async with runtime.request():
                entered.set()
                await finish.wait()
                latest.append("saved model")

        task = asyncio.create_task(operation())
        await entered.wait()

        async def finalize(preparation, candidate):
            assert runtime.status()["admission"] == "gated"
            assert runtime.status()["active_leases"] == 0
            assert latest == ["saved model"]
            observed.append("finalize")
            preparation.own(RuntimeResource("report", lambda: observed.append("close")))
            return RuntimeCandidate({"report": service(latest[0])}, candidate.resources)

        def commit(candidate, number):
            observed.append((candidate.services["report"].read(), number))

        change = asyncio.create_task(runtime.transition(
            lambda _: RuntimeCandidate({"report": service("stale")}),
            finalize=finalize, commit=commit,
        ))
        await runtime.wait_for_phase("draining")
        assert observed == []
        finish.set()
        await task
        assert await change == 2
        assert observed == ["finalize", ("saved model", 2)]
        assert runtime.handle("report").call("read") == "saved model"
        await runtime.shutdown()
        assert observed[-1] == "close"

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["exception", "invalid", "cancel", "busy"])
def test_failed_finalization_resumes_old_generation_and_retires_candidate(failure):
    async def scenario():
        closed, committed = [], []
        old = RuntimeResource("old", lambda: closed.append("old"))
        runtime = BudgetRuntime("budget", {"report": service("old")}, (old,))

        def finalize(preparation, candidate):
            preparation.own(RuntimeResource("candidate", lambda: closed.append("candidate")))
            if failure == "invalid":
                return object()
            if failure == "cancel":
                raise asyncio.CancelledError("private model bytes")
            if failure == "busy":
                raise RuntimeTransitionError("continuation_busy", "categorizer")
            raise OSError("private model bytes")

        with pytest.raises(RuntimeTransitionError) as caught:
            await runtime.transition(lambda _: RuntimeCandidate({"report": service("new")}),
                                     finalize=finalize, commit=lambda *_: committed.append(True))
        assert caught.value.code == ("continuation_busy" if failure == "busy" else "readiness_failed")
        assert caught.value.plugin_id == ("categorizer" if failure == "busy" else None)
        assert not committed
        assert closed == ["candidate"]
        assert runtime.status()["generation"] == 1
        assert runtime.status()["admission"] == "open"
        assert runtime.status()["health"] == "ready"
        assert runtime.handle("report").call("read") == "old"
        assert "private model" not in str(runtime.status())

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["prepare", "readiness", "commit", "retirement"])
def test_plugin_callback_cancellation_is_contained_and_attributed(stage):
    async def scenario():
        closed = []

        async def cancel(*_):
            raise asyncio.CancelledError("synthetic private detail")

        async def old_close():
            closed.append("active")
            if stage == "retirement":
                await cancel()

        active = RuntimeResource("report", old_close)
        runtime = BudgetRuntime("budget", {"report": service("first")}, (active,))

        async def prepare(prep):
            prep.own(RuntimeResource("report", lambda: closed.append("candidate")))
            if stage == "prepare":
                await cancel()
            return prep.candidate({"report": service("second")})

        arguments = {"plugin_id": "report"}
        if stage == "readiness":
            arguments["readiness"] = cancel
        elif stage == "commit":
            arguments["commit"] = cancel
        if stage == "retirement":
            assert await runtime.transition(prepare, **arguments) == 2
            assert runtime.status()["cleanup_errors"][0]["code"] == "retirement_failed"
            assert closed == ["active"]
        else:
            with pytest.raises(RuntimeTransitionError) as caught:
                await runtime.transition(prepare, **arguments)
            assert caught.value.code == ("commit_uncertain" if stage == "commit" else f"{stage}_failed")
            assert caught.value.plugin_id == "report"
            if stage == "commit":
                assert runtime.status()["health"] == "recovery_required"
                assert runtime.status()["admission"] == "gated"
                assert closed == []
                await runtime.recover("active", lambda *_: True)
            assert runtime.handle("report").call("read") == "first"
            assert closed == ["candidate"]
        assert "synthetic private detail" not in str(runtime.status())

    asyncio.run(scenario())


def test_cancelled_waiting_request_cannot_leak_an_admission_or_delay_retirement():
    async def scenario():
        runtime = BudgetRuntime("budget", {})
        entered, finish, waiting = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def operation():
            async with runtime.request():
                entered.set()
                await finish.wait()

        async def queued():
            waiting.set()
            async with runtime.request():
                pytest.fail("Cancelled queued request must not be admitted")

        task = asyncio.create_task(operation())
        await entered.wait()
        change = asyncio.create_task(runtime.transition(lambda _: RuntimeCandidate({})))
        await runtime.wait_for_phase("draining")
        pending = asyncio.create_task(queued())
        await waiting.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert runtime.status()["active_leases"] == 1
        finish.set()
        await task
        await change
        assert runtime.status()["active_leases"] == 0
        assert runtime.status()["generation"] == 2

    asyncio.run(scenario())


def test_copied_thread_scope_remains_admitted_after_outer_request_cancellation():
    async def scenario():
        closed = []
        runtime = BudgetRuntime("budget", {"report": service("first")}, (
            RuntimeResource("report", lambda: closed.append(True)),
        ))
        entered, finish = Event(), Event()
        thread_done = asyncio.Event()
        thread_tasks = []
        observed = []

        def thread_work():
            with runtime.lease() as lease:
                entered.set()
                assert finish.wait(5)
                observed.append(lease.service("report").read())
                assert closed == []

        async def request():
            async with runtime.request():
                thread = asyncio.create_task(asyncio.to_thread(thread_work))
                thread.add_done_callback(lambda _: thread_done.set())
                thread_tasks.append(thread)
                await thread_done.wait()

        caller = asyncio.create_task(request())
        assert await asyncio.to_thread(entered.wait, 5)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert runtime.status()["active_leases"] == 1
        change = asyncio.create_task(runtime.transition(lambda _: RuntimeCandidate({"report": service("second")})))
        await runtime.wait_for_phase("draining")
        assert closed == []
        finish.set()
        await thread_tasks[0]
        await change
        assert observed == ["first"]
        assert closed == [True]

    asyncio.run(scenario())


def test_cancelled_managed_async_caller_keeps_financial_work_and_generation_alive():
    async def scenario():
        entered, finish = asyncio.Event(), asyncio.Event()
        closed, committed = [], []

        async def write():
            entered.set()
            await finish.wait()
            assert closed == []
            committed.append("acknowledged")
            return "saved"

        runtime = BudgetRuntime("budget", {"ledger": SimpleNamespace(write=write)}, (
            RuntimeResource("storage", lambda: closed.append(True)),
        ))

        async def request():
            async with runtime.request():
                return await runtime.handle("ledger").acall("write")

        caller = asyncio.create_task(request())
        await entered.wait()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert runtime.status()["active_leases"] == 1
        change = asyncio.create_task(runtime.transition(lambda _: RuntimeCandidate({})))
        await runtime.wait_for_phase("draining")
        assert not committed and not closed
        finish.set()
        await change
        assert committed == ["acknowledged"]
        assert closed == [True]
        assert runtime.status()["active_leases"] == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("budget_id", [
    "default", "12345678-1234-4234-8234-123456789abc", "UPPER", "Бюджет", "x" * 200,
])
def test_runtime_accepts_public_ledger_identities_including_digit_leading_uuids(budget_id):
    runtime = BudgetRuntime(budget_id, {"report": service("fictional")})
    assert runtime.status()["budget_id"] == budget_id
    with runtime.lease() as lease:
        assert lease.service("report").read() == "fictional"


def test_nested_other_budget_scopes_keep_independent_generation_and_admission():
    async def scenario():
        first = BudgetRuntime("12345678-1234-4234-8234-123456789abc", {
            "report": service("first"),
        })
        second = BudgetRuntime("23456789-1234-4234-8234-123456789abc", {
            "report": service("second"),
        })
        async with first.request() as first_lease:
            async with second.request() as second_lease:
                assert first_lease is not second_lease
                assert first.status()["active_leases"] == second.status()["active_leases"] == 1
                with first.lease() as nested:
                    assert nested is first_lease
                    assert first.handle("report").call("read") == "first"
                async with second.request() as nested:
                    assert nested is second_lease
                    assert await second.handle("report").acall("read") == "second"
            assert first.status()["active_leases"] == 1
            assert second.status()["active_leases"] == 0
            # A lease on one budget must not block another budget's handover.
            assert await second.transition(lambda _: RuntimeCandidate({"report": service("replaced")})) == 2
            assert first_lease.generation.number == 1
            assert first.handle("report").call("read") == "first"
            assert await second.handle("report").acall("read") == "replaced"
        assert first.status()["active_leases"] == second.status()["active_leases"] == 0

    asyncio.run(scenario())
