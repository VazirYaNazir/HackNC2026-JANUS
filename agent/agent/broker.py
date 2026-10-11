"""Typed, locally granted native controls for successfully isolated C jobs.

Workers receive scoped snapshots and return bounded JSON commands. They never
receive a host path, shell, credential, or direct native API handle.
"""
import json
from dataclasses import replace

from .bridge import command_line
from .isolation import BoundaryUnavailable, ResourceLimitExceeded
from .process import ProcessResult

HOST_CAPABILITIES = frozenset({"audio.volume", "desktop.mouse", "desktop.screen", "process.inspect"})
STREAM_CAPABILITY = {"volume": "audio.volume", "mouse": "desktop.mouse", "screen": "desktop.screen",
                     "metrics": "process.inspect", "processes": "process.inspect"}
ARGUMENTS = {("volume", "set"): {"value"}, ("mouse", "move"): {"x", "y"},
             ("mouse", "button"): {"button", "state"}, ("mouse", "scroll"): {"amount"}}
MAX_ACTIONS = 8
MAX_RESULT_BYTES = 4096
MAX_SNAPSHOT_BYTES = MAX_RESULT_BYTES - 256


class BrokerError(ValueError):
    """A fixed native command was malformed, denied, or failed locally."""


def encode(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise BrokerError("Duplicate JSON fields are not permitted")
        result[name] = value
    return result


def reject_constant(_):
    raise BrokerError("Nonfinite JSON is not permitted")


class HostBroker:
    def __init__(self, native):
        self.native = native

    @property
    def capabilities(self):
        return frozenset(STREAM_CAPABILITY[name] for name in self.native.streams if name in STREAM_CAPABILITY)

    def _scope(self, capabilities):
        granted = capabilities & HOST_CAPABILITIES
        if not granted <= self.capabilities:
            raise BoundaryUnavailable("Requested native tools are not enabled locally")
        return granted

    def _snapshot(self, name):
        data = self.native.snapshot(name)
        if not isinstance(data, dict):
            return {"available": False}
        if name == "screen":
            # Images continue on the existing opted-in mirror stream. A C job's
            # bounded stdin/stdout receives screen metadata, never image bytes.
            data = {key: value for key, value in data.items() if key in {"mime", "width", "height", "display"}}
        elif name == "processes" and isinstance(data.get("processes"), list):
            original = data["processes"]
            data["processes"] = original[:32]
            if len(original) > 32:
                data["truncated"] = True
        data["available"] = True
        try:
            while len(encode(data).encode()) > MAX_SNAPSHOT_BYTES:
                if name == "processes" and data.get("processes"):
                    data["processes"].pop()
                    data["truncated"] = True
                else:
                    return {"available": True, "truncated": True}
        except (ValueError, TypeError, UnicodeError):
            return {"available": False}
        return data

    def prepare_input(self, stdin, capabilities, max_bytes=65536):
        granted = self._scope(capabilities)
        if not granted:
            return stdin
        snapshots = {name: self._snapshot(name) for name in self.native.streams
                     if STREAM_CAPABILITY.get(name) in granted}
        result = encode({"input": stdin, "native": snapshots})
        if len(result.encode()) > max_bytes:
            raise ResourceLimitExceeded("Native snapshot input exceeds the local input budget")
        return result

    def _command(self, item, granted):
        if not isinstance(item, dict) or set(item) != {"tool", "action", "args"}:
            raise BrokerError("Native actions require exactly tool, action, and args")
        name, action, args = item["tool"], item["action"], item["args"]
        if not isinstance(name, str) or name not in STREAM_CAPABILITY or not isinstance(action, str) or not isinstance(args, dict):
            raise BrokerError("Unknown native tool or malformed action")
        if STREAM_CAPABILITY[name] not in granted or name not in self.native.streams:
            raise BrokerError("Native action exceeds the locally granted capabilities")
        if action == "get":
            if args:
                raise BrokerError("Native get takes no arguments")
            return name, action, args
        if (name, action) not in ARGUMENTS or set(args) != ARGUMENTS[name, action]:
            raise BrokerError("Unsupported native action or arguments")
        try:
            command_line({"target": name, "action": action, "args": args})
        except (ValueError, TypeError, OverflowError):
            raise BrokerError("Invalid native action arguments") from None
        if not self.native.control_available(name):
            raise BoundaryUnavailable("Native control is not running locally")
        return name, action, args

    async def apply(self, result, capabilities, limits):
        """Validate the full list before applying any action, only after success.

        Local failures can stop a valid list partway through; applied desktop
        effects cannot be rolled back. Cancellation stops subsequent actions.
        """
        granted = self._scope(capabilities)
        if (not isinstance(result, ProcessResult) or type(result.returncode) is not int or result.returncode != 0
                or result.timed_out or result.output_limited or result.unavailable or result.cancelled):
            return result
        if len(result.stdout.encode()) > limits.stdout_bytes or len(result.stderr.encode()) > limits.stderr_bytes:
            raise ResourceLimitExceeded("Worker output exceeds the local output budget")
        try:
            document = json.loads(result.stdout, object_pairs_hook=unique_object,
                                  parse_constant=reject_constant)
        except (ValueError, TypeError, UnicodeError):
            # A pure text result cannot request native effects. Native-capability
            # requests use json-v1, independently checked by the executor.
            if granted:
                raise BrokerError("Native output must be a JSON-v1 object") from None
            return result
        if not isinstance(document, dict) or "janus" not in document:
            return result
        if type(document.get("version")) is not int or document["version"] != 1 or "native_results" in document:
            raise BrokerError("Native output must use version 1 and cannot supply native_results")
        commands = document["janus"]
        if not isinstance(commands, list) or len(commands) > MAX_ACTIONS:
            raise BrokerError("Native output permits at most eight actions")
        prepared = [self._command(item, granted) for item in commands]
        if not prepared:
            return result
        # Reserve the worst-case bounded result size before any desktop effect.
        # No valid command list can mutate the host and then fail just because
        # its result framing did not fit the already-known output budget.
        reserve = len(encode(document).encode()) + 32 + len(prepared) * (MAX_RESULT_BYTES + 1)
        if reserve > limits.stdout_bytes:
            raise ResourceLimitExceeded("Native results exceed the local output budget")
        native_results = []
        for name, action, args in prepared:
            if action == "get":
                outcome = {"tool": name, "action": action, "ok": True, "data": self._snapshot(name)}
            else:
                try:
                    reply = await self.native.execute({"target": name, "action": action, "args": args})
                except (ValueError, RuntimeError, OSError):
                    raise BrokerError("Native operation failed; check laptop permissions") from None
                if not isinstance(reply, dict) or reply.get("ok") is not True:
                    raise BrokerError("Native operation failed; check laptop permissions")
                outcome = {"tool": name, "action": action, "ok": True, "data": self._snapshot(name)}
            native_results.append(outcome)
        document["native_results"] = native_results
        return replace(result, stdout=encode(document))
