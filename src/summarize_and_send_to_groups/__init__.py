import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Mapping
from zoneinfo import ZoneInfo

from pydantic_ai import Agent
from pydantic_ai.models import Model
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession
from tenacity import (
    retry,
    wait_random_exponential,
    stop_after_attempt,
    before_sleep_log,
)

from config import Settings
from handler.base_handler import BaseHandler
from llm import build_model, build_model_settings, unload_model
from models import Group, Message
from services.prompt_manager import prompt_manager
from utils.chat_text import chat_lines, sender_display_name, strip_mentions
from utils.hebrew_names import (
    load_names,
    needs_hebrew,
    numbered_list,
    parse_numbered_names,
    save_names,
)
from utils.participant_notes import (
    combined_notes,
    format_notes,
    load_notes,
    merge_learned,
    save_learned,
)
from whatsapp import WhatsAppClient

logger = logging.getLogger(__name__)

SummaryStatus = Literal["sent", "skipped", "failed"]
# What the brief recap is written from.
BriefSource = Literal["transcript", "notes", "summary"]


@dataclass
class Summaries:
    full: str
    # Empty when the recap could not be written.
    brief: str
    # The material the recap was written from (also used to learn about people).
    source: str
    source_kind: BriefSource


_NOTES_SEPARATOR = "\n\n---\n\n"
# Shown above the short recap that is sent before the full summary.
_BRIEF_HEADER = "*בקצרה:*"
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
# Alphabets small models sometimes drift into mid-sentence. An output may only
# use one of these if the chat itself contains it.
_FOREIGN_SCRIPTS = {
    "arabic": re.compile(
        r"[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff\ufb50-\ufdff\ufe70-\ufeff]"
    ),
    "cyrillic": re.compile(r"[\u0400-\u04ff]"),
    "cjk": re.compile(
        r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff]"
    ),
    "devanagari": re.compile(r"[\u0900-\u097f]"),
    "thai": re.compile(r"[\u0e00-\u0e7f]"),
}
# Extra generations allowed when an output drifts into another alphabet.
_SCRIPT_ATTEMPTS = 3
# Summaries are Hebrew only: no English letters, even where the chat uses them.
_LATIN_RE = re.compile(r"[A-Za-z\u00c0-\u024f]")
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_LATIN_WORD_RE = re.compile(
    r"[^\s\u0590-\u05ff]*[A-Za-z\u00c0-\u024f][^\s\u0590-\u05ff]*"
)
# Only one summary run at a time per process (scheduler and API share it).
_run_lock = asyncio.Lock()


def utc_now_naive() -> datetime:
    """Current UTC time without tzinfo, matching the naive `group` timestamp columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def as_utc(value: datetime) -> datetime:
    """Treat naive datetimes as UTC."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def pack_chunks(items: list[str], max_chars: int, separator: str = "\n") -> list[str]:
    """
    Greedily join consecutive items into chunks of at most `max_chars`.
    A single item longer than `max_chars` is truncated.
    """
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for item in items:
        item = item[:max_chars]
        added = len(item) + (len(separator) if current else 0)
        if current and size + added > max_chars:
            chunks.append(separator.join(current))
            current, size, added = [], 0, len(item)
        current.append(item)
        size += added
    if current:
        chunks.append(separator.join(current))
    return chunks


def clean_output(text: str) -> str:
    """Drop leaked reasoning blocks and anything that would tag people."""
    return strip_mentions(_THINK_RE.sub("", text)).strip()


def introduced_scripts(output: str, source: str) -> set[str]:
    """Foreign alphabets used in `output` that don't appear in `source`."""
    return {
        name
        for name, pattern in _FOREIGN_SCRIPTS.items()
        if pattern.search(output) and not pattern.search(source)
    }


def strip_scripts(text: str, scripts: set[str]) -> str:
    for name in scripts:
        text = _FOREIGN_SCRIPTS[name].sub("", text)
    return re.sub(r"[ \t]{2,}", " ", text)


