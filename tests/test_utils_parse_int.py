"""Tests for parse_int / try_parse_int helpers (Area 9)."""

from __future__ import annotations

import pytest

from custom_components.grocy_helper.utils import parse_int, try_parse_int


# ── parse_int ───────────────────────────────────────────────────────


def test_parse_int_returns_parsed_value_for_valid_input():
    """Valid integer-like input should parse successfully."""
    assert parse_int("42") == 42



def test_parse_int_returns_default_for_invalid_input():
    """Invalid value should return provided default."""
    assert parse_int("abc", default=7) == 7



def test_parse_int_returns_default_for_none_input():
    """None should not raise and should return default."""
    assert parse_int(None, default=9) == 9



def test_parse_int_returns_default_for_empty_string():
    """Empty string should be treated as invalid and return default."""
    assert parse_int("", default=3) == 3



def test_parse_int_raise_errors_true_propagates_value_error():
    """When raise_errors=True, parse_int should raise on invalid input."""
    with pytest.raises(ValueError):
        parse_int("not-an-int", raise_errors=True)


# ── try_parse_int ───────────────────────────────────────────────────


def test_try_parse_int_returns_success_tuple_for_valid_input():
    """Valid input returns (True, parsed_int)."""
    assert try_parse_int("123") == (True, 123)



def test_try_parse_int_accepts_whitespace_wrapped_integer():
    """Whitespace around digits should still parse as int."""
    assert try_parse_int(" 5 ") == (True, 5)



def test_try_parse_int_returns_failure_tuple_for_invalid_string():
    """Invalid string should return (False, 0)."""
    assert try_parse_int("abc") == (False, 0)



def test_try_parse_int_returns_failure_tuple_for_none():
    """None should return (False, 0)."""
    assert try_parse_int(None) == (False, 0)



def test_try_parse_int_returns_failure_tuple_for_empty_string():
    """Empty string should return (False, 0)."""
    assert try_parse_int("") == (False, 0)
