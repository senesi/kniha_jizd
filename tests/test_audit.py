"""Auditní log - sanitizace payloadu (regrese).

Sloupce before_data/after_data jsou JSON. Do JSONu neprojde Decimal,
date, UUID ani set - a stačilo jednou zapomenout na převod, aby uložení
běžného formuláře skončilo na 500.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from app.core.audit import sanitize


def test_decimal_becomes_float():
    """Numeric sloupce (objem nádrže, cena) se z databáze vrací jako
    Decimal - právě na tom padala úprava vozidla."""
    assert sanitize(Decimal("50.5")) == 50.5
    assert sanitize({"tank_capacity_l": Decimal("55")}) == {"tank_capacity_l": 55.0}


def test_dates_become_iso_strings():
    assert sanitize(date(2026, 3, 12)) == "2026-03-12"
    moment = datetime(2026, 3, 12, 8, 30, tzinfo=timezone.utc)
    assert sanitize(moment) == moment.isoformat()


def test_uuid_becomes_string():
    value = uuid.uuid4()
    assert sanitize(value) == str(value)


def test_sets_are_sorted_for_reproducibility():
    assert sanitize({"b", "a", "c"}) == ["a", "b", "c"]


def test_nested_structures_are_walked():
    payload = {
        "price": Decimal("1887.15"),
        "dates": [date(2026, 1, 1), date(2026, 2, 1)],
        "nested": {"id": uuid.UUID("00000000-0000-0000-0000-000000000001")},
    }
    assert sanitize(payload) == {
        "price": 1887.15,
        "dates": ["2026-01-01", "2026-02-01"],
        "nested": {"id": "00000000-0000-0000-0000-000000000001"},
    }


def test_plain_values_pass_through():
    assert sanitize(None) is None
    assert sanitize(True) is True
    assert sanitize(42) == 42
    assert sanitize("text") == "text"


def test_unknown_type_degrades_to_string():
    """Radši méně přesný záznam než ztracený audit."""
    class Weird:
        def __str__(self):
            return "weird"

    assert sanitize(Weird()) == "weird"


def test_everything_survives_json_dumps():
    import json

    payload = sanitize({
        "decimal": Decimal("1.5"), "date": date(2026, 1, 1), "uuid": uuid.uuid4(),
        "set": {"a", "b"}, "list": [Decimal("2"), None], "nested": {"x": Decimal("3")},
    })
    json.dumps(payload)  # nesmí vyhodit
