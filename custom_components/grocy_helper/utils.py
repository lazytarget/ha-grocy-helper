from typing import Any, Iterable

from .const import NUMERIC_FIELDS

def parse_int(value: Any, catch_errors: bool = True) -> int:
    """Parsing a value as int. Raise errors."""
    if not catch_errors:
        return int(value)
    (_, parsed) = try_parse_int(value)
    return parsed

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
        if key not in NUMERIC_FIELDS:
            val = str(val) if val is not None else None
        user_input[key] = val
    return user_input
