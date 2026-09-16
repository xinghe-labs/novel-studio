# -*- coding: utf-8 -*-
"""Chinese numeral codec for the contradiction injector.

The injector mutates values *subtly* (a nearby number that a typo could
plausibly produce) rather than substituting an arbitrary value from elsewhere in
the corpus, because subtle corruptions are the realistic failure mode and the
hard case for a detector to catch.

Rendering Chinese numerals is easy to get subtly wrong (一十 vs 十, 两百 vs 二百,
零 placeholders in 一千九百零三). So every render is verified by parsing it back:
`parse_cn(render_cn(n)) == n` is asserted in tests across a wide range, and the
injector additionally round-trips each candidate it generates before using it.

Stdlib only.
"""
from __future__ import annotations

DIGITS = {
    "〇": 0, "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
SMALL_UNITS = {"十": 10, "百": 100, "千": 1000}
BIG_UNITS = {"万": 10 ** 4, "亿": 10 ** 8}
SPECIAL_UNITS = {"廿": 20, "卅": 30}

DIGIT_CHARS = "".join(DIGITS)
UNIT_CHARS = "".join(SMALL_UNITS) + "".join(BIG_UNITS) + "".join(SPECIAL_UNITS)
# Characters that may appear inside a numeral expression.
NUMERAL_CHARS = DIGIT_CHARS + UNIT_CHARS

_RENDER_DIGITS = "零一二三四五六七八九"


def parse_cn(text: str) -> int | None:
    """Parse a Chinese numeral expression to an int, or None if malformed.

    Handles 十/百/千 sections, 万/亿 groupings, and the 廿/卅 contractions.
    """
    if not text:
        return None
    total = 0
    section = 0
    number = 0
    for ch in text:
        if ch in DIGITS:
            number = DIGITS[ch]
        elif ch in SPECIAL_UNITS:
            section += SPECIAL_UNITS[ch]
            number = 0
        elif ch in SMALL_UNITS:
            unit = SMALL_UNITS[ch]
            # 十 alone means 10, not 0*10
            section += (number if number else 1) * unit
            number = 0
        elif ch in BIG_UNITS:
            unit = BIG_UNITS[ch]
            section = (section + number) * unit
            total += section
            section = 0
            number = 0
        else:
            return None
    return total + section + number


def _render_section(value: int, use_liang: bool = False) -> str:
    """Render 0..9999 without grouping units."""
    if value == 0:
        return ""
    out: list[str] = []
    pending_zero = False
    started = False
    for place, name in ((1000, "千"), (100, "百"), (10, "十"), (1, "")):
        digit = value // place % 10
        if digit == 0:
            if started and value % place:
                pending_zero = True
            continue
        if pending_zero:
            out.append("零")
            pending_zero = False
        head = _RENDER_DIGITS[digit]
        # 两百/两千 rather than 二百/二千, when the source text used 两
        if use_liang and digit == 2 and place >= 100 and not out:
            head = "两"
        out.append(head + name)
        started = True
    rendered = "".join(out)
    # 一十五 -> 十五 (but keep 一十八 when inside a larger number)
    if rendered.startswith("一十"):
        rendered = rendered[1:]
    return rendered


def render_cn(value: int, use_liang: bool = False) -> str | None:
    """Render an int as a Chinese numeral expression. None if out of range."""
    if value < 0 or value > 10 ** 12:
        return None
    if value == 0:
        return "零"
    if value < 10000:
        return _render_section(value, use_liang)

    parts: list[str] = []
    remainder = value
    for unit, name in ((10 ** 8, "亿"), (10 ** 4, "万")):
        chunk = remainder // unit
        remainder = remainder % unit
        if chunk:
            head = _render_section(chunk, use_liang) or "零"
            parts.append(head + name)
        elif parts:
            parts.append("零")
    if remainder:
        parts.append(_render_section(remainder, use_liang))
    rendered = "".join(parts)
    # collapse any doubled 零 produced by grouping, and trim a trailing 零
    while "零零" in rendered:
        rendered = rendered.replace("零零", "零")
    return rendered


def round_trips(value: int, use_liang: bool = False) -> bool:
    """True when the rendered form parses back to the same integer."""
    rendered = render_cn(value, use_liang=use_liang)
    return rendered is not None and parse_cn(rendered) == value


def nearby_values(value: int, deltas: tuple[int, ...] = (1, -1, 2, -2, 3, -3)):
    """Candidate replacement values, nearest first, that survive a round trip."""
    for delta in deltas:
        candidate = value + delta
        if candidate >= 0 and round_trips(candidate):
            yield candidate