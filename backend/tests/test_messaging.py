"""iMessage channel: allowlist, deterministic parser, confirmations, dedupe, offline agent, AI fallback, route auth.

Needs no C compiler: the native layer is faked, and only registered actions can ever be selected.
"""
import asyncio
import json
import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent_runtime.config import Identity
from app.agent_runtime.connections import PipelineError
from app.agent_runtime.native import NativeManager
from app.agent_runtime.events import PanelEvents
from app.messaging import parser
from app.messaging.service import (CONFIRM_SECONDS, DEDUPE_LIMIT, GeminiClassifier, MessagingService, clock_text, mask, normalize_sender,
                                   parse_allowlist, pretty_app)
from app.tiger import TigerUnavailable
from app.routes import messaging as messaging_routes

OWNER = "+15551234567"
STRANGER = "+15559998888"
ALL_STREAMS = ("volume", "mouse", "metrics", "processes", "screen")
UNKNOWN = "Not sure what that means. Text help to see what I can do."
NOTHING_TO_CONFIRM = "Nothing to confirm. Text help to see what I can do."
HELP = """JANUS here. Text me:
• status – CPU and RAM
• apps – what's using the most
• history – why it was slow
• volume 40 – set volume (0–100)
• mute / unmute
• lock – locks your PC (I'll ask first)"""


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class FakeRuntime:
    def __init__(self, *, online=True, streams=ALL_STREAMS, latest=None, client=None):
        self.identity = Identity("laptop", "owner")
        self.registry = SimpleNamespace(devices={"laptop": self.identity})
        connection = SimpleNamespace(closed=False, identity=self.identity, hello=SimpleNamespace(native_streams=list(streams)))
        self.hub = SimpleNamespace(connections={"laptop": connection} if online else {})
        self.native = SimpleNamespace(
            latest={self.identity: latest if latest is not None else {
                "cpu.percent": 23.4, "cpu.temperature": 61.2, "mem.percent": 48.0, "mem.used_mb": 7900.0,
                "volume.percent": 40.0,
                "process.tree": json.dumps([{"name": "chrome", "cpu_percent": 12.0}, {"name": "code", "cpu_percent": 30.0}, {"name": "idle", "cpu_percent": None}]),
            }},
            command=AsyncMock(return_value={"ok": True}), lock=AsyncMock(return_value={"ok": True}))
        self.provider = SimpleNamespace(client=client)


def make(runtime=None, *, allowed=(OWNER,), classifier=None):
    clock = Clock()
    runtime = runtime or FakeRuntime()
    service = MessagingService(runtime, allowed=frozenset(allowed), classifier=classifier, clock=clock)
    return service, runtime, clock


class ParserTests(unittest.TestCase):
    def test_commands_map_to_registered_actions(self):
        cases = {
            "status": "status", "Status?": "status", "cpu": "status", "CPU": "status", "stats": "status", "what's my cpu at?": "status",
            "how's my laptop": "status", "ram": "status", "temp": "status", "memory usage": "status",
            "history": "history", "History?": "history", "slow": "history", "spikes": "history", "why was my laptop slow?": "history",
            "why is my pc so slow": "history", "any cpu spikes?": "history",
            "apps": "apps", "Apps?": "apps", "processes": "apps", "what's running": "apps", "top apps": "apps",
            "volume": "volume_get", "what's the volume?": "volume_get",
            "mute": "mute", "Mute my PC!": "mute", "unmute": "unmute", "please unmute the volume": "unmute",
            "lock": "lock", "Lock my PC.": "lock", "can you lock the laptop": "lock",
            "help": "help", "?": "help", "Commands": "help",
        }
        for text, action in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parser.parse(text).action, action)

    def test_volume_levels(self):
        for text, level in (("volume 30", 30), ("Volume 40%", 40), ("set volume to 0", 0), ("vol 100", 100), ("set the volume at 75", 75)):
            with self.subTest(text=text):
                self.assertEqual(parser.parse(text), parser.Intent("volume_set", {"level": level}))
        for text in ("volume 101", "volume 9999"):
            with self.subTest(text=text):
                self.assertEqual(parser.parse(text).action, "rejected")

    def test_unrecognized_and_chained_texts_do_not_become_actions(self):
        for text in ("", "   ", "hello there", "volume 30 and lock", "lock then mute", "rm -rf /", "run notepad", "x" * 500, "volume loud", "volume -5"):
            with self.subTest(text=text[:30]):
                intent = parser.parse(text)
                self.assertTrue(intent is None or intent.action not in ("lock", "mute", "volume_set"))

    def test_validate_only_accepts_registered_actions_with_exact_args(self):
        self.assertEqual(parser.validate("lock", {}), parser.Intent("lock"))
        self.assertEqual(parser.validate("volume_set", {"level": 5}), parser.Intent("volume_set", {"level": 5}))
        for action, args in (("shell", {}), ("lock", {"force": True}), ("volume_set", {}), ("volume_set", {"level": 101}), ("volume_set", {"level": "5"}),
                             ("volume_set", {"level": True}), ("volume_set", {"level": 5, "extra": 1}), ("none", {}), (None, {}), ("lock", None)):
            with self.subTest(action=action, args=args):
                self.assertIsNone(parser.validate(action, args))

    def test_confirmation_words(self):
        self.assertTrue(parser.is_yes("YES")); self.assertTrue(parser.is_yes(" yes! "))
        self.assertTrue(parser.is_no("no")); self.assertFalse(parser.is_yes("yes lock it"))


