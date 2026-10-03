import logging
from pathlib import Path

import pytest
from app.services.style import load_examples


def write_files(directory: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (directory / name).write_text(text, encoding="utf-8")


def test_missing_directory_gives_no_examples(tmp_path: Path) -> None:
    assert load_examples(tmp_path / "absent", 3) == []


def test_empty_directory_gives_no_examples(tmp_path: Path) -> None:
    assert load_examples(tmp_path, 3) == []


def test_a_file_in_place_of_the_directory_gives_no_examples(tmp_path: Path) -> None:
    path = tmp_path / "examples"
    path.write_text("Пост.", encoding="utf-8")

    assert load_examples(path, 3) == []


def test_one_file_is_one_example_stripped(tmp_path: Path) -> None:
    write_files(tmp_path, {"post.md": "\n  Первый пост.\n\nВторой абзац.  \n"})

    assert load_examples(tmp_path, 3) == ["Первый пост.\n\nВторой абзац."]


def test_files_are_taken_in_name_order(tmp_path: Path) -> None:
    write_files(tmp_path, {"b.md": "Б", "a.md": "А", "c.md": "В"})

    assert load_examples(tmp_path, 3) == ["А", "Б", "В"]


def test_only_the_first_n_are_taken(tmp_path: Path) -> None:
    write_files(tmp_path, {f"{index:02}.md": f"Пост {index}" for index in range(5)})

    assert load_examples(tmp_path, 2) == ["Пост 0", "Пост 1"]


def test_a_limit_of_zero_gives_no_examples(tmp_path: Path) -> None:
    write_files(tmp_path, {"a.md": "А"})

    assert load_examples(tmp_path, 0) == []


def test_gitkeep_hidden_and_other_files_are_ignored(tmp_path: Path) -> None:
    write_files(
        tmp_path,
        {
            ".gitkeep": "",
            ".draft.md": "Скрытый",
            "notes.txt": "Не пост",
            "post.md.bak": "Копия",
            "post.MD": "Пост",
        },
    )
    (tmp_path / "folder.md").mkdir()

    assert load_examples(tmp_path, 3) == ["Пост"]


def test_empty_files_are_skipped_and_do_not_use_up_the_limit(tmp_path: Path) -> None:
    write_files(tmp_path, {"a.md": "", "b.md": "  \n\t", "c.md": "В", "d.md": "Г"})

    assert load_examples(tmp_path, 2) == ["В", "Г"]


def test_a_file_that_is_not_utf8_is_skipped_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "a.md").write_bytes("Пост".encode("cp1251"))
    write_files(tmp_path, {"b.md": "Б"})

    with caplog.at_level(logging.WARNING):
        assert load_examples(tmp_path, 3) == ["Б"]

    assert "a.md" in caplog.text
    assert "Пост" not in caplog.text
