from __future__ import annotations


def format_elapsed_seconds(seconds_value: float | int | None) -> str:
    """Format observed elapsed time without forcing a non-zero floor."""
    try:
        seconds = int(round(float(seconds_value or 0.0)))
    except Exception:
        seconds = 0
    if seconds <= 0:
        return "0s"
    if seconds < 90:
        return f"{seconds}s"
    mins = seconds / 60.0
    return f"{mins:.1f} min"
