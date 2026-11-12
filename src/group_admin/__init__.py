"""
Command line tool to choose which WhatsApp groups get scheduled summaries.

    python app/cli.py groups list
    python app/cli.py groups enable <number | name | JID> [--yes]
    python app/cli.py groups disable <number | name | JID> [--yes]
    python app/cli.py summarize-now [--min-messages N]
    python app/cli.py preview <number | name | JID> [--last N]
"""

import argparse
import asyncio
import logging
import sys
from typing import Callable, Sequence

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from config import Settings, get_settings
from models import Group
from sqlmodel import col, select

from llm import build_model, unload_model
from models import Message
from summarize_and_send_to_groups import (
    own_name_fallback,
    summarize,
    summarize_and_send_to_groups,
    utc_now_naive,
)
from whatsapp import WhatsAppClient
from whatsapp.init_groups import gather_groups


class GroupSelectionError(ValueError):
    pass


def sort_groups(groups: Sequence[Group]) -> list[Group]:
    """Stable order used for the numbers shown by `groups list`."""
    return sorted(groups, key=lambda g: ((g.group_name or "").casefold(), g.group_jid))


def list_number(groups: Sequence[Group], ref: str) -> int | None:
    """The `groups list` number `ref` refers to, or None if it isn't one."""
    ref = ref.strip()
    if ref.isdigit() and 1 <= int(ref) <= len(groups):
        return int(ref)
    return None