class AllowlistTests(unittest.IsolatedAsyncioTestCase):
    def test_parsing_keeps_only_e164(self):
        with self.assertLogs("uvicorn.error", level="WARNING") as logs:
            allowed = parse_allowlist(f" {OWNER}, +1 (555) 000-1111 ,5551234567, nonsense,,+0123 ,a@b.co")
        self.assertEqual(allowed, {OWNER, "+15550001111"})
        self.assertEqual(len(logs.records), 4)
        self.assertNotIn("nonsense", "".join(logs.output))
        self.assertEqual(parse_allowlist(None), frozenset())

    def test_sender_normalization(self):
        self.assertEqual(normalize_sender("+1 (555) 123-4567"), OWNER)
        self.assertEqual(normalize_sender("Me@Example.COM"), "me@example.com")
        self.assertEqual(mask(OWNER), "***4567")

    async def test_unlisted_senders_get_no_reply_and_nothing_runs_or_leaks_into_logs(self):
        service, runtime, _ = make()
        with self.assertLogs("uvicorn.error", level="INFO") as logs:
            reply = await service.handle(STRANGER, "lock super-secret-body", "m1")
        self.assertIsNone(reply.text)
        runtime.native.lock.assert_not_awaited()
        output = "".join(logs.output)
        self.assertIn("allowlist", output)
        self.assertNotIn("super-secret-body", output)
        self.assertNotIn(STRANGER, output)  # only the masked tail is logged

    async def test_empty_allowlist_means_nobody(self):
        service, _, _ = make(allowed=())
        self.assertFalse(service.enabled)
        self.assertIsNone((await service.handle(OWNER, "cpu", "m1")).text)

    async def test_unlisted_sender_cannot_confirm_someone_elses_pending_action(self):
        service, runtime, _ = make(allowed=(OWNER, STRANGER))
        await service.handle(OWNER, "lock", "m1")
        self.assertEqual((await service.handle(STRANGER, "yes", "m2")).text, NOTHING_TO_CONFIRM)
        runtime.native.lock.assert_not_awaited()


