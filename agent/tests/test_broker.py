"""Native effects require successful isolated output and the complete local scope."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import json
import unittest
from unittest.mock import AsyncMock

from agent.broker import BrokerError, HOST_CAPABILITIES, HostBroker
from agent.isolation import BoundaryUnavailable, ResourceLimitExceeded, ResourceLimits
from agent.process import ProcessResult


def action(tool="volume", kind="set", args=None):
    return {"tool": tool, "action": kind, "args": {"value": 50} if args is None else args}


def output(commands=None, **fields):
    return ProcessResult(0, stdout=json.dumps({"version": 1, "value": 42,
                                               "janus": [action()] if commands is None else commands, **fields}))


class NativeFixture:
    streams = ["volume", "mouse", "metrics", "processes", "screen"]

    def __init__(self):
        self.data = {"volume": {"value": 20, "muted": False}, "mouse": {"x": 1, "y": 2},
                     "metrics": {"cpu_percent": 5}, "processes": {"processes": [{"pid": 3, "ram_bytes": 123}]},
                     "screen": {"mime": "image/jpeg", "width": 960, "height": 600, "display": "primary", "base64": "private pixels"}}
        self.execute = AsyncMock(side_effect=self.execute_action)
        self.disabled = set()

    def snapshot(self, name):
        return deepcopy(self.data.get(name))

    def control_available(self, name):
        return name not in self.disabled

    async def execute_action(self, payload):
        if payload["target"] == "volume":
            self.data["volume"]["value"] = payload["args"]["value"]
        return {"ok": True}


class BrokerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.native = NativeFixture()
        self.broker = HostBroker(self.native)
        self.limits = ResourceLimits()

    def test_available_capabilities_follow_only_locally_enabled_streams(self):
        self.assertEqual(self.broker.capabilities, HOST_CAPABILITIES)
        self.native.streams = ["volume", "metrics", "unknown"]
        self.assertEqual(self.broker.capabilities, frozenset({"audio.volume", "process.inspect"}))
        with self.assertRaises(BoundaryUnavailable):
            self.broker.prepare_input("", frozenset({"desktop.screen"}))

    def test_pure_compute_and_worker_capability_input_remains_exactly_unchanged(self):
        for caps in (frozenset(), frozenset({"files.read", "files.write", "process.spawn"})):
            self.assertEqual(self.broker.prepare_input("typed input\n42", caps), "typed input\n42")

    def test_input_snapshots_are_scoped_copied_and_exclude_image_bytes(self):
        volume = json.loads(self.broker.prepare_input("original input", frozenset({"audio.volume"})))
        self.assertEqual(volume, {"input": "original input", "native": {"volume": {"value": 20, "muted": False, "available": True}}})
        data = json.loads(self.broker.prepare_input("", frozenset({"desktop.screen", "process.inspect"})))
        self.assertEqual(set(data["native"]), {"screen", "metrics", "processes"})
        self.assertNotIn("base64", data["native"]["screen"])
        self.assertNotIn("private pixels", json.dumps(data))
        data["native"]["processes"]["processes"].clear()
        self.assertEqual(len(self.native.data["processes"]["processes"]), 1)

    def test_missing_invalid_and_large_snapshots_are_bounded(self):
        for value in (None, [], {"bad": object()}, {"bad": float("nan")}):
            self.native.data["volume"] = value
            self.assertEqual(self.broker._snapshot("volume"), {"available": False})
        self.native.data["volume"] = {"blob": "x" * 5000}
        self.assertEqual(self.broker._snapshot("volume"), {"available": True, "truncated": True})
        self.native.data["processes"] = {"processes": [{"pid": i, "name": "x" * 200} for i in range(100)]}
        data = self.broker._snapshot("processes")
        self.assertTrue(data["truncated"])
        self.assertLess(len(data["processes"]), 32)
        self.assertLess(len(json.dumps(data).encode()), 4096)

    def test_native_input_budget_is_checked_before_running_code(self):
        with self.assertRaises(ResourceLimitExceeded):
            self.broker.prepare_input("x" * 100, frozenset({"audio.volume"}), max_bytes=100)

    async def test_successful_scoped_actions_share_order_and_get_sees_the_new_value(self):
        before = output([action(), action(kind="get", args={}), action("mouse", "move", {"x": .5, "y": -.5})])
        result = await self.broker.apply(before, frozenset({"audio.volume", "desktop.mouse"}), self.limits)
        data = json.loads(result.stdout)
        self.assertEqual(data["value"], 42)
        self.assertEqual(data["native_results"][1]["data"]["value"], 50)
        self.assertEqual([call.args[0]["target"] for call in self.native.execute.await_args_list], ["volume", "mouse"])
        self.assertEqual(before.stdout, output([action(), action(kind="get", args={}), action("mouse", "move", {"x": .5, "y": -.5})]).stdout)

    async def test_every_read_only_tool_has_scoped_get_without_a_mutating_command(self):
        commands = [action(name, "get", {}) for name in self.native.streams]
        result = await self.broker.apply(output(commands), HOST_CAPABILITIES, self.limits)
        results = json.loads(result.stdout)["native_results"]
        self.assertEqual([item["tool"] for item in results], self.native.streams)
        self.assertNotIn("base64", results[-1]["data"])
        self.native.execute.assert_not_awaited()

    async def test_mouse_buttons_and_scroll_use_the_existing_scalar_contract(self):
        commands = [action("mouse", "button", {"button": "left", "state": "down"}),
                    action("mouse", "button", {"button": "left", "state": "up"}),
                    action("mouse", "scroll", {"amount": -100})]
        result = await self.broker.apply(output(commands), frozenset({"desktop.mouse"}), self.limits)
        self.assertEqual(len(json.loads(result.stdout)["native_results"]), 3)

    async def test_entire_list_is_validated_before_any_native_side_effect(self):
        invalid = [None, {}, {**action(), "extra": "shell"}, action("unknown"), action([], "set"),
                   action(kind=[]), action(args=[]), action("mouse", "move", {"x": 0, "y": 0}),
                   action(kind="get", args={"path": "/private"}), action(kind="set", args={"value": 1, "shell": "cmd"}),
                   action(kind="set", args={"value": True}), action(kind="set", args={"value": 101}),
                   action("volume", "lock", {})]
        for item in invalid:
            with self.subTest(item=str(item)), self.assertRaises(BrokerError):
                await self.broker.apply(output([action(), item]), frozenset({"audio.volume"}), self.limits)
            self.native.execute.assert_not_awaited()
        for item in (action("mouse", "button", {"button": "other", "state": "up"}),
                     action("mouse", "scroll", {"amount": .5}), action("mouse", "move", {"x": 2, "y": 0}),
                     action("mouse", "lock", {})):
            with self.subTest(item=item), self.assertRaises(BrokerError):
                await self.broker.apply(output([action(), item]), HOST_CAPABILITIES, self.limits)
            self.native.execute.assert_not_awaited()

    async def test_granted_but_disabled_controls_are_rejected_before_the_first_effect(self):
        self.native.disabled.add("mouse")
        with self.assertRaises(BoundaryUnavailable):
            await self.broker.apply(output([action(), action("mouse", "move", {"x": 0, "y": 0})]), HOST_CAPABILITIES, self.limits)
        self.native.execute.assert_not_awaited()
        self.native.streams = ["volume"]
        with self.assertRaises(BrokerError):
            await self.broker.apply(output([action("processes", "get", {})]), frozenset({"audio.volume"}), self.limits)

    async def test_failed_timed_out_cancelled_unavailable_and_limited_workers_cannot_apply_actions(self):
        cases = [None, replace(output(), returncode=1), replace(output(), returncode=True)]
        cases += [replace(output(), **{field: True}) for field in ("timed_out", "cancelled", "unavailable", "output_limited")]
        for result in cases:
            with self.subTest(result=result):
                self.assertIs(await self.broker.apply(result, HOST_CAPABILITIES, self.limits), result)
        self.native.execute.assert_not_awaited()

    async def test_text_and_plain_computation_results_never_request_native_actions(self):
        for result in (ProcessResult(0, stdout="hello\n"), ProcessResult(0, stdout='{"version":1,"value":42}'), output([]), ProcessResult(0, stdout="[]")):
            self.assertIs(await self.broker.apply(result, frozenset(), self.limits), result)
        with self.assertRaises(BrokerError):
            await self.broker.apply(ProcessResult(0, stdout="not JSON"), HOST_CAPABILITIES, self.limits)
        self.native.execute.assert_not_awaited()

    async def test_malformed_version_duplicate_keys_nonfinite_and_action_limit_fail_closed(self):
        invalid = [output(version=True), output(version=2), output(native_results=[]), output("not a list"), output([action()] * 9),
                   ProcessResult(0, stdout='{"version":1,"janus":[],"janus":[{"tool":"volume","action":"set","args":{"value":50}}]}'),
                   ProcessResult(0, stdout='{"version":1,"janus":[],"value":NaN}')]
        for result in invalid:
            with self.subTest(result=result.stdout[:80]), self.assertRaises(BrokerError):
                await self.broker.apply(result, HOST_CAPABILITIES, self.limits)
        self.native.execute.assert_not_awaited()

    async def test_initial_output_and_reserved_results_fit_before_any_effect(self):
        for result in (replace(output(), stdout="x" * 65537), replace(output(), stderr="x" * 16385)):
            with self.assertRaises(ResourceLimitExceeded):
                await self.broker.apply(result, HOST_CAPABILITIES, self.limits)
        with self.assertRaises(ResourceLimitExceeded):
            await self.broker.apply(output(value="x" * 8000), HOST_CAPABILITIES, ResourceLimits(stdout_bytes=9000))
        self.native.execute.assert_not_awaited()

    async def test_native_denial_or_failure_stops_remaining_actions_and_hides_raw_errors(self):
        for failure in (ValueError("private path"), RuntimeError("private detail"), OSError("private failure"), None, {"ok": False}, {"ok": 1}):
            self.native.execute.reset_mock()
            if isinstance(failure, BaseException):
                self.native.execute.side_effect = failure
            else:
                self.native.execute.side_effect = None
                self.native.execute.return_value = failure
            with self.subTest(failure=failure), self.assertRaisesRegex(BrokerError, "^Native operation failed; check laptop permissions$"):
                await self.broker.apply(output([action(), action()]), HOST_CAPABILITIES, self.limits)
            self.assertEqual(self.native.execute.await_count, 1)

    async def test_cancellation_stops_the_remaining_valid_actions(self):
        started = asyncio.Event()
        async def waiting(_):
            started.set()
            await asyncio.Event().wait()
        self.native.execute.side_effect = waiting
        task = asyncio.create_task(self.broker.apply(output([action(), action()]), HOST_CAPABILITIES, self.limits))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.native.execute.await_count, 1)
