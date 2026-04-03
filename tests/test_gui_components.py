"""Tests for polyphony_gui.components — shared UI helpers."""

from __future__ import annotations

import pytest

from polyphony_gui.components import (
    color_irr_value,
    format_irr_label,
    style_irr_cell,
)


# ─────────────────────────────────────────────────────────────────────────────
# IRR label formatting
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value, expected_text",
    [
        (0.95, "Excellent"),
        (0.80, "Excellent"),
        (0.70, "Moderate"),
        (0.50, "Poor"),
        (0.0, "Poor"),
    ],
)
def test_format_irr_label_text(value, expected_text):
    label = format_irr_label(value)
    assert expected_text in label


def test_format_irr_label_includes_emoji():
    """WCAG accessibility: labels should include text, not just color."""
    excellent = format_irr_label(0.95)
    poor = format_irr_label(0.3)
    # Both should have some indicator beyond just a number
    assert len(excellent) > 5
    assert len(poor) > 5
    # They should differ
    assert excellent != poor


# ─────────────────────────────────────────────────────────────────────────────
# IRR color coding
# ─────────────────────────────────────────────────────────────────────────────


def test_color_irr_value_high():
    color = color_irr_value(0.9)
    assert isinstance(color, str)
    assert "#" in color


def test_color_irr_value_low():
    color = color_irr_value(0.3)
    assert isinstance(color, str)
    assert "#" in color


def test_color_irr_values_differ():
    """High and low values should map to different colors."""
    assert color_irr_value(0.95) != color_irr_value(0.3)


# ─────────────────────────────────────────────────────────────────────────────
# IRR cell styling
# ─────────────────────────────────────────────────────────────────────────────


def test_style_irr_cell_numeric():
    result = style_irr_cell(0.85)
    assert "background-color" in result


def test_style_irr_cell_string():
    result = style_irr_cell("0.75")
    assert "background-color" in result


def test_style_irr_cell_non_numeric():
    result = style_irr_cell("—")
    assert result == ""


def test_style_irr_cell_none():
    result = style_irr_cell(None)
    assert result == ""