class ConfirmationTests(unittest.IsolatedAsyncioTestCase):
    async def test_lock_requires_yes_then_runs_once(self):
        service, runtime, _ = make()
        prompt = await service.handle(OWNER, "lock", "m1")
        self.assertEqual(prompt.text, f"Lock your PC? Reply YES within {CONFIRM_SECONDS}s")
        runtime.native.lock.assert_not_awaited()
        self.assertEqual((await service.handle(OWNER, "yes", "m2")).text, "Locked.")
        runtime.native.lock.assert_awaited_once_with(runtime.identity, source="imessage", request_id="m2")
        self.assertEqual((await service.handle(OWNER, "yes", "m3")).text, NOTHING_TO_CONFIRM)
        runtime.native.lock.assert_awaited_once()

    async def test_confirmation_expires(self):
        service, runtime, clock = make()
        await service.handle(OWNER, "lock", "m1")
        clock.now += CONFIRM_SECONDS + 1
        self.assertEqual((await service.handle(OWNER, "YES", "m2")).text, "That confirmation expired. Send the command again.")
        runtime.native.lock.assert_not_awaited()
        self.assertEqual((await service.handle(OWNER, "yes", "m3")).text, NOTHING_TO_CONFIRM)

    async def test_confirmation_at_the_deadline_still_works(self):
        service, runtime, clock = make()
        await service.handle(OWNER, "lock", "m1")
        clock.now += CONFIRM_SECONDS
        self.assertEqual((await service.handle(OWNER, "yes", "m2")).text, "Locked.")

    async def test_no_cancels(self):
        service, runtime, _ = make()
        await service.handle(OWNER, "lock", "m1")
        self.assertEqual((await service.handle(OWNER, "no", "m2")).text, "Cancelled.")
        await service.handle(OWNER, "yes", "m3")
        runtime.native.lock.assert_not_awaited()

    async def test_any_other_message_abandons_the_pending_action(self):
        service, runtime, _ = make()
        await service.handle(OWNER, "lock", "m1")
        self.assertIn("CPU 23%", (await service.handle(OWNER, "cpu", "m2")).text)
        await service.handle(OWNER, "yes", "m3")
        runtime.native.lock.assert_not_awaited()

    async def test_mute_runs_immediately_without_confirmation_and_unmute_restores_the_previous_volume(self):
        service, runtime, _ = make()
        self.assertEqual((await service.handle(OWNER, "mute", "m1")).text, "Muted (volume 0).")
        runtime.native.command.assert_awaited_once_with(runtime.identity, "native_volume", 0, source="imessage", request_id="m1")
        self.assertEqual(service._pending, {})
        self.assertEqual((await service.handle(OWNER, "yes", "m2")).text, NOTHING_TO_CONFIRM)
        self.assertEqual((await service.handle(OWNER, "unmute", "m3")).text, "Unmuted - volume 40%.")
        runtime.native.command.assert_awaited_with(runtime.identity, "native_volume", 40, source="imessage", request_id="m3")

    async def test_unmute_without_history_uses_a_safe_default(self):
        service, runtime, _ = make()
        await service.handle(OWNER, "unmute", "m1")
        runtime.native.command.assert_awaited_with(runtime.identity, "native_volume", 30, source="imessage", request_id="m1")

    async def test_pending_state_is_per_sender(self):
        other = "+15550001111"
        service, runtime, _ = make(allowed=(OWNER, other))
        await service.handle(OWNER, "lock", "m1")
        self.assertEqual((await service.handle(other, "yes", "m2")).text, NOTHING_TO_CONFIRM)
        runtime.native.lock.assert_not_awaited()
        self.assertEqual((await service.handle(OWNER, "yes", "m3")).text, "Locked.")


class DedupeTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_redelivered_message_id_runs_once(self):
        service, runtime, _ = make()
        first = await service.handle(OWNER, "volume 30", "same-id")
        again = await service.handle(OWNER, "volume 30", "same-id")
        self.assertEqual(first.text, "Volume set to 30%.")
        self.assertTrue(again.duplicate)
        self.assertIsNone(again.text)
        self.assertEqual(runtime.native.command.await_count, 1)

    async def test_a_redelivered_yes_cannot_confirm_twice(self):
        service, runtime, _ = make()
        await service.handle(OWNER, "lock", "m1")
        await service.handle(OWNER, "yes", "m2")
        await service.handle(OWNER, "yes", "m2")
        runtime.native.lock.assert_awaited_once()

    async def test_ids_expire_and_the_table_is_bounded(self):
        service, runtime, clock = make()
        await service.handle(OWNER, "volume 10", "old")
        clock.now += 3601
        await service.handle(OWNER, "volume 10", "old")
        self.assertEqual(runtime.native.command.await_count, 2)
        for n in range(DEDUPE_LIMIT + 50):
            await service.handle(OWNER, "volume", f"id-{n}")
        self.assertLessEqual(len(service._seen), DEDUPE_LIMIT)

    async def test_concurrent_duplicates_run_once(self):
        service, runtime, _ = make()
        await asyncio.gather(*(service.handle(OWNER, "volume 20", "dup") for _ in range(5)))
        self.assertEqual(runtime.native.command.await_count, 1)


