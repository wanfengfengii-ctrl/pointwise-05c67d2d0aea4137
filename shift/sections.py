import sqlite3
from dataclasses import dataclass

from shift.config import require_identifier
from shift.registry import Recording, ShiftRegistry


@dataclass(frozen=True)
class Section:
    section_id: str
    recording: Recording
    first_slot: int
    last_slot: int


class SectionRegistry:
    def __init__(self, db: sqlite3.Connection, shifts: ShiftRegistry):
        self.db = db
        self.shifts = shifts

    def initialize(self) -> None:
        self.db.execute('''
            CREATE TABLE IF NOT EXISTS sections (
                section_id TEXT NOT NULL PRIMARY KEY,
                shift_id TEXT NOT NULL REFERENCES shifts(shift_id),
                first_slot TEXT NOT NULL,
                last_slot TEXT NOT NULL
            )
        ''')

    def define_section(self, section_id: str, shift_id: str,
                       first_slot: int, last_slot: int) -> Section:
        require_identifier(section_id, 'section_id')
        if (type(first_slot) is not int or type(last_slot) is not int
                or first_slot < 0 or last_slot < first_slot):
            raise ValueError('section requires an ordered nonnegative slot interval')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            recording = self.shifts.get_shift(shift_id)
            if recording is None:
                raise KeyError(shift_id)
            proposed = Section(section_id, recording, first_slot, last_slot)
            existing = self.get_section(section_id)
            if existing is not None:
                if existing != proposed:
                    raise ValueError('section ID already has a different definition')
                return existing
            self.db.execute('INSERT INTO sections VALUES (?, ?, ?, ?)',
                            (section_id, shift_id, str(first_slot), str(last_slot)))
        return proposed

    def get_section(self, section_id: str) -> Section | None:
        require_identifier(section_id, 'section_id')
        row = self.db.execute(
            'SELECT shift_id, first_slot, last_slot FROM sections WHERE section_id = ?',
            (section_id,),
        ).fetchone()
        if row is None:
            return None
        shift_id, first_slot, last_slot = row
        recording = self.shifts.get_shift(shift_id)
        if recording is None:
            raise RuntimeError('defined section has no shift binding')
        return Section(section_id, recording, int(first_slot), int(last_slot))
