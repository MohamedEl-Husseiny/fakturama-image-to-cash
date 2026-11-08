"""Offline field-format cases; these do not verify desktop widget behavior."""

from decimal import Decimal
import pytest

from fakturama.fields import NumericFieldError, parse_ui_decimal, parse_ui_money, format_ui_number


@pytest.mark.parametrize(
    "text,expected",
    [
        ("678,30 EUR", "678.30"),
        ("1.234,57 €", "1234.57"),
        ("$1,234.57", "1234.57"),
        ("| -10.00 %", "-10.00"),
        ("0,00", "0.00"),
        ("1,234", "1234"),
        ("0.125", "0.125"),
        ("+40,00", "40.00"),
        ("-$1,234.57", "-1234.57"),
        ("-EUR 1.234,57", "-1234.57"),
    ],
)
def test_observed_numeric_formats(text, expected):
    assert parse_ui_decimal(text) == Decimal(expected)


@pytest.mark.parametrize(
    "text", ["", "EUR", "250,00 / 120,00", "1.2.3", "NaN", "Infinity", "--1.00", "1.00-", "(1.00)"]
)
def test_unreadable_or_multiple_values_are_rejected(text):
    with pytest.raises(NumericFieldError):
        parse_ui_decimal(text)


def test_quantity_precision_is_independent_of_money_rounding():
    assert parse_ui_decimal("0.125") == Decimal("0.125")
    assert parse_ui_money("0.125") == Decimal("0.13")


def test_formatting_uses_destination_cell_not_a_global_locale():
    assert format_ui_number(2, "1.00") == "2.00"
    assert format_ui_number(250, "0,00 EUR") == "250,00"
    assert format_ui_number(10, "0.00 %") == "10.00"


def test_workflow_guard_does_not_concatenate_two_amounts():
    from fakturama_full_workflow import decimal_from_ui, GuardError

    with pytest.raises(GuardError):
        decimal_from_ui("250,00 120,00")
