import sqlite3
from dataclasses import dataclass
from fractions import Fraction

from shift.calibration import Calibration, freeze_calibration
from shift.config import ShiftConfig, require_identifier


@dataclass(frozen=True)
class Recording:
    shift_id: str
    recording_id: str
    calibration: Calibration


class ShiftRegistry:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def initialize(self) -> None:
        self.db.execute('''
            CREATE TABLE IF NOT EXISTS shifts (
                shift_id TEXT NOT NULL PRIMARY KEY,
                recording_id TEXT NOT NULL UNIQUE,
                zero TEXT NOT NULL,
                numerator TEXT NOT NULL,
                denominator TEXT NOT NULL
            )
        ''')

    def open_shift(self, config: ShiftConfig) -> Recording:
        calibration = freeze_calibration(config)
        proposed = Recording(config.shift_id, config.recording_id, calibration)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            existing = self.get_shift(config.shift_id)
            if existing is not None:
                if existing != proposed:
                    raise ValueError('shift ID already has a different binding')
                return existing
            if self.get_recording(config.recording_id) is not None:
                raise ValueError('recording is already bound to a shift')
            self.db.execute(
                'INSERT INTO shifts VALUES (?, ?, ?, ?, ?)',
                (proposed.shift_id, proposed.recording_id, str(calibration.zero),
                 str(calibration.ratio.numerator), str(calibration.ratio.denominator)),
            )
        return proposed

    def get_shift(self, shift_id: str) -> Recording | None:
        require_identifier(shift_id, 'shift_id')
        row = self.db.execute('SELECT * FROM shifts WHERE shift_id = ?', (shift_id,)).fetchone()
        return self._decode(row)

    def get_recording(self, recording_id: str) -> Recording | None:
        require_identifier(recording_id, 'recording_id')
        row = self.db.execute(
            'SELECT * FROM shifts WHERE recording_id = ?', (recording_id,),
        ).fetchone()
        return self._decode(row)

    @staticmethod
    def _decode(row: tuple | None) -> Recording | None:
        if row is None:
            return None
        shift_id, recording_id, zero, numerator, denominator = row
        return Recording(shift_id, recording_id,
                         Calibration(int(zero), Fraction(int(numerator), int(denominator))))
