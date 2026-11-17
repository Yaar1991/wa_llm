"""
Background notes about the people in a group, one "<name>: <note>" line each.

Two files are read: one the owner writes by hand, and one the model keeps up
to date. Lines starting with "#" and names without a note are ignored.
"""

import logging
import re
from pathlib import Path
from typing import Mapping

logger = logging.getLogger(__name__)

_NOTE_LINE_RE = re.compile(r"^\s*[-•*]?\s*([^:#\n]{1,60}?)\s*:\s*(\S.*?)\s*$")
_LEARNED_HEADER = (
    "# Written by the bot after each summary it sends. One line per person.\n"
    "# You can edit or delete lines. Your own notes go in participants.txt and\n"
    "# always win over these.\n"
)
# Keeps the notes a small part of every prompt.
MAX_NOTE_CHARS = 300
MAX_LEARNED_PEOPLE = 60


def parse_notes(text: str) -> dict[str, str]:
    """Read "<name>: <note>" lines, skipping comments and empty notes."""
    notes: dict[str, str] = {}
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        match = _NOTE_LINE_RE.match(line)
        if match:
            name = match.group(1).strip("*\"' ")
            if name:
                notes[name] = match.group(2)
    return notes


def format_notes(notes: Mapping[str, str]) -> str:
    return "\n".join(f"{name}: {note}" for name, note in notes.items())


def load_notes(path: str) -> dict[str, str]:
    """Read a notes file. A missing or unreadable file counts as empty."""
    file = Path(path)
    if not path or not file.is_file():
        return {}
    try:
        return parse_notes(file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as e:
        logger.warning("Could not read participant notes file %s: %s", path, e)
        return {}


def merge_learned(known: Mapping[str, str], reply: str) -> dict[str, str]:
    """
    Apply the model's updated notes on top of the known ones.

    People the model left out keep their note, over-long notes are cut, and a
    reply without any usable line changes nothing.
    """
    merged = dict(known)
    for name, note in parse_notes(reply).items():
        if name in merged or len(merged) < MAX_LEARNED_PEOPLE:
            merged[name] = note[:MAX_NOTE_CHARS]
    return merged


def save_learned(path: str, notes: Mapping[str, str]) -> None:
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    ordered = dict(sorted(notes.items(), key=lambda item: item[0].casefold()))
    file.write_text(_LEARNED_HEADER + format_notes(ordered) + "\n", encoding="utf-8")


def combined_notes(owner: Mapping[str, str], learned: Mapping[str, str]) -> str:
    """Notes for the prompt: the owner's note replaces a learned one."""
    return format_notes({**learned, **owner})