@retry(
    wait=wait_random_exponential(min=1, max=30),
    stop=stop_after_attempt(3),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
async def _run_agent(
    model: Model, settings: Settings, system_prompt: str, content: str
) -> str:
    agent = Agent(
        model=model,
        system_prompt=system_prompt,
        output_type=str,
        model_settings=build_model_settings(settings),
    )
    result = await agent.run(content)
    return clean_output(result.output)


def has_latin(text: str) -> bool:
    return bool(_LATIN_RE.search(text))


def strip_latin(text: str) -> str:
    """Last resort: remove links and any words still in English letters."""
    text = _LATIN_WORD_RE.sub("", _URL_RE.sub("", text))
    text = re.sub(r"[ \t]{2,}", " ", text)
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()


async def _generate(
    model: Model,
    settings: Settings,
    template: str,
    content: str,
    *,
    hebrew_only: bool = False,
    **context,
) -> str:
    """
    Generate text, regenerating when the model drifts into an alphabet the chat
    doesn't use (e.g. Chinese mid-sentence). As a last resort those characters
    are removed.

    With `hebrew_only`, English letters left in the output (names, brands,
    links) are rewritten into Hebrew by one extra short request, and whatever
    is still left is removed.
    """
    system_prompt = prompt_manager.render(template, **context)
    output = ""
    drifted: set[str] = set()
    for attempt in range(1, _SCRIPT_ATTEMPTS + 1):
        output = await _run_agent(model, settings, system_prompt, content)
        drifted = introduced_scripts(output, content)
        if not drifted:
            break
        logger.warning(
            "Output used unexpected alphabet(s) %s (attempt %d/%d)",
            sorted(drifted),
            attempt,
            _SCRIPT_ATTEMPTS,
        )
    else:
        output = strip_scripts(output, drifted).strip()

    if hebrew_only and has_latin(output):
        output = await _to_hebrew_only(model, settings, output)
    return output


async def _to_hebrew_only(model: Model, settings: Settings, text: str) -> str:
    try:
        fixed = await _generate(model, settings, "hebrew_only_fix.j2", text)
    except Exception:
        logger.exception("Could not rewrite English words into Hebrew")
        fixed = ""
    # A rewrite that lost most of the text is worse than a few removed words.
    if not fixed or len(fixed) < len(text) // 2:
        fixed = text
    if has_latin(fixed):
        logger.warning("Removing English words the model left in a summary")
        fixed = strip_latin(fixed)
    return fixed


async def resolve_hebrew_names(
    model: Model,
    settings: Settings,
    messages: list[Message],
    fallback_names: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """
    Hebrew spellings for the senders' display names, from `hebrew_names_file`.

    Names that aren't in the file yet (and aren't already in Hebrew) get a
    spelling from the model, which is saved to the file so it stays the same
    in later summaries and can be corrected by hand.
    """
    path = settings.hebrew_names_file.strip()
    if not path:
        return {}

    known = load_names(path)
    senders = {sender_display_name(m, fallback_names).strip() for m in messages}
    missing = sorted(n for n in senders if needs_hebrew(n) and n not in known)
    if not missing:
        return known

    try:
        reply = await _generate(
            model, settings, "hebrew_names.j2", numbered_list(missing)
        )
    except Exception:
        logger.exception("Could not get Hebrew spellings for new names")
        return known

    added = parse_numbered_names(missing, reply)
    if len(added) < len(missing):
        logger.warning(
            "No Hebrew spelling for %d new name(s)", len(missing) - len(added)
        )
    if added:
        known.update(added)
        try:
            save_names(path, known)
            logger.info("Added %d name(s) to %s", len(added), path)
        except OSError as e:
            logger.warning("Could not save Hebrew names to %s: %s", path, e)
    return known


def load_participant_notes(settings: Settings) -> tuple[dict[str, str], dict[str, str]]:
    """Background notes about the group's people: (owner-written, model-learned)."""
    return (
        load_notes(settings.participant_notes_file.strip()),
        load_notes(settings.learned_notes_file.strip()),
    )


async def summarize_brief(
    model: Model,
    settings: Settings,
    group_name: str,
    source: str,
    source_kind: BriefSource = "summary",
    participant_notes: str = "",
) -> str:
    """
    Write one very brief Hebrew paragraph about the whole period.

    It is written from the transcript itself when that fits in one request
    (otherwise from the notes about its parts), so the model can see how much
    of the chat each discussion really took up.
    """
    return await _generate(
        model,
        settings,
        "brief_summary.j2",
        source,
        hebrew_only=True,
        group_name=group_name,
        source=source_kind,
        participant_notes=participant_notes,
    )


async def _summarize_full(
    model: Model,
    settings: Settings,
    group_name: str,
    messages: list[Message],
    fallback_names: Mapping[str, str] | None,
    participant_notes: str,
) -> tuple[str, str, BriefSource]:
    """:return: the full summary, and the material to write the brief recap from."""
    max_chars = settings.summary_chunk_max_chars
    hebrew_names = await resolve_hebrew_names(model, settings, messages, fallback_names)
    lines = chat_lines(
        messages, ZoneInfo(settings.summary_timezone), fallback_names, hebrew_names
    )
    chunks = pack_chunks(lines, max_chars)

    if len(chunks) == 1:
        full = await _generate(
            model,
            settings,
            "quick_summary.j2",
            chunks[0],
            hebrew_only=True,
            group_name=group_name,
            participant_notes=participant_notes,
        )
        return full, chunks[0], "transcript"

    logger.info("Summarizing %s in %d parts", group_name, len(chunks))
    notes = [
        await _generate(
            model,
            settings,
            "partial_summary.j2",
            chunk,
            group_name=group_name,
            part=i,
            total=len(chunks),
            participant_notes=participant_notes,
        )
        for i, chunk in enumerate(chunks, start=1)
    ]
    # Notes on every part still show each topic's size; a merged summary doesn't.
    all_notes = _NOTES_SEPARATOR.join(notes)
    notes_fit = len(all_notes) <= max_chars

    # Merge the notes, in rounds if they don't fit in one request.
    while True:
        batches = pack_chunks(notes, max_chars, _NOTES_SEPARATOR)
        if len(batches) == 1:
            content = batches[0]
            break
        if len(batches) == len(notes):
            logger.warning("Partial notes too long to merge fully; truncating")
            content = _NOTES_SEPARATOR.join(notes)[:max_chars]
            break
        notes = [
            await _generate(
                model, settings, "merge_summaries.j2", batch, group_name=group_name
            )
            for batch in batches
        ]

    full = await _generate(
        model,
        settings,
        "merge_summaries.j2",
        content,
        hebrew_only=True,
        group_name=group_name,
    )
    return (full, all_notes, "notes") if notes_fit else (full, full, "summary")


async def summarize(
    model: Model,
    settings: Settings,
    group_name: str,
    messages: list[Message],
    fallback_names: Mapping[str, str] | None = None,
) -> str:
    """
    Summarize messages (chronological order) in Hebrew.

    Transcripts larger than `summary_chunk_max_chars` are summarized in parts,
    and the partial notes are then merged into one summary.
    :param fallback_names: display names (by JID user part) for senders whose
        name isn't stored, e.g. the account owner.
    """
    notes = combined_notes(*load_participant_notes(settings))
    full, _, _ = await _summarize_full(
        model, settings, group_name, messages, fallback_names, notes
    )
    return full


async def summarize_with_brief(
    model: Model,
    settings: Settings,
    group_name: str,
    messages: list[Message],
    fallback_names: Mapping[str, str] | None = None,
) -> Summaries:
    """
    The full summary plus a very brief recap of the same messages.

    The recap is an extra: if it can't be written, `brief` is empty.
    """
    notes = combined_notes(*load_participant_notes(settings))
    full, source, source_kind = await _summarize_full(
        model, settings, group_name, messages, fallback_names, notes
    )
    brief = ""
    if full:
        try:
            brief = await summarize_brief(
                model, settings, group_name, source, source_kind, notes
            )
        except Exception:
            logger.exception("Error writing the brief recap for group %s", group_name)
    return Summaries(full=full, brief=brief, source=source, source_kind=source_kind)


async def learn_about_participants(
    model: Model,
    settings: Settings,
    group_name: str,
    source: str,
    source_kind: BriefSource,
) -> None:
    """
    Let the model update its notes about how each person writes, from the chat
    it just summarized. Best effort: a failure leaves the notes as they were.
    """
    path = settings.learned_notes_file.strip()
    if not path:
        return
    owner, learned = load_participant_notes(settings)
    try:
        reply = await _generate(
            model,
            settings,
            "learn_participants.j2",
            source,
            group_name=group_name,
            source=source_kind,
            owner_notes=format_notes(owner),
            learned_notes=format_notes(learned),
        )
        updated = merge_learned(learned, reply)
        if updated != learned:
            save_learned(path, updated)
            logger.info("Updated notes about %d participant(s)", len(updated))
    except Exception:
        logger.exception("Could not update participant notes for %s", group_name)


async def summarize_and_send_to_group(
    settings: Settings,
    session: AsyncSession,
    whatsapp: WhatsAppClient,
    group: Group,
    model: Model,
    min_messages: int,
    fallback_names: Mapping[str, str] | None = None,
) -> SummaryStatus:
    run_started = utc_now_naive()
    group_name = group.group_name or group.group_jid

    resp = await session.exec(
        select(Message)
        .where(Message.group_jid == group.group_jid)
        .where(Message.timestamp >= as_utc(group.last_summary_sync))
        # Messages you type yourself count; messages the bot sent don't.
        .where(col(Message.sent_by_bot).is_(False))
        .order_by(col(Message.timestamp).asc())
    )
    messages = list(resp.all())

    if len(messages) < min_messages:
        logger.info(
            "Not enough messages to summarize in group %s (%d < %d)",
            group_name,
            len(messages),
            min_messages,
        )
        return "skipped"

    try:
        summaries = await summarize_with_brief(
            model, settings, group_name, messages, fallback_names
        )
    except Exception:
        logger.exception("Error summarizing group %s", group_name)
        return "failed"

    summary, brief = summaries.full, summaries.brief
    if not summary:
        logger.warning("Model returned an empty summary for group %s", group_name)
        return "failed"
    if not brief:
        logger.warning("Sending only the full summary to group %s", group_name)

    try:
        # Stores what was sent flagged as sent_by_bot (inside a savepoint).
        handler = BaseHandler(session, whatsapp)
        if brief:
            await handler.send_message(group.group_jid, f"{_BRIEF_HEADER}\n{brief}")
        await handler.send_message(group.group_jid, summary)
    except Exception:
        logger.exception("Error sending summary to group %s", group_name)
        return "failed"

    # Messages that arrived while the summary was generated are kept for next time.
    group.last_summary_sync = run_started
    session.add(group)
    await session.commit()
    logger.info("Sent summary of %d messages to %s", len(messages), group_name)

    await learn_about_participants(
        model, settings, group_name, summaries.source, summaries.source_kind
    )
    return "sent"


async def own_name_fallback(whatsapp: WhatsAppClient) -> dict[str, str]:
    """Map the account owner's number to their display name (best effort)."""
    try:
        my_jid = await whatsapp.get_my_jid()
        my_name = await whatsapp.get_my_name()
    except Exception as e:
        logger.warning("Could not look up own display name: %s", e)
        return {}
    return {my_jid.user: my_name} if my_name else {}


async def summarize_and_send_to_groups(
    settings: Settings,
    session: AsyncSession,
    whatsapp: WhatsAppClient,
    model: Model | None = None,
    min_messages: int | None = None,
) -> dict[str, SummaryStatus]:
    """
    Summarize every enabled group and post each summary to its group.

    Groups are processed one at a time (a local model serves one request at a time).
    :return: status per group name
    """
    if min_messages is None:
        min_messages = settings.summary_min_messages

    async with _run_lock:
        groups = await session.exec(select(Group).where(col(Group.managed).is_(True)))
        enabled_groups = list(groups.all())
        if not enabled_groups:
            logger.info("No enabled groups to summarize")
            return {}

        llm = model or build_model(settings)
        fallback_names = await own_name_fallback(whatsapp)
        results: dict[str, SummaryStatus] = {}
        try:
            for group in enabled_groups:
                results[
                    group.group_name or group.group_jid
                ] = await summarize_and_send_to_group(
                    settings,
                    session,
                    whatsapp,
                    group,
                    llm,
                    min_messages,
                    fallback_names,
                )
        finally:
            used_model = any(status != "skipped" for status in results.values())
            if model is None and used_model and settings.unload_model_after_run:
                await unload_model(settings)

        return results
