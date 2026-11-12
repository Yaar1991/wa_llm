import asyncio
import logging
import re
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
from utils.chat_text import chat_lines, strip_mentions
from whatsapp import WhatsAppClient

logger = logging.getLogger(__name__)

SummaryStatus = Literal["sent", "skipped", "failed"]

_NOTES_SEPARATOR = "\n\n---\n\n"
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


async def _generate(
    model: Model, settings: Settings, template: str, content: str, **context
) -> str:
    """
    Generate text, regenerating when the model drifts into an alphabet the chat
    doesn't use (e.g. Chinese mid-sentence). As a last resort those characters
    are removed.
    """
    system_prompt = prompt_manager.render(template, **context)
    output = ""
    drifted: set[str] = set()
    for attempt in range(1, _SCRIPT_ATTEMPTS + 1):
        output = await _run_agent(model, settings, system_prompt, content)
        drifted = introduced_scripts(output, content)
        if not drifted:
            return output
        logger.warning(
            "Output used unexpected alphabet(s) %s (attempt %d/%d)",
            sorted(drifted),
            attempt,
            _SCRIPT_ATTEMPTS,
        )
    return strip_scripts(output, drifted).strip()


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
    max_chars = settings.summary_chunk_max_chars
    lines = chat_lines(messages, ZoneInfo(settings.summary_timezone), fallback_names)
    chunks = pack_chunks(lines, max_chars)

    if len(chunks) == 1:
        return await _generate(
            model, settings, "quick_summary.j2", chunks[0], group_name=group_name
        )

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
        )
        for i, chunk in enumerate(chunks, start=1)
    ]

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

    return await _generate(
        model, settings, "merge_summaries.j2", content, group_name=group_name
    )


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
        summary = await summarize(model, settings, group_name, messages, fallback_names)
    except Exception:
        logger.exception("Error summarizing group %s", group_name)
        return "failed"

    if not summary:
        logger.warning("Model returned an empty summary for group %s", group_name)
        return "failed"

    try:
        # Stores the sent summary flagged as sent_by_bot (inside a savepoint).
        await BaseHandler(session, whatsapp).send_message(group.group_jid, summary)
    except Exception:
        logger.exception("Error sending summary to group %s", group_name)
        return "failed"

    # Messages that arrived while the summary was generated are kept for next time.
    group.last_summary_sync = run_started
    session.add(group)
    await session.commit()
    logger.info("Sent summary of %d messages to %s", len(messages), group_name)
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
