from pydantic import ValidationError
import pytest

import color as tested_color


# ----------------------------
# Color parsing
# ----------------------------

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("#abc", (170, 187, 204, 255)),
        ("#aabbcc", (170, 187, 204, 255)),
        ("#abcd", (170, 187, 204, 221)),
        ("#aabbccdd", (170, 187, 204, 221)),
        ("#ABC", (170, 187, 204, 255)),
        ("#AABBCCDD", (170, 187, 204, 221)),
    ],
)
def test_parse_hex_colors(value, expected):
    assert tested_color.parse_str_color_exn(value) == expected


def test_parse_named_color_case_insensitively():
    name = next(iter(tested_color.COLORS_BY_NAME))
    assert tested_color.parse_str_color_exn(name) == (*tested_color.COLORS_BY_NAME[name], 255)
    assert tested_color.parse_str_color_exn(name.upper()) == (*tested_color.COLORS_BY_NAME[name], 255)


@pytest.mark.parametrize("value", ["", "red-ish", "#12", "#12345", "#ggg", "not-a-color"])
def test_parse_invalid_color_raises_custom_exception(value):
    with pytest.raises(tested_color.ColorNotParseable):
        tested_color.parse_str_color_exn(value)


# ----------------------------
# ColorInterface
# ----------------------------

def test_as_rgba_returns_fixed_color_components():
    c = tested_color.FixedColor(r=12, g=34, b=56, a=78)
    assert c.as_rgba_exn() == (12, 34, 56, 78)
    assert c.as_rgba() == (12, 34, 56, 78)


def test_as_rgba_falls_back_to_transparent_black_on_error():
    c = tested_color.StringColor(color="not-a-color")
    assert c.as_rgba() == (0, 0, 0, 255)


def test_as_hex_long_format():
    c = tested_color.FixedColor(r=170, g=187, b=204, a=221)
    assert c.as_hex("long") == "#aabbccdd"


def test_as_hex_short_format_when_channels_are_repeated():
    c = tested_color.FixedColor(r=170, g=187, b=204, a=221)
    assert c.as_hex("short") == "#abcd"


def test_as_hex_short_format_falls_back_to_long_when_not_compressible():
    c = tested_color.FixedColor(r=171, g=187, b=204, a=221)
    assert c.as_hex("short") == "#abbbccdd"


def test_as_hsl_tuple_returns_four_values_in_expected_ranges():
    c = tested_color.FixedColor(r=255, g=0, b=0, a=128)
    h, s, lightness, alpha = c.as_hsl_tuple()
    assert h == pytest.approx(0.0)
    assert s == pytest.approx(1.0)
    assert lightness == pytest.approx(0.5)
    assert alpha == pytest.approx(128 / 255)


def test_as_hsl_string():
    c = tested_color.FixedColor(r=255, g=0, b=0, a=255)
    assert c.as_hsl_str() == "hsl(0, 100%, 50%, 1.0)"


def test_string_representation_is_rgba_tuple():
    c = tested_color.FixedColor(r=1, g=2, b=3, a=4)
    assert str(c) == "(1, 2, 3, 4)"


def test_colors_compare_by_rgba_values():
    a = tested_color.FixedColor(r=1, g=2, b=3, a=4)
    b = tested_color.FixedColor(r=1, g=2, b=3, a=4)
    c = tested_color.FixedColor(r=1, g=2, b=3, a=5)
    assert a == b
    assert a != c


def test_colors_are_hashable_by_rgba_values():
    a = tested_color.FixedColor(r=1, g=2, b=3, a=4)
    b = tested_color.FixedColor(r=1, g=2, b=3, a=4)
    assert hash(a) == hash(b)
    assert len({a, b}) == 1


# ----------------------------
# Pydantic color models
# ----------------------------

def test_fixed_color_defaults():
    c = tested_color.FixedColor(r=1, g=2, b=3)
    assert c.colorType is tested_color.ColorType.FIXED_COLOR
    assert c.as_rgba() == (1, 2, 3, 0)


@pytest.mark.parametrize(
    "payload",
    [
        {"r": -1, "g": 0, "b": 0},
        {"r": 256, "g": 0, "b": 0},
        {"r": 0, "g": -1, "b": 0},
        {"r": 0, "g": 256, "b": 0},
        {"r": 0, "g": 0, "b": -1},
        {"r": 0, "g": 0, "b": 256},
        {"r": 0, "g": 0, "b": 0, "a": -1},
        {"r": 0, "g": 0, "b": 0, "a": 256},
    ],
)
def test_fixed_color_rejects_channels_outside_byte_range(payload):
    with pytest.raises(ValidationError):
        tested_color.FixedColor(**payload)