class OfflineTests(unittest.IsolatedAsyncioTestCase):
    async def test_offline_agent_gets_a_friendly_reply_and_no_pending_confirmation(self):
        service, runtime, _ = make(FakeRuntime(online=False))
        for n, text in enumerate(("status", "volume 30", "mute", "apps")):
            self.assertEqual((await service.handle(OWNER, text, f"m{n}")).text, "Your laptop is offline.")
        self.assertEqual((await service.handle(OWNER, "lock", "ml")).text, "Your laptop is offline.")
        self.assertEqual(service._pending, {})
        runtime.native.command.assert_not_awaited()

    async def test_help_works_while_offline(self):
        service, _, _ = make(FakeRuntime(online=False))
        self.assertIn("lock", (await service.handle(OWNER, "help", "m1")).text)

    async def test_agent_that_drops_mid_confirmation(self):
        service, runtime, _ = make()
        await service.handle(OWNER, "lock", "m1")
        runtime.hub.connections.clear()
        self.assertEqual((await service.handle(OWNER, "yes", "m2")).text, "Your laptop is offline.")
        runtime.native.lock.assert_not_awaited()

    async def test_pipeline_errors_become_short_replies(self):
        service, runtime, _ = make()
        for reason, expected in (("NATIVE_TOOLS_NOT_ENABLED", "isn't enabled"), ("NATIVE_COMMAND_TIMEOUT", "didn't respond"), ("NATIVE_COMMAND_FAILED", "didn't work"), ("AGENT_OFFLINE", "offline")):
            runtime.native.command.side_effect = PipelineError(reason, 409)
            self.assertIn(expected, (await service.handle(OWNER, "volume 10", f"e-{reason}")).text)


class ActionTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_has_cpu_ram_and_temperature_when_available(self):
        service, _, _ = make()
        for n, text in enumerate(("status", "cpu", "stats", "what's my cpu at?")):
            self.assertEqual((await service.handle(OWNER, text, f"m{n}")).text, "CPU 23% · 61°C · RAM 48% (7.7 GB)")

    async def test_status_omits_whatever_is_unavailable_instead_of_failing(self):
        for latest, expected in (
            ({"cpu.percent": 23.4, "mem.percent": 48.0, "mem.used_mb": 7900.0}, "CPU 23% · RAM 48% (7.7 GB)"),
            ({"cpu.percent": 23.4, "cpu.temperature": None, "mem.percent": 48.0, "mem.used_mb": None}, "CPU 23% · RAM 48%"),
            ({"cpu.percent": 5.0, "cpu.temperature": None, "mem.percent": None}, "CPU 5%"),
            ({"cpu.percent": None, "cpu.temperature": None, "mem.percent": 61.0, "mem.used_mb": 12288.0}, "RAM 61% (12.0 GB)"),
            ({"cpu.percent": None, "cpu.temperature": None, "mem.percent": None}, "No stats available right now."),
            ({}, "No stats available right now."),
        ):
            with self.subTest(latest=latest):
                service, _, _ = make(FakeRuntime(latest=latest))
                self.assertEqual((await service.handle(OWNER, "status", "m1")).text, expected)

    async def test_streams_that_are_off_say_how_to_enable_them(self):
        service, _, _ = make(FakeRuntime(streams=("volume",)))
        self.assertIn("--native-tools", (await service.handle(OWNER, "status", "m1")).text)
        self.assertIn("--native-tools", (await service.handle(OWNER, "apps", "m2")).text)

    async def test_volume_get_set(self):
        service, runtime, _ = make()
        self.assertEqual((await service.handle(OWNER, "volume", "m1")).text, "Volume is 40%.")
        self.assertEqual((await service.handle(OWNER, "volume 30", "m2")).text, "Volume set to 30%.")
        runtime.native.command.assert_awaited_once_with(runtime.identity, "native_volume", 30, source="imessage", request_id="m2")
        self.assertEqual((await service.handle(OWNER, "volume 300", "m3")).text, "Volume must be between 0 and 100.")
        self.assertEqual(runtime.native.command.await_count, 1)

    async def test_apps(self):
        service, _, _ = make()
        for n, text in enumerate(("apps", "processes")):
            self.assertEqual((await service.handle(OWNER, text, f"m{n}")).text, "Busiest: code 30%, chrome 12%")

    async def test_screenshots_are_gone(self):
        from app.messaging import parser as p
        self.assertNotIn("screenshot", p.ACTIONS)
        service, runtime, _ = make()
        for n, text in enumerate(("screenshot", "take a screenshot", "what's on my screen", "screen")):
            self.assertEqual((await service.handle(OWNER, text, f"s{n}")).text, UNKNOWN)

    async def test_help_lists_commands_and_oversize_or_unknown_text_is_handled(self):
        service, _, _ = make()
        for n, text in enumerate(("help", "Help!", "?", "commands")):
            self.assertEqual((await service.handle(OWNER, text, f"h{n}")).text, HELP)
        self.assertEqual((await service.handle(OWNER, "cpu " + "x" * 600, "m2")).text, "That message is too long. Text help to see what I can do.")
        self.assertEqual((await service.handle(OWNER, "write me a poem", "m3")).text, UNKNOWN)


