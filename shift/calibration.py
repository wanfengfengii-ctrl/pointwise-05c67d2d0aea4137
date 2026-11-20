from dataclasses import dataclass
from fractions import Fraction

from shift.config import ShiftConfig, validate_shift_config


@dataclass(frozen=True)
class Calibration:
    zero: int
    ratio: Fraction


def freeze_calibration(config: ShiftConfig) -> Calibration:
    validate_shift_config(config)
    return Calibration(config.zero, Fraction(config.numerator, config.denominator))
