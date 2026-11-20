from fractions import Fraction

from shift.calibration import Calibration


def to_micrometres(raw: int, calibration: Calibration) -> Fraction:
    return (raw - calibration.zero) * calibration.ratio
