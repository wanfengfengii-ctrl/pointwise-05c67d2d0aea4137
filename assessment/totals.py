from collections.abc import Iterable
from dataclasses import dataclass
from fractions import Fraction


@dataclass(frozen=True)
class Totals:
    valid_count: int
    micrometres: Fraction


def summarize_readings(readings: Iterable[Fraction | None]) -> Totals:
    valid_count = 0
    total = Fraction(0)
    for reading in readings:
        if reading is not None:
            valid_count += 1
            total += reading
    return Totals(valid_count, total)
