"""Tests for connector result coercion (JSON-safe primitives)."""

import json
from datetime import datetime
from decimal import Decimal

from tellurite.connector import _jsonable


def test_jsonable_decimal_to_float():
    assert _jsonable(Decimal("12.34")) == 12.34
    assert isinstance(_jsonable(Decimal("12.34")), float)


def test_jsonable_datetime_to_iso():
    assert _jsonable(datetime(2026, 1, 2, 3, 4, 5)) == "2026-01-02T03:04:05"


def test_jsonable_recurses_into_struct_and_array():
    value = {"cost": Decimal("5.0"), "tags": [Decimal("1"), {"k": Decimal("2")}]}
    out = _jsonable(value)
    assert out == {"cost": 5.0, "tags": [1.0, {"k": 2.0}]}
    # The whole thing must now be JSON-serializable without a custom default.
    json.dumps(out)


def test_jsonable_passes_through_primitives():
    assert _jsonable("x") == "x"
    assert _jsonable(7) == 7
    assert _jsonable(None) is None
