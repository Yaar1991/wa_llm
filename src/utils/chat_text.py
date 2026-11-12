import re
from collections import Counter
from datetime import tzinfo
from typing import List, Mapping

from models import Message, Reaction
from whatsapp.jid import parse_jid

# "@" followed by digits: a WhatsApp mention of a phone number / LID.
_MENTION_RE = re.compile(r"@(\d+)")


# If no reactions, don't display anything.
# Group Reactions by count per reaction. E.g: "👍 5, 👎 2"
# If all reactions has only one count, don't need to display number. E.G: "👍, 👎, 😁"
def render_reactions(reactions: List[Reaction]) -> str:
    if not reactions:
        return ""

    counts = Counter(r.emoji for r in reactions)
    if all(count == 1 for count in counts.values()):
        return f"Reactions: {', '.join(counts.keys())}"

    return (
        f"Reactions: {', '.join(f'{emoji} {count}' for emoji, count in counts.items())}"
    )


def sender_display_name(
    message: Message, fallback_names: Mapping[str, str] | None = None
) -> str:
    """
    The sender's WhatsApp display name.

    Falls back to `fallback_names` (keyed by JID user part), then to a label
    with the last digits of the number, so a full phone number is never shown.
    """
    if message.sender and message.sender.push_name:
        return message.sender.push_name
    user = parse_jid(message.sender_jid).user
    if fallback_names and fallback_names.get(user):
        return fallback_names[user]
    return f"משתתף ...{user[-4:]}"


def strip_mentions(text: str) -> str:
    """Remove "@" before numbers so WhatsApp does not turn them into tags."""
    return _MENTION_RE.sub(r"\1", text)


def chat_lines(
    history: List[Message],
    tz: tzinfo | None = None,
    fallback_names: Mapping[str, str] | None = None,
) -> List[str]:
    """
    Render messages as transcript lines: "<time> <name>: <text>".

    People are referred to by display name rather than "@number", and
    "@number" mentions inside the text are replaced with the name when known.
    """
    names = {
        parse_jid(m.sender_jid).user: sender_display_name(m, fallback_names)
        for m in history
    }

    def replace_mention(match: re.Match[str]) -> str:
        return names.get(match.group(1), match.group(1))

    lines = []
    for message in history:
        timestamp = message.timestamp.astimezone(tz) if tz else message.timestamp
        text = _MENTION_RE.sub(replace_mention, message.text or "")
        name = sender_display_name(message, fallback_names)
        line = f"{timestamp:%Y-%m-%d %H:%M} {name}: {text}"

        reaction_text = render_reactions(message.reactions)
        if reaction_text:
            line = f"{line} ({reaction_text})"
        lines.append(line)

    return lines


def chat2text(
    history: List[Message],
    tz: tzinfo | None = None,
    fallback_names: Mapping[str, str] | None = None,
) -> str:
    return "\n".join(chat_lines(history, tz, fallback_names))