class HistoryTests(unittest.IsolatedAsyncioTestCase):
    """'why was my laptop slow?' answers from Tiger Data (metrics_1m + process_samples); everything else is unchanged."""

    PEAK = datetime(2026, 10, 11, 16, 31).astimezone()  # local wall-clock 4:31 PM, whatever the machine's time zone

    def tiger(self, **overrides):
        store = SimpleNamespace(worst_minute=AsyncMock(return_value={"bucket": self.PEAK, "cpu": 94.2}),
                                top_apps=AsyncMock(return_value=["chrome.exe", "Code.exe", "Teams"]))
        for name, value in overrides.items():
            setattr(store, name, value)
        return store

    def service(self, store, runtime=None):
        runtime = runtime or FakeRuntime()
        return MessagingService(runtime, allowed=frozenset({OWNER}), clock=Clock(), tiger=store), runtime

    async def test_every_phrasing_gets_the_same_answer(self):
        store = self.tiger()
        service, _ = self.service(store)
        for n, text in enumerate(("why was my laptop slow?", "slow", "spikes", "history")):
            self.assertEqual((await service.handle(OWNER, text, f"h{n}")).text, "CPU peaked at 94% at 4:31 PM. Busiest: Chrome, Code, Teams.")
        store.worst_minute.assert_awaited_with("laptop", 60)
        store.top_apps.assert_awaited_with("laptop", self.PEAK, 3)

    async def test_the_peak_alone_is_reported_when_no_process_samples_exist(self):
        service, _ = self.service(self.tiger(top_apps=AsyncMock(return_value=[])))
        self.assertEqual((await service.handle(OWNER, "slow", "m1")).text, "CPU peaked at 94% at 4:31 PM.")

    async def test_morning_and_midnight_use_a_12_hour_clock(self):
        for moment, expected in ((datetime(2026, 10, 11, 0, 5), "12:05 AM"), (datetime(2026, 10, 11, 9, 7), "9:07 AM"),
                                 (datetime(2026, 10, 11, 12, 0), "12:00 PM"), (datetime(2026, 10, 11, 23, 59), "11:59 PM")):
            self.assertEqual(clock_text(moment.astimezone()), expected)

    async def test_app_names_are_tidied(self):
        self.assertEqual([pretty_app(n) for n in ("chrome.exe", "Code.exe", "Teams", "msedge", "WINWORD.EXE", "")], ["Chrome", "Code", "Teams", "Msedge", "WINWORD", ""])

    async def test_no_data_unavailable_and_not_configured_have_their_own_replies(self):
        empty, _ = self.service(self.tiger(worst_minute=AsyncMock(return_value=None)))
        self.assertEqual((await empty.handle(OWNER, "history", "m1")).text, "No history yet. Give me a few minutes of data.")
        down, _ = self.service(self.tiger(worst_minute=AsyncMock(side_effect=TigerUnavailable("OperationalError"))))
        self.assertEqual((await down.handle(OWNER, "history", "m1")).text, "History isn't available right now.")
        missing_apps, _ = self.service(self.tiger(top_apps=AsyncMock(side_effect=TigerUnavailable("OperationalError"))))
        self.assertEqual((await missing_apps.handle(OWNER, "history", "m1")).text, "History isn't available right now.")
        unset, _ = self.service(None)
        self.assertIn("no time-series database", (await unset.handle(OWNER, "history", "m1")).text)

    async def test_history_needs_the_laptop_to_be_online_like_every_other_command(self):
        store = self.tiger()
        service, _ = self.service(store, FakeRuntime(online=False))
        self.assertEqual((await service.handle(OWNER, "history", "m1")).text, "Your laptop is offline.")
        store.worst_minute.assert_not_awaited()

    async def test_unlisted_senders_cannot_read_history(self):
        store = self.tiger()
        service, _ = self.service(store)
        self.assertIsNone((await service.handle(STRANGER, "history", "m1")).text)
        store.worst_minute.assert_not_awaited()


class ActivityLogTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_only_commands_are_logged_as_imessage_activity_with_the_message_id(self):
        service, runtime, _ = make()
        runtime.native.sink = MagicMock()
        for n, text in enumerate(("status", "apps", "volume")):
            await service.handle(OWNER, text, f"read-{n}")
        calls = [c.args for c in runtime.native.sink.action.call_args_list]
        self.assertEqual(calls, [("laptop", "imessage", "status", {}, "ok", "read-0"), ("laptop", "imessage", "apps", {}, "ok", "read-1"),
                                 ("laptop", "imessage", "volume_get", {}, "ok", "read-2")])

    async def test_help_unknown_and_declined_texts_are_not_logged(self):
        service, runtime, _ = make()
        runtime.native.sink = MagicMock()
        for n, text in enumerate(("help", "write me a poem", "yes")):
            await service.handle(OWNER, text, f"x{n}")
        await service.handle(OWNER, "lock", "x3")
        await service.handle(OWNER, "no", "x4")
        runtime.native.sink.action.assert_not_called()

    async def test_native_controls_pass_their_source_down_instead_of_double_logging(self):
        service, runtime, _ = make()
        runtime.native.sink = MagicMock()
        await service.handle(OWNER, "volume 30", "v1")
        runtime.native.sink.action.assert_not_called()  # NativeManager logs it (with source="imessage"); the service must not repeat it
        runtime.native.command.assert_awaited_once_with(runtime.identity, "native_volume", 30, source="imessage", request_id="v1")

    async def test_a_missing_sink_is_fine(self):
        service, _, _ = make()
        self.assertIn("CPU", (await service.handle(OWNER, "status", "m1")).text)


class FallbackTests(unittest.IsolatedAsyncioTestCase):
    def provider(self, reply, client=object()):
        return SimpleNamespace(client=client, structured=AsyncMock(side_effect=reply if isinstance(reply, Exception) else None, return_value=None if isinstance(reply, Exception) else reply))

    async def test_valid_choice_is_used_but_still_goes_through_confirmation(self):
        classifier = GeminiClassifier(self.provider('{"action": "lock", "level": null}'))
        service, runtime, _ = make(classifier=classifier)
        self.assertEqual((await service.handle(OWNER, "secure my machine", "m1")).text, "Lock your PC? Reply YES within 60s")
        runtime.native.lock.assert_not_awaited()

    async def test_fallback_can_choose_a_volume_level_within_bounds(self):
        service, runtime, _ = make(classifier=GeminiClassifier(self.provider('{"action": "volume_set", "level": 25}')))
        await service.handle(OWNER, "make it quieter please", "m1")
        runtime.native.command.assert_awaited_once_with(runtime.identity, "native_volume", 25, source="imessage", request_id="m1")

    async def test_anything_outside_the_registered_list_is_rejected(self):
        for raw in ('{"action": "shell", "level": null}', '{"action": "lock", "level": 5}', '{"action": "volume_set", "level": 500}',
                    '{"action": "volume_set", "level": null}', '{"action": "none", "level": null}', '{"action": "lock", "command": "rm -rf /"}',
                    "not json", "[]", '{"level": 5}', ""):
            with self.subTest(raw=raw):
                service, runtime, _ = make(classifier=GeminiClassifier(self.provider(raw)))
                reply = await service.handle(OWNER, "do something odd", "m1")
                self.assertEqual(reply.text, UNKNOWN)
                runtime.native.lock.assert_not_awaited(); runtime.native.command.assert_not_awaited()

    async def test_provider_errors_fall_back_to_the_normal_unknown_reply(self):
        service, _, _ = make(classifier=GeminiClassifier(self.provider(RuntimeError("boom"))))
        self.assertEqual((await service.handle(OWNER, "odd request", "m1")).text, UNKNOWN)

    async def test_deterministic_parser_wins_and_never_calls_the_model(self):
        provider = self.provider('{"action": "lock", "level": null}')
        service, runtime, _ = make(classifier=GeminiClassifier(provider))
        await service.handle(OWNER, "volume 10", "m1")
        provider.structured.assert_not_awaited()

    def test_fallback_is_off_without_a_gemini_credential(self):
        self.assertFalse(GeminiClassifier(SimpleNamespace(client=None, structured=AsyncMock())).available)
        runtime = FakeRuntime(client=None)
        with patch.dict(os.environ, {"IMESSAGE_ALLOWED_SENDERS": OWNER}, clear=True):
            self.assertIsNone(MessagingService.from_env(runtime).classifier)
        runtime = FakeRuntime(client=object()); runtime.provider.structured = AsyncMock()
        with patch.dict(os.environ, {"IMESSAGE_ALLOWED_SENDERS": OWNER}, clear=True):
            self.assertIsNotNone(MessagingService.from_env(runtime).classifier)
        with patch.dict(os.environ, {"IMESSAGE_ALLOWED_SENDERS": OWNER, "IMESSAGE_AI_FALLBACK": "0"}, clear=True):
            self.assertIsNone(MessagingService.from_env(runtime).classifier)


