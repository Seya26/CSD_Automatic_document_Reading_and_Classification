import pytest

from albo.values import norm_municipality, norm_number, parse_date


@pytest.mark.parametrize("raw,expected", [
    ("12/03/2025", "2025-03-12"),
    ("12-03-2025", "2025-03-12"),
    ("12.3.25", "2025-03-12"),
    ("2025-03-12", "2025-03-12"),
    ("1° marzo 2025", "2025-03-01"),
    ("3 Aprile 2025", "2025-04-03"),
    ("del 21/02/2025", "2025-02-21"),
    ("31/02/2025", None),        # impossible date
    ("12/03/1850", None),        # implausible year
    ("marzo", None),
    ("", None),
    (None, None),
])
def test_parse_date(raw, expected):
    assert parse_date(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("45", "45"),
    ("N. 045/2025", "45/2025"),
    ("n° 210 / 2025", "210/2025"),
    ("Reg. Gen. n. 7", "7"),
    ("senza numero", ""),
    (None, ""),
])
def test_norm_number(raw, expected):
    assert norm_number(raw) == expected


def test_norm_municipality_ignores_prefix_and_case():
    assert norm_municipality("COMUNE DI CAMERINO") == norm_municipality("Camerino") == "camerino"


def test_all_dates_and_number_tokens():
    from albo.values import all_dates, number_tokens
    assert all_dates("N. 7 del 10/01/2025, pubblicata il 15 gennaio 2025") == ["2025-01-10", "2025-01-15"]
    assert all_dates("nessuna data") == []
    assert number_tokens("N. 045 del 12/03/2025") == {45, 12, 3, 2025}
