from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from models import Message, Sender
from utils.chat_text import chat2text, chat_lines, strip_mentions


def _message(
    message_id: str, sender: str, text: str, push_name: str | None = None
) -> Message:
    message = Message(
        message_id=message_id,
        chat_jid="123@g.us",
        sender_jid=f"{sender}@s.whatsapp.net",
        text=text,
        timestamp=datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
    )
    if push_name:
        message.sender = Sender(jid=f"{sender}@s.whatsapp.net", push_name=push_name)
    return message


def test_chat2text_uses_display_name():
    result = chat2text([_message("1", "123456789", "Hello", push_name="John Doe")])
    assert result == "2023-01-01 12:00 John Doe: Hello"


def test_chat2text_unknown_sender_gets_label_not_full_number():
    result = chat2text([_message("1", "123456789", "Hello")])
    assert result == "2023-01-01 12:00 משתתף ...6789: Hello"


def test_chat2text_uses_fallback_names_for_unnamed_senders():
    result = chat2text(
        [_message("1", "123456789", "Hello")], fallback_names={"123456789": "Me"}
    )
    assert result == "2023-01-01 12:00 Me: Hello"


def test_chat2text_converts_timezone():
    result = chat2text(
        [_message("1", "123456789", "Hello", push_name="John")],
        ZoneInfo("Asia/Jerusalem"),
    )
    assert result == "2023-01-01 14:00 John: Hello"


def test_chat_lines_replace_known_mentions_with_names():
    lines = chat_lines(
        [
            _message("1", "111", "hi", push_name="Dana"),
            _message("2", "222", "@111 thanks! cc @999", push_name="Avi"),
        ]
    )
    assert lines[1] == "2023-01-01 12:00 Avi: Dana thanks! cc 999"


def test_strip_mentions():
    assert strip_mentions("שלום @972501234567 ו-@123") == "שלום 972501234567 ו-123"
    assert strip_mentions("email me at a@b.com") == "email me at a@b.com"
