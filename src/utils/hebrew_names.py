"""
Hebrew spellings for people's display names.

The dictionary is a JSON file mapping a WhatsApp display name to how it is
written in summaries, e.g. {"Dana Levi": "דנה לוי"}. Names the bot hasn't seen
yet are added automatically; edit the file to correct a spelling.
"""

import json
import logging
import re
from pathlib import Path
from typing import Mapping

logger = logging.getLogger(__name__)

_HEBREW_LETTER_RE = re.compile(r"[א-ת]")
_NUMBERED_LINE_RE = re.compile(r"^\s*(\d+)\s*[.):\-]\s*(.+?)\s*$")


def _foreign_letters(text: str) -> bool:
    return any(ch.isalpha() and not _HEBREW_LETTER_RE.match(ch) for ch in text)


def needs_hebrew(name: str) -> bool:
    """True if the name has letters from an alphabet other than Hebrew."""
    return _foreign_letters(name)


def is_hebrew_name(text: str) -> bool:
    """True if the text is usable as a Hebrew spelling of a name."""
    return bool(_HEBREW_LETTER_RE.search(text)) and not _foreign_letters(text)


def load_names(path: str) -> dict[str, str]:
    """Read the dictionary. A missing or unreadable file counts as empty."""
    file = Path(path)
    if not path or not file.is_file():
        return {}
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("Could not read Hebrew names file %s: %s", path, e)
        return {}
    if not isinstance(data, dict):
        logger.warning("Hebrew names file %s must hold a JSON object", path)
        return {}
    return {
        str(name).strip(): str(hebrew).strip()
        for name, hebrew in data.items()
        if str(hebrew).strip()
    }


def save_names(path: str, names: Mapping[str, str]) -> None:
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    ordered = dict(sorted(names.items(), key=lambda item: item[0].casefold()))
    file.write_text(
        json.dumps(ordered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def numbered_list(names: list[str]) -> str:
    return "\n".join(f"{i}. {name}" for i, name in enumerate(names, start=1))


def parse_numbered_names(names: list[str], reply: str) -> dict[str, str]:
    """
    Match a model reply of "<number>. <Hebrew spelling>" lines back to `names`.
    Lines that aren't a clean Hebrew spelling are ignored.
    """
    parsed: dict[str, str] = {}
    for line in reply.splitlines():
        match = _NUMBERED_LINE_RE.match(line)
        if not match:
            continue
        index = int(match.group(1)) - 1
        hebrew = match.group(2).strip("\"'* ")
        if 0 <= index < len(names) and is_hebrew_name(hebrew):
            parsed.setdefault(names[index], hebrew)
    return parsed