class RouteTests(unittest.TestCase):
    TOKEN = "t" * 40

    def client(self, runtime=None, peer=("127.0.0.1", 50000)):
        app = FastAPI()
        service, runtime, _ = make(runtime)
        app.state.agent_runtime, app.state.messaging = runtime, service
        app.include_router(messaging_routes.router)
        self.runtime = runtime
        return TestClient(app, client=peer)

    def post(self, client, body=None, token=None, headers=None, path="/api/messaging/inbound"):
        headers = {"Authorization": f"Bearer {self.TOKEN if token is None else token}", **(headers or {})}
        return client.post(path, json=body if body is not None else {"sender": OWNER, "text": "cpu", "messageId": "r1"}, headers=headers)

    def test_happy_path_and_reply_shape(self):
        with patch.dict(os.environ, {"MESSAGING_SERVICE_TOKEN": self.TOKEN}):
            response = self.post(self.client())
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"reply": "CPU 23% · 61°C · RAM 48% (7.7 GB)", "duplicate": False})

    def test_disabled_without_a_strong_token(self):
        for env in ({}, {"MESSAGING_SERVICE_TOKEN": "short"}):
            with patch.dict(os.environ, env, clear=True):
                self.assertEqual(self.post(self.client(), token="short").status_code, 503)

    def test_wrong_or_missing_token_is_rejected(self):
        with patch.dict(os.environ, {"MESSAGING_SERVICE_TOKEN": self.TOKEN}):
            client = self.client()
            self.assertEqual(self.post(client, token="x" * 40).status_code, 401)
            self.assertEqual(client.post("/api/messaging/inbound", json={"sender": OWNER, "text": "cpu", "messageId": "r1"}).status_code, 401)
            self.assertEqual(client.post("/api/messaging/inbound", json={"sender": OWNER, "text": "cpu", "messageId": "r1"}, headers={"Authorization": self.TOKEN}).status_code, 401)

    def test_loopback_only_even_with_a_valid_token(self):
        with patch.dict(os.environ, {"MESSAGING_SERVICE_TOKEN": self.TOKEN}):
            self.assertEqual(self.post(self.client(peer=("203.0.113.9", 1))).status_code, 403)
            self.assertEqual(self.post(self.client(peer=("192.168.1.20", 1))).status_code, 403)
            self.assertEqual(self.post(self.client(peer=("::1", 1))).status_code, 200)

    def test_requests_relayed_by_a_tunnel_or_proxy_are_refused(self):
        # cloudflared connects from localhost, so the socket peer alone cannot prove the request is local.
        with patch.dict(os.environ, {"MESSAGING_SERVICE_TOKEN": self.TOKEN}):
            client = self.client()
            for header in ("CF-Connecting-IP", "CF-Ray", "X-Forwarded-For", "X-Real-IP", "Forwarded"):
                with self.subTest(header=header):
                    self.assertEqual(self.post(client, headers={header: "1.2.3.4"}).status_code, 403)

    def test_malformed_payloads_are_rejected(self):
        with patch.dict(os.environ, {"MESSAGING_SERVICE_TOKEN": self.TOKEN}):
            client = self.client()
            for body in ({}, {"sender": OWNER}, {"sender": OWNER, "text": "", "messageId": "a"}, {"sender": OWNER, "text": "cpu", "messageId": "a", "x": 1},
                         {"sender": OWNER, "text": 5, "messageId": "a"}, {"sender": "", "text": "cpu", "messageId": "a"}):
                with self.subTest(body=body):
                    self.assertEqual(self.post(client, body).status_code, 422)

    def test_unlisted_sender_gets_an_empty_reply(self):
        with patch.dict(os.environ, {"MESSAGING_SERVICE_TOKEN": self.TOKEN}):
            body = self.post(self.client(), {"sender": STRANGER, "text": "lock", "messageId": "r9"}).json()
            self.assertEqual(body, {"reply": None, "duplicate": False})
            self.runtime.native.lock.assert_not_awaited()

    def test_heartbeat_marks_the_sidecar_connected(self):
        with patch.dict(os.environ, {"MESSAGING_SERVICE_TOKEN": self.TOKEN}):
            client = self.client()
            service = client.app.state.messaging
            self.assertFalse(service.connected())
            self.assertEqual(self.post(client, {"connected": True}, path="/api/messaging/heartbeat").status_code, 200)
            self.assertTrue(service.connected())
            service.clock.now += 46
            self.assertFalse(service.connected())
            # A 'down' report turns it off immediately instead of waiting out the timeout.
            self.post(client, {"connected": True}, path="/api/messaging/heartbeat")
            self.assertTrue(service.connected())
            self.assertEqual(self.post(client, {"connected": False}, path="/api/messaging/heartbeat").status_code, 200)
            self.assertFalse(service.connected())
            self.post(client, {"connected": True}, path="/api/messaging/heartbeat")  # and it recovers on the next report
            self.assertTrue(service.connected())

    def test_studio_status_requires_the_desktop_credential(self):
        from app.routes.studio import desktop
        with patch.dict(os.environ, {"MESSAGING_SERVICE_TOKEN": self.TOKEN}):
            client = self.client()
            client.app.dependency_overrides[desktop] = lambda: Identity("laptop", "owner")
            self.assertEqual(client.get("/api/v1/studio/messaging").json(), {"enabled": True, "connected": False})
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(client.get("/api/v1/studio/messaging").json()["enabled"], False)


class NativeLockTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        class Connection:
            identity = Identity("device", "owner")
            hello = SimpleNamespace(native_streams=["volume", "mouse"])
        self.connection = Connection()
        self.manager = NativeManager(SimpleNamespace(get=lambda device: self.connection), PanelEvents())

    def frame(self, payload):
        return SimpleNamespace(job_id=None, state="CONNECTED", payload=payload)

    async def test_lock_sends_the_fixed_mouse_lock_command(self):
        sent = []

        async def send(kind, job, state, payload):
            sent.append(payload)
            await self.manager.receive(self.connection, self.frame({"type": "command_result", "stream": "mouse", "request_id": payload["request_id"], "ok": True, "data": {}}))
        self.connection.send = AsyncMock(side_effect=send)
        self.assertTrue((await self.manager.lock(self.connection.identity))["ok"])
        self.assertEqual([(p["target"], p["action"], p["args"]) for p in sent], [("mouse", "lock", {})])
        self.assertEqual(self.manager.pending, {})

    async def test_lock_is_not_a_panel_widget_action(self):
        from app.agent_runtime.native import NATIVE_ACTIONS
        self.assertNotIn("native_lock", NATIVE_ACTIONS)
        with self.assertRaisesRegex(PipelineError, "NATIVE_ACTION_NOT_FOUND"):
            await self.manager.command(self.connection.identity, "native_lock", None)

    async def test_lock_failure_and_disabled_mouse_tool(self):
        async def fail(kind, job, state, payload):
            await self.manager.receive(self.connection, self.frame({"type": "command_result", "stream": "mouse", "request_id": payload["request_id"], "ok": False}))
        self.connection.send = AsyncMock(side_effect=fail)
        with self.assertRaisesRegex(PipelineError, "NATIVE_COMMAND_FAILED"):
            await self.manager.lock(self.connection.identity)
        self.connection.hello = SimpleNamespace(native_streams=["volume"])
        with self.assertRaisesRegex(PipelineError, "NOT_ENABLED"):
            await self.manager.lock(self.connection.identity)


if __name__ == "__main__":
    unittest.main()