def resolve_group(groups: Sequence[Group], ref: str) -> Group:
    """
    Find a group by its number in `groups list`, its JID, or its name.
    Names match exactly (case-insensitive) first, then as a unique substring.
    """
    number = list_number(groups, ref)
    if number is not None:
        return groups[number - 1]
    ref = ref.strip()

    for group in groups:
        if group.group_jid == ref:
            return group

    needle = ref.casefold()
    exact = [g for g in groups if (g.group_name or "").casefold() == needle]
    if len(exact) == 1:
        return exact[0]
    matches = exact or [g for g in groups if needle in (g.group_name or "").casefold()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise GroupSelectionError(
            f"No group matches '{ref}'. Run 'groups list' to see your groups."
        )
    names = ", ".join(f"'{g.group_name}'" for g in matches)
    raise GroupSelectionError(
        f"'{ref}' matches several groups ({names}). "
        "Use the number or JID from 'groups list'."
    )


def format_group_table(groups: Sequence[Group]) -> str:
    lines = ["  #  On   Name (JID)"]
    for i, group in enumerate(groups, start=1):
        mark = "[x]" if group.managed else "[ ]"
        name = group.group_name or "(no name)"
        lines.append(f"{i:>3}  {mark}  {name}  ({group.group_jid})")
    return "\n".join(lines)


def describe_schedule(settings: Settings) -> str:
    if not settings.summary_cron.strip():
        return "Scheduled summaries are disabled (SUMMARY_CRON is empty)."
    return (
        f"Enabled groups are summarized on '{settings.summary_cron}' "
        f"({settings.summary_timezone}) when they have at least "
        f"{settings.summary_min_messages} new messages."
    )


def ask_yes_no(prompt: str) -> bool:
    """Ask on the terminal. No answer (e.g. no terminal attached) counts as no."""
    try:
        answer = input(prompt)
    except EOFError:
        return False
    return answer.strip().casefold() in {"y", "yes"}


async def _current_groups(session: AsyncSession, whatsapp: WhatsAppClient):
    groups = sort_groups(await gather_groups(session, whatsapp))
    await session.commit()
    return groups


async def cmd_list(
    session: AsyncSession, whatsapp: WhatsAppClient, settings: Settings
) -> int:
    groups = await _current_groups(session, whatsapp)
    if not groups:
        print(
            "No groups found. Is your WhatsApp linked? "
            "Open the WhatsApp panel (http://localhost:47321 by default) to scan the QR code."
        )
        return 1
    print(format_group_table(groups))
    print()
    print(describe_schedule(settings))
    return 0


async def cmd_set_enabled(
    session: AsyncSession,
    whatsapp: WhatsAppClient,
    settings: Settings,
    ref: str,
    enabled: bool,
    *,
    assume_yes: bool = False,
    confirm: Callable[[str], bool] = ask_yes_no,
) -> int:
    groups = await _current_groups(session, whatsapp)
    try:
        group = resolve_group(groups, ref)
    except GroupSelectionError as e:
        print(e, file=sys.stderr)
        return 2

    name = group.group_name or group.group_jid
    if group.managed == enabled:
        print(f"'{name}' is already {'enabled' if enabled else 'disabled'}.")
        return 0

    # List numbers shift when groups are added or renamed, so an old number
    # could point at a different group. Show which one before changing it.
    if list_number(groups, ref) is not None and not assume_yes:
        if enabled:
            effect = "Its messages will be stored and summaries posted to it."
        else:
            effect = "Its messages will no longer be stored or summarized."
        question = (
            f"#{ref.strip()} is '{name}' ({group.group_jid}). {effect}\n"
            f"{'Enable' if enabled else 'Disable'} it? [y/N] "
        )
        if not confirm(question):
            print("Nothing changed. (Pass --yes to skip this question.)")
            return 1

    group.managed = enabled
    if enabled:
        # Start fresh: only messages from now on are summarized.
        group.last_summary_sync = utc_now_naive()
    session.add(group)
    await session.commit()

    if enabled:
        print(f"Enabled '{name}'. Its messages are stored from now on.")
        print(describe_schedule(settings))
    else:
        print(
            f"Disabled '{name}'. New messages are no longer stored or summarized "
            "(messages already stored stay in the database)."
        )
    return 0


async def cmd_summarize_now(
    session: AsyncSession,
    whatsapp: WhatsAppClient,
    settings: Settings,
    min_messages: int | None,
) -> int:
    results = await summarize_and_send_to_groups(
        settings, session, whatsapp, min_messages=min_messages
    )
    if not results:
        print("No enabled groups. Use 'groups enable' first.")
        return 1
    for name, status in results.items():
        print(f"{name}: {status}")
    return 1 if "failed" in results.values() else 0


async def cmd_preview(
    session: AsyncSession,
    whatsapp: WhatsAppClient,
    settings: Settings,
    ref: str,
    last: int,
) -> int:
    """Summarize a group's last N stored messages and print it (nothing is sent)."""
    groups = await _current_groups(session, whatsapp)
    try:
        group = resolve_group(groups, ref)
    except GroupSelectionError as e:
        print(e, file=sys.stderr)
        return 2

    resp = await session.exec(
        select(Message)
        .where(Message.group_jid == group.group_jid)
        .where(col(Message.sent_by_bot).is_(False))
        .order_by(col(Message.timestamp).desc())
        .limit(last)
    )
    messages = list(reversed(resp.all()))
    name = group.group_name or group.group_jid
    if not messages:
        print(f"No stored messages for '{name}' (is it enabled?).")
        return 1

    print(f"Summarizing the last {len(messages)} messages of '{name}'...\n")
    try:
        summary = await summarize(
            build_model(settings),
            settings,
            name,
            messages,
            await own_name_fallback(whatsapp),
        )
    finally:
        if settings.unload_model_after_run:
            await unload_model(settings)
    print(summary)
    print("\n(Preview only: nothing was sent to the group.)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="Choose which WhatsApp groups get scheduled summaries.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    groups = commands.add_parser(
        "groups", help="List your WhatsApp groups and enable/disable summaries"
    )
    actions = groups.add_subparsers(dest="action", required=True)
    actions.add_parser("list", help="Show your WhatsApp groups and which are enabled")
    for action, verb in (("enable", "Start"), ("disable", "Stop")):
        sub = actions.add_parser(
            action, help=f"{verb} storing and summarizing a group's messages"
        )
        sub.add_argument("group", help="Number from 'groups list', group name, or JID")
        sub.add_argument(
            "-y",
            "--yes",
            action="store_true",
            help="Don't ask for confirmation when the group is given by number",
        )

    summarize = commands.add_parser(
        "summarize-now", help="Run the summary job once now for all enabled groups"
    )
    summarize.add_argument(
        "--min-messages",
        type=int,
        default=None,
        help="Override SUMMARY_MIN_MESSAGES for this run",
    )
    preview = commands.add_parser(
        "preview",
        help="Print a summary of a group's last N stored messages (nothing is sent)",
    )
    preview.add_argument("group", help="Number from 'groups list', group name, or JID")
    preview.add_argument(
        "--last", type=int, default=50, help="How many recent messages (default 50)"
    )
    return parser


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    engine = create_async_engine(settings.db_uri)
    session_factory = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )
    whatsapp = WhatsAppClient(
        settings.whatsapp_host,
        settings.whatsapp_basic_auth_user,
        settings.whatsapp_basic_auth_password,
    )
    try:
        async with session_factory() as session:
            if args.command == "preview":
                return await cmd_preview(
                    session, whatsapp, settings, args.group, args.last
                )
            if args.command == "summarize-now":
                return await cmd_summarize_now(
                    session, whatsapp, settings, args.min_messages
                )
            if args.action == "list":
                return await cmd_list(session, whatsapp, settings)
            return await cmd_set_enabled(
                session,
                whatsapp,
                settings,
                args.group,
                args.action == "enable",
                assume_yes=args.yes,
            )
    finally:
        await whatsapp.close()
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO if args.command == "summarize-now" else logging.WARNING,
    )
    try:
        return asyncio.run(run(args))
    except httpx.HTTPError as e:
        settings = get_settings()
        print(
            f"Could not reach the WhatsApp service at {settings.whatsapp_host}: {e}\n"
            "Is it running (docker compose up -d) and is WHATSAPP_HOST correct?",
            file=sys.stderr,
        )
        return 1
    except OSError as e:
        print(
            f"Could not connect to the database or a service: {e}\n"
            "Is the stack running (docker compose up -d) and is DB_URI correct?",
            file=sys.stderr,
        )
        return 1
