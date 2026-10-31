"""Slug generation, shared by the row identifier and (in stage 6) the report filename."""

from __future__ import annotations

import pytest

from auto_html_report._naming import DEFAULT_MAX_LENGTH, slug


def test_slug_should_lowercase_and_hyphenate_runs_of_punctuation() -> None:
    # given a title with mixed case and punctuation
    # when it is slugged
    # then it becomes a lowercase token with single hyphens, safe in a filename and an attribute
    assert slug("LINE 4 — Daily Health") == "line-4-daily-health"


def test_slug_should_strip_leading_and_trailing_separators() -> None:
    # given text surrounded by punctuation
    # when it is slugged
    # then no separator is left at either end, so the token composes cleanly into a filename
    assert slug("/leading and trailing/") == "leading-and-trailing"


def test_slug_should_cap_the_length_at_the_documented_default() -> None:
    # given a 300-character title, as the filename tests in docs/06 exercise
    # when it is slugged
    # then the result respects the documented cap, so filenames and S3 keys stay within limits
    assert len(slug("x" * 300)) <= DEFAULT_MAX_LENGTH
    assert DEFAULT_MAX_LENGTH == 60


def test_slug_should_not_leave_a_trailing_hyphen_when_truncating() -> None:
    # given text whose 60th character falls on a separator
    # when it is slugged
    # then the cut is tidied, because a trailing hyphen looks like a truncation bug to a reader
    assert slug(("abcde " * 20).strip(), max_length=11) == "abcde-abcde"


def test_slug_with_no_usable_characters_should_return_the_callers_fallback() -> None:
    # given a title made only of emoji and punctuation
    # when it is slugged with a fallback
    # then the fallback is returned rather than an empty identifier, because a blank one would be
    # useless in a filename and invisible in an attribute
    assert slug("🎉🎉🎉", fallback="block") == "block"


def test_slug_with_no_usable_characters_and_no_fallback_should_return_empty() -> None:
    # given the same title
    # when no fallback is supplied
    # then an empty string comes back, leaving the choice to the caller rather than inventing a name
    assert slug("🎉🎉🎉") == ""


def test_slug_with_a_path_separator_should_remove_it() -> None:
    # given a title containing a path separator, which would otherwise escape a directory
    # when it is slugged
    # then no separator survives
    assert "/" not in slug("../../etc/passwd")


def test_slug_with_max_length_below_one_should_raise_value_error() -> None:
    # given a nonsensical cap
    # when it is slugged
    # then it is refused rather than silently returning an empty string
    with pytest.raises(ValueError, match="max_length"):
        slug("anything", max_length=0)


def test_slug_should_be_idempotent() -> None:
    # given an already-slugged token
    # when it is slugged again
    # then nothing changes, so double-application cannot corrupt an identifier
    once = slug("LINE 4 — Daily Health")
    assert slug(once) == once
