from pathlib import Path

from utils.participant_notes import (
    MAX_NOTE_CHARS,
    combined_notes,
    load_notes,
    merge_learned,
    parse_notes,
    save_learned,
)


def test_parse_notes_skips_comments_and_empty_notes():
    text = "# comment: ignored\nדנה: צינית\n- **יוסי**: רציני\nניר:\nno colon here\n"

    assert parse_notes(text) == {"דנה": "צינית", "יוסי": "רציני"}


def test_merge_learned_keeps_missing_people_and_cuts_long_notes():
    merged = merge_learned({"דנה": "צינית", "יוסי": "רציני"}, "דנה: " + "א" * 500)

    assert merged["יוסי"] == "רציני"
    assert len(merged["דנה"]) == MAX_NOTE_CHARS


def test_merge_learned_ignores_unusable_reply():
    assert merge_learned({"דנה": "צינית"}, "אין לי מה להוסיף") == {"דנה": "צינית"}


def test_save_and_load_learned_round_trip(tmp_path: Path):
    path = str(tmp_path / "data" / "learned.txt")

    save_learned(path, {"יוסי": "רציני", "דנה": "צינית"})

    assert Path(path).read_text(encoding="utf-8").startswith("#")
    assert load_notes(path) == {"דנה": "צינית", "יוסי": "רציני"}
    assert load_notes("") == {} and load_notes(str(tmp_path / "missing.txt")) == {}


def test_combined_notes_prefers_the_owner():
    assert combined_notes({"דנה": "שלי"}, {"דנה": "נלמד", "יוסי": "רציני"}) == (
        "דנה: שלי\nיוסי: רציני"
    )
