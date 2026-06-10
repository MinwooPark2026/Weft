from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def _decimal_part(part: str, original: str) -> Decimal:
    try:
        return Decimal(part.strip())
    except InvalidOperation as exc:
        raise ValueError(f"invalid timecode: {original!r}") from exc


def parse_timecode(value: str) -> Decimal:
    value = value.strip()
    if not value:
        raise ValueError("empty timecode")
    parts = value.split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        return _decimal_part(minutes, value) * Decimal(60) + _decimal_part(seconds, value)
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return (
            _decimal_part(hours, value) * Decimal(3600)
            + _decimal_part(minutes, value) * Decimal(60)
            + _decimal_part(seconds, value)
        )
    return _decimal_part(value, value)


def parse_time_range(value: str) -> tuple[Decimal, Decimal] | None:
    # Accept fullwidth/wave-dash tildes (U+FF5E, U+301C) the same as ASCII "~".
    value = value.strip().replace("～", "~").replace("〜", "~")
    if not value or "~" not in value:
        return None
    start, end = value.split("~", 1)
    return parse_timecode(start), parse_timecode(end)


def seconds_to_samples(seconds: Decimal | float | int, sample_rate: int) -> int:
    dec = seconds if isinstance(seconds, Decimal) else Decimal(str(seconds))
    return int((dec * Decimal(sample_rate)).to_integral_value(rounding=ROUND_HALF_UP))


def samples_to_seconds(samples: int, sample_rate: int) -> float:
    return samples / sample_rate


def _samples_to_ms(samples: int, sample_rate: int) -> int:
    # Integer round-half-up; avoids float precision drift for long timelines.
    return (samples * 1000 + sample_rate // 2) // sample_rate


def format_clock(samples: int, sample_rate: int) -> str:
    total_ms = _samples_to_ms(samples, sample_rate)
    minutes, ms = divmod(total_ms, 60_000)
    seconds, millis = divmod(ms, 1000)
    return f"{minutes}:{seconds:02d}.{millis:03d}"


def format_srt_time(samples: int, sample_rate: int) -> str:
    total_ms = _samples_to_ms(samples, sample_rate)
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"