def test_string_color_parses_on_demand():
    c = tested_color.StringColor(color="#123")
    assert c.rgba_exn == (17, 34, 51, 255)


def test_string_color_invalid_value_raises_from_exn_method():
    c = tested_color.StringColor(color="invalid")
    with pytest.raises(tested_color.ColorNotParseable):
        c = c.rgba_exn


# ----------------------------
# Palette and context behavior
# ----------------------------

def _palette(*rgba_values):
    return tested_color.ColorPalette(
        availableColors=tuple(
            tested_color.FixedColor(r=r, g=g, b=b, a=a)
            for r, g, b, a in rgba_values
        )
    )


def test_palette_available_colors_are_immutable():
    palette = _palette((1, 2, 3, 4), (5, 6, 7, 8))
    with pytest.raises(ValidationError):
        palette.availableColors = (tested_color.FixedColor(r=0, g=0, b=0),)
    with pytest.raises(TypeError):
        palette.availableColors[0] = tested_color.FixedColor(r=0, g=0, b=0)


def test_palette_repr_is_comma_separated_hex_colors():
    palette = _palette((255, 0, 0, 0), (0, 255, 0, 0))
    assert repr(palette) == "#f000,#0f00"


def test_palette_in_context_uses_current_palette_when_index_is_none():
    palette = _palette((1, 2, 3, 0))
    token = tested_color.current_color_palette.set(palette)
    try:
        assert tested_color.palette_in_context_exn() is palette
    finally:
        tested_color.current_color_palette.reset(token)


def test_palette_in_context_uses_indexed_palette_list():
    palette = _palette((1, 2, 3, 0))
    token = tested_color.current_color_palettes.set([palette])
    try:
        assert tested_color.palette_in_context_exn(0) is palette
    finally:
        tested_color.current_color_palettes.reset(token)


def test_palette_in_context_raises_if_current_palette_is_unset():
    # ContextVar has no default for current_color_palette.
    with pytest.raises(LookupError):
        tested_color.current_color_palette.get()


def test_palette_color_wraps_index_when_avoid_index_exception_is_true():
    palette = _palette((10, 20, 30, 0), (40, 50, 60, 0))
    token = tested_color.current_color_palette.set(palette)
    try:
        c = tested_color.PaletteColor(colorIndexInPalette=3)
        assert c.rgba_exn == (40, 50, 60, 0)
    finally:
        tested_color.current_color_palette.reset(token)


def test_palette_color_raises_for_out_of_range_index_when_wrapping_disabled():
    palette = _palette((10, 20, 30, 0),)
    token = tested_color.current_color_palette.set(palette)
    try:
        c = tested_color.PaletteColor(colorIndexInPalette=2, avoidIndexException=False)
        with pytest.raises(IndexError):
            c = c.rgba_exn
    finally:
        tested_color.current_color_palette.reset(token)


# ----------------------------
# Random color behavior
# ----------------------------

def test_random_color_uses_random_rgb_channels_and_configured_alpha(monkeypatch):
    values = iter([10, 20, 30])
    monkeypatch.setattr(tested_color.random, "randint", lambda low, high: next(values))
    c = tested_color.RandomColor(transparent=128)
    assert c.rgba_exn == (10, 20, 30, 128)


def test_random_color_defaults_alpha_to_zero(monkeypatch):
    values = iter([10, 20, 30])
    monkeypatch.setattr(tested_color.random, "randint", lambda low, high: next(values))
    c = tested_color.RandomColor()
    assert c.rgba_exn == (10, 20, 30, 0)


def test_random_palette_color_selects_a_color_from_palette(monkeypatch):
    palette = _palette((10, 20, 30, 0), (40, 50, 60, 0))
    token = tested_color.current_color_palette.set(palette)
    try:
        monkeypatch.setattr(tested_color.random, "randint", lambda low, high: 1)
        c = tested_color.RandomPaletteColor()
        assert c.rgba_exn == (40, 50, 60, 0)
    finally:
        tested_color.current_color_palette.reset(token)


def test_random_palette_color_index_range_does_not_include_length(monkeypatch):
    """Regression test: randint's upper bound currently permits an invalid index."""
    palette = _palette((10, 20, 30, 0), (40, 50, 60, 0))
    token = tested_color.current_color_palette.set(palette)
    try:
        monkeypatch.setattr(tested_color.random, "randint", lambda low, high: high)
        c = tested_color.RandomPaletteColor()
        with pytest.raises(IndexError):
            c = c.rgba_exn
    finally:
        tested_color.current_color_palette.reset(token)
