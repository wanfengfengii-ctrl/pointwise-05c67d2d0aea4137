from dataclasses import dataclass


@dataclass(frozen=True)
class ShiftConfig:
    shift_id: str
    recording_id: str
    zero: int
    numerator: int
    denominator: int


def require_identifier(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field} must be a nonblank string')


def validate_shift_config(config: ShiftConfig) -> ShiftConfig:
    require_identifier(config.shift_id, 'shift_id')
    require_identifier(config.recording_id, 'recording_id')
    for value in (config.zero, config.numerator, config.denominator):
        if type(value) is not int:
            raise ValueError('calibration requires integer arguments')
    if config.denominator == 0:
        raise ValueError('calibration denominator cannot be zero')
    return config
