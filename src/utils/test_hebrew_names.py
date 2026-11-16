from pathlib import Path

from utils.hebrew_names import (
    is_hebrew_name,
    load_names,
    needs_hebrew,
    numbered_list,
    parse_numbered_names,
    save_names,
)


def test_needs_hebrew_only_for_names_with_other_alphabets():
    assert needs_hebrew("Dana Levi")
    assert needs_hebrew("Yossi יוסי")
    assert needs_hebrew("Иван")
    assert not needs_hebrew("יוסי כהן")
    assert not needs_hebrew("משתתף ...1234")
    assert not needs_hebrew("🙂")


def test_is_hebrew_name_rejects_mixed_or_empty_spellings():
    assert is_hebrew_name("ג'ון דו")
    assert not is_hebrew_name("John")
    assert not is_hebrew_name("דנה Levi")
    assert not is_hebrew_name("123")


def test_parse_numbered_names_matches_by_number_and_skips_bad_lines():
    names = ["Dana", "John Doe", "Mike"]
    reply = "הנה השמות:\n2) ג'ון דו\n1. **דנה**\n3. Mike\n7. שגיא"

    assert numbered_list(names) == "1. Dana\n2. John Doe\n3. Mike"
    assert parse_numbered_names(names, reply) == {"Dana": "דנה", "John Doe": "ג'ון דו"}


def test_save_and_load_names_round_trip(tmp_path: Path):
    path = str(tmp_path / "data" / "hebrew_names.json")

    save_names(path, {"mike": "מייק", "Dana": "דנה"})

    assert load_names(path) == {"Dana": "דנה", "mike": "מייק"}
    assert "דנה" in Path(path).read_text(encoding="utf-8")


def test_load_names_tolerates_missing_or_broken_file(tmp_path: Path):
    assert load_names("") == {}
    assert load_names(str(tmp_path / "missing.json")) == {}
    broken = tmp_path / "broken.json"
    broken.write_text("[not a dict", encoding="utf-8")
    assert load_names(str(broken)) == {}
