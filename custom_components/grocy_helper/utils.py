from typing import Any, Iterable

from .const import NUMERIC_FIELDS


def parse_int(value: Any, default: int = 0, raise_errors: bool = False) -> int:
    """Parsing a value as int. If failure, then return default. Raise errors if raise_errors is True."""
    if raise_errors:
        return int(value)
    (success, parsed) = try_parse_int(value)
    return parsed if success else default


def try_parse_int(value: Any) -> tuple[bool, int]:
    """Try parsing a value as int. Return as a tuple."""
    try:
        return (True, int(value))
    except ValueError:
        return (False, 0)
    except TypeError:
        return (False, 0)


def transform_input(
    user_input: dict | None,
    persisted: dict | None,
    suggested: dict | None,
    keys: Iterable[str] | None = None,
    str_keys: Iterable[str] | None = None,
) -> dict:
    """Resolve input by merging user input, persisted data, and suggested data.

    Parameters
    ----------
    user_input:
        Submitted user input (highest precedence)
    persisted:
        Persisted product data (medium precedence)
    suggested:
        Suggested product data (lowest precedence)
    keys:
        List of keys to resolve (if None, resolve all keys present in any dict)
    str_keys:
        List of keys to always convert to strings (if None, use default behavior)

    Returns
    -------
        User input dictionary with defaults filled in
    """
    user_input = user_input or {}
    persisted = persisted or {}
    suggested = suggested or {}
    if keys is None:
        # By default, resolve all keys present in any of the dictionaries
        keys = set(user_input) | set(persisted) | set(suggested)

    for key in keys:
        val = user_input.get(key, persisted.get(key) or suggested.get(key))
        if str_keys is not None:
            if key in str_keys:
                val = str(val) if val is not None else None
        else:
            if key not in NUMERIC_FIELDS:
                val = str(val) if val is not None else None
        user_input[key] = val
    return user_input
