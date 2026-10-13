"""Every interpretation in docs/SPEC_NOTES.md is cited from the code that implements it and
from a test that checks it (PLAN goal 4), as INT-nnn or, in a test's name, int_nnn."""

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
ENTRIES = re.findall(r"^### (INT-\d{3}):", (ROOT / "docs" / "SPEC_NOTES.md").read_text(), re.M)


def cited(directory: str, entry: str) -> bool:
    number = entry.split("-")[1]
    pattern = re.compile(rf"\bINT-{number}\b|\bint_{number}")
    return any(pattern.search(p.read_text()) for p in (ROOT / directory).rglob("*.py"))


def test_the_register_was_read():
    assert len(ENTRIES) >= 28 and len(set(ENTRIES)) == len(ENTRIES)


@pytest.mark.parametrize("entry", ENTRIES)
def test_every_interpretation_is_cited_in_code_and_tests(entry):
    assert cited("src", entry), f"{entry} is not cited under src/"
    assert cited("tests", entry), f"{entry} is not cited under tests/"
