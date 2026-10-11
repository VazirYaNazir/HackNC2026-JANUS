"""Deterministic text -> action mapping for the iMessage channel.

A text can only ever select one of the registered actions below, with validated arguments. Nothing here (or in the
optional AI fallback, which must go through `validate`) can generate, compile or run code.
"""
import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ActionSpec:
    id: str
    summary: str
    example: str
    confirm: bool = False


ACTIONS: dict[str, ActionSpec] = {spec.id: spec for spec in (
    ActionSpec("status", "CPU and RAM", "status"),
    ActionSpec("apps", "what's using the most", "apps"),
    ActionSpec("volume_get", "current volume", "volume"),
    ActionSpec("volume_set", "set the volume (0-100)", "volume 40"),
    ActionSpec("mute", "mute", "mute"),
    ActionSpec("unmute", "restore the volume", "unmute"),
    ActionSpec("lock", "lock the PC (asks you to confirm)", "lock", confirm=True),
    ActionSpec("help", "list commands", "help"),
)}

# Never selectable by the AI fallback or any other caller: they are handled by the confirmation state machine.
CONFIRM_YES = {"yes", "y", "yep", "yeah", "confirm", "ok", "okay", "do it"}
CONFIRM_NO = {"no", "n", "nope", "cancel", "stop", "abort"}

UNKNOWN_REPLY = "Not sure what that means. Text help to see what I can do."

HELP_TEXT = """JANUS here. Text me:
• status – CPU and RAM
• apps – what's using the most
• volume 40 – set volume (0–100)
• mute / unmute
• lock – locks your PC (I'll ask first)"""


@dataclass(frozen=True)
class Intent:
    action: str
    args: dict = field(default_factory=dict)


def normalize(text: str) -> str:
    text = re.sub(r"[^\w%\s?+.'-]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip(" .!?")


def is_yes(text: str) -> bool:
    return normalize(text) in CONFIRM_YES


def is_no(text: str) -> bool:
    return normalize(text) in CONFIRM_NO


_FILLER = r"(?:please |pls |hey |can you |could you )*"
_HELP = re.compile(rf"^{_FILLER}(?:help|commands|menu|what can you do|\?)$")
_LOCK = re.compile(rf"^{_FILLER}lock(?: (?:my|the))?(?: (?:pc|laptop|computer|screen|mac|machine))?$")
_UNMUTE = re.compile(rf"^{_FILLER}unmute(?: (?:my|the))?(?: (?:pc|laptop|volume|sound|audio))?$")
_MUTE = re.compile(rf"^{_FILLER}mute(?: (?:my|the))?(?: (?:pc|laptop|volume|sound|audio))?$")
_VOLUME_SET = re.compile(rf"^{_FILLER}(?:set )?(?:the )?vol(?:ume)?(?: to| at)? (\d{{1,4}}) ?%?$")
_VOLUME_GET = re.compile(rf"^(?:what'?s |what is |how loud is )?(?:my |the )?vol(?:ume)?(?: level| at)?$")
# "apps" (alias "processes"), plus the phrasings people actually type.
_APPS = re.compile(r"\b(?:apps?|process(?:es)?|top apps?|what'?s (?:running|using)|task manager)\b")
# "status" (aliases "cpu", "stats"); ram/memory/temp are accepted too and answer with the same status line.
_STATUS = re.compile(r"\b(?:status|cpu|stats?|ram|mem(?:ory)?|temp(?:erature)?|how'?s my (?:pc|laptop|computer|mac))\b")


def parse(text: str) -> Intent | None:
    """Return the matching registered action, or None. Patterns are anchored so a text cannot chain commands."""
    if text.strip() == "?":
        return Intent("help")
    words = normalize(text)
    if not words or len(words) > 200:
        return None
    if _HELP.match(words):
        return Intent("help")
    if _LOCK.match(words):
        return Intent("lock")
    if _UNMUTE.match(words):
        return Intent("unmute")
    if _MUTE.match(words):
        return Intent("mute")
    match = _VOLUME_SET.match(words)
    if match:
        level = int(match.group(1))
        return Intent("volume_set", {"level": level}) if 0 <= level <= 100 else Intent("rejected", {"reason": "Volume must be between 0 and 100."})
    if _VOLUME_GET.match(words):
        return Intent("volume_get")
    if _APPS.search(words):
        return Intent("apps")
    if _STATUS.search(words):
        return Intent("status")
    return None


def validate(action: object, args: object) -> Intent | None:
    """Strictly validate an (action, args) pair that came from somewhere untrusted, such as the AI fallback."""
    if not isinstance(action, str) or action not in ACTIONS:
        return None
    if not isinstance(args, dict):
        return None
    if action == "volume_set":
        level = args.get("level")
        if set(args) != {"level"} or type(level) is not int or not 0 <= level <= 100:
            return None
        return Intent(action, {"level": level})
    return Intent(action) if not args else None


def help_text() -> str:
    return HELP_TEXT
