from pydantic.color import COLORS_BY_NAME, r_hex_short, r_hex_long, r_rgb, r_hsl, r_rgb_v4_style, r_hsl_v4_style, \
    parse_hsl, repeat_colors
from pydantic import BaseModel, Field, ConfigDict

from typing import Literal, final, Protocol
from functools import cached_property
from contextvars import ContextVar
from colorsys import rgb_to_hls
from enum import Enum
from abc import ABC
import random
import re


class ColorProtocol(Protocol):

    def as_rgba_exn(self) -> tuple[int, int, int, int]:
        ...

    def as_rgba(self) -> tuple[int, int, int, int]:
        ...


class ColorInterface(ABC):

    def as_rgba_exn(self) -> tuple[int, int, int, int]:
        ...

    # The function above can raise unwanted exceptions in case we want a color and bad parameters have been specified
    # so a non-raising function returning black is also defined
    @final
    def as_rgba(self) -> tuple[int, int, int, int]:
        try:
            return self.as_rgba_exn()
        except:
            return 0, 0, 0, 255

    @cached_property
    @final
    def rgba_exn(self):
        return self.as_rgba_exn()

    @cached_property
    @final
    def rgba(self):
        return self.as_rgba()

    @final
    def as_hex(self, format: Literal['short', 'long'] = 'short') -> str:
        values = self.as_rgba()
        as_hex = ''.join(f"{v:02x}" for v in values)
        if format == 'short' and all(c in repeat_colors for c in values):
            as_hex = ''.join(as_hex[c] for c in range(0, len(as_hex), 2))
        return f"#{as_hex}"

    @final
    def as_hsl_str(self) -> str:
        h, s, li, a = self.as_hsl_tuple()
        return f"hsl({h * 360:0.0f}, {s:0.0%}, {li:0.0%}, {round(a, 2)})"

    def as_hsl_tuple(self) -> tuple[float, float, float, float]:
        values = self.as_rgba()
        h, l, s = rgb_to_hls(*(v / 255.0 for v in values[:3]))
        return h, s, l, values[-1] / 255.0 or 0

    def __str__(self) -> str:
        return f"{self.as_rgba()}"

    def __eq__(self, other: ColorProtocol) -> bool:
        return hasattr(other, 'as_rgba') and self.as_rgba() == other.as_rgba()

    def __hash__(self) -> int:
        return hash(self.as_rgba())


class ColorNotParseable(Exception):
    ...

def parse_str_color_exn(color: str) -> tuple[int, int, int, int]:
    value_lower = color.lower()
    if value_lower in COLORS_BY_NAME:
        r, g, b = COLORS_BY_NAME[value_lower]
        return r, g, b, 255

    m = re.fullmatch(r_hex_short, value_lower)
    if m:
        *rgb, a = m.groups()
        r, g, b = (int(v * 2, 16) for v in rgb)
        alpha = int(a * 2, 16) if a else 255
        return r, g, b, alpha

    m = re.fullmatch(r_hex_long, value_lower)
    if m:
        *rgb, a = m.groups()
        r, g, b = (int(v, 16) for v in rgb)
        alpha = int(a, 16) if a else 255
        return r, g, b, alpha

    m = re.fullmatch(r_rgb, value_lower) or re.fullmatch(r_rgb_v4_style, value_lower)
    if m:
        r, g, b, a = m.groups()
        return int(r), int(g), int(b), int(255 * a)

    m = re.fullmatch(r_hsl, value_lower) or re.fullmatch(r_hsl_v4_style, value_lower)
    if m:
        r, g, b, a = parse_hsl(*m.groups())
        return int(255 * r), int(255 * g), int(255 * b), int(255 * a)

    raise ColorNotParseable(color)


class ColorType(Enum):
    FIXED_COLOR = 1
    STRING_COLOR = 2
    PALETTE_COLOR = 3
    RANDOM_COLOR = 4
    RANDOM_PALETTE_COLOR = 5

class BaseColor(BaseModel):
    colorType: ColorType


