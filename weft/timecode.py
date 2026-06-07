from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


def parse_timecode(value: str) -> Decimal:
    value = value.strip()
    if not value:
        raise ValueError("empty timecode")
    parts = value.split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        return Decimal(minutes) * Decimal(60) + Decimal(seconds)
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return Decimal(hours) * Decimal(3600) + Decimal(minutes) * Decimal(60) + Decimal(seconds)
    return Decimal(value)


def parse_time_range(value: str) -> tuple[Decimal, Decimal] | None:
    value = value.strip()
    if not value or "~" not in value:
        return None
    start, end = value.split("~", 1)
    return parse_timecode(start), parse_timecode(end)


def seconds_to_samples(seconds: Decimal | float | int, sample_rate: int) -> int:
    dec = seconds if isinstance(seconds, Decimal) else Decimal(str(seconds))
    return int((dec * Decimal(sample_rate)).to_integral_value(rounding=ROUND_HALF_UP))


def samples_to_seconds(samples: int, sample_rate: int) -> float:
    return samples / sample_rate


def format_clock(samples: int, sample_rate: int) -> str:
    total_ms = int(Decimal(samples * 1000 / sample_rate).to_integral_value(rounding=ROUND_HALF_UP))
    minutes, ms = divmod(total_ms, 60_000)
    seconds, millis = divmod(ms, 1000)
    return f"{minutes}:{seconds:02d}.{millis:03d}"


def format_srt_time(samples: int, sample_rate: int) -> str:
    total_ms = int(Decimal(samples * 1000 / sample_rate).to_integral_value(rounding=ROUND_HALF_UP))
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"