class FixedColor(ColorInterface, BaseColor):
    colorType: Literal[ColorType.FIXED_COLOR] = ColorType.FIXED_COLOR
    r: int = Field(ge=0, le=255)
    g: int = Field(ge=0, le=255)
    b: int = Field(ge=0, le=255)
    a: int = Field(ge=0, le=255, default=0)

    def as_rgba_exn(self) -> tuple[int, int, int, int]:
        return self.r, self.g, self.b, self.a


class StringColor(ColorInterface, BaseColor):
    model_config = ConfigDict(frozen=True)

    colorType: Literal[ColorType.STRING_COLOR] = ColorType.STRING_COLOR
    color: str

    def as_rgba_exn(self) -> tuple[int, int, int, int]:
        return parse_str_color_exn(self.color)


class PaletteColor(ColorInterface, BaseColor):
    model_config = ConfigDict(frozen=True)

    colorType: Literal[ColorType.PALETTE_COLOR] = ColorType.PALETTE_COLOR
    paletteIndex: int | None = None  # if none, a current palette must be defined
    colorIndexInPalette: int
    avoidIndexException: bool = True  # take modulo if index is bad

    def as_rgba_exn(self) -> tuple[int, int, int, int]:
        palette = palette_in_context_exn(self.paletteIndex)
        index = self.colorIndexInPalette if not self.avoidIndexException else self.colorIndexInPalette % len(palette.availableColors)
        return palette.availableColors[index].as_rgba()


class RandomColor(ColorInterface, BaseColor):
    model_config = ConfigDict(frozen=True)

    colorType: Literal[ColorType.RANDOM_COLOR] = ColorType.RANDOM_COLOR
    transparent: int | None = None

    def as_rgba_exn(self) -> tuple[int, int, int, int]:
        return random.randint(0, 255), random.randint(0, 255), random.randint(0, 255), self.transparent or 0


class RandomPaletteColor(ColorInterface, BaseColor):
    model_config = ConfigDict(frozen=True)

    colorType: Literal[ColorType.RANDOM_PALETTE_COLOR] = ColorType.RANDOM_PALETTE_COLOR
    paletteIndex: int | None = None  # if none, a current palette must be defined

    def as_rgba_exn(self) -> tuple[int, int, int, int]:
        palette = palette_in_context_exn(self.paletteIndex)
        index = random.randint(0, len(palette.availableColors))
        return palette.availableColors[index].as_rgba()


class ColorPalette(BaseModel):
    model_config = ConfigDict(frozen=True)

    availableColors: tuple[FixedColor, ...]

    def __repr__(self):
        return ','.join([*(color.as_hex() for color in self.availableColors)])


_DEFAULT_PALETTES = [
    ["#80bee2", "#288eb0", "#082538", "#fca700", "#f36e00"],  # sky / teal / navy / amber / orange
    ["#08243b", "#c50f1d", "#ee6900", "#f8b231", "#e5dca6"],  # navy / red / orange / amber / sand
    ["#1f3643", "#2d8d7d", "#e1b751", "#ec8f4a", "#db573d"],  # slate / teal / mustard / apricot / coral
    ["#d81e36", "#eefaea", "#9cd2d5", "#3a688d", "#162847"],  # red / mint / aqua / steel / navy
]


DEFAULT_PALETTES = [
    ColorPalette(
        availableColors=[
            FixedColor(
                r=r,
                g=g,
                b=b,
                a=a,
            )
            for color in palette
            for r, g, b, a in [parse_str_color_exn(color)]
        ]
    ) for palette in _DEFAULT_PALETTES
]

current_color_palette = ContextVar[ColorPalette]('current_color_palette')
current_color_palettes = ContextVar[list[ColorPalette]]('current_color_palettes', default=DEFAULT_PALETTES)


def palette_in_context_exn(idx: int | None = None):
    if idx is None:
        return current_color_palette.get()
    return current_color_palettes.get()[idx]


if __name__ == '__main__':
    pass
