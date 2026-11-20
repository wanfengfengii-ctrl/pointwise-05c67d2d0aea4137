import sqlite3
from dataclasses import dataclass
from fractions import Fraction

from assessment.coverage import SlotState
from assessment.totals import Totals
from assessment.worksheet import Worksheet, WorksheetRow
from capture.provenance import Provenance
from shift.calibration import Calibration
from shift.registry import Recording
from shift.sections import Section


@dataclass(frozen=True)
class ReportRecord:
    number: str
    worksheet: Worksheet


class ReportStore:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def initialize(self) -> None:
        self.db.execute('''
            CREATE TABLE IF NOT EXISTS reports (
                number TEXT NOT NULL PRIMARY KEY,
                section_id TEXT NOT NULL UNIQUE REFERENCES sections(section_id),
                shift_id TEXT NOT NULL,
                recording_id TEXT NOT NULL,
                first_slot TEXT NOT NULL,
                last_slot TEXT NOT NULL,
                zero TEXT NOT NULL,
                ratio TEXT NOT NULL,
                valid_count INTEGER NOT NULL,
                total TEXT NOT NULL
            )
        ''')
        self.db.execute('''
            CREATE TABLE IF NOT EXISTS report_rows (
                number TEXT NOT NULL REFERENCES reports(number),
                position INTEGER NOT NULL,
                slot TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('valid', 'missing')),
                raw TEXT,
                micrometres TEXT,
                source_recording TEXT,
                source_slot TEXT,
                source_generation INTEGER,
                source_request INTEGER,
                source_cell INTEGER,
                PRIMARY KEY (number, position),
                UNIQUE (number, slot)
            )
        ''')

    def save_report(self, worksheet: Worksheet) -> ReportRecord:
        if worksheet.pending_slots:
            raise ValueError('a report cannot contain pending slots')
        section = worksheet.section
        recording = section.recording
        calibration = recording.calibration
        number = f'VR-{section.section_id}'
        self.db.execute('INSERT INTO reports VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                        (number, section.section_id, recording.shift_id, recording.recording_id,
                         str(section.first_slot), str(section.last_slot), str(calibration.zero),
                         str(calibration.ratio), worksheet.totals.valid_count,
                         str(worksheet.totals.micrometres)))
        self.db.executemany('INSERT INTO report_rows VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                            (self._encode_row(number, position, row)
                             for position, row in enumerate(worksheet.rows)))
        return ReportRecord(number, worksheet)

    def find_by_section(self, section_id: str) -> ReportRecord | None:
        header = self.db.execute(
            'SELECT * FROM reports WHERE section_id = ?', (section_id,),
        ).fetchone()
        return self._assemble(header)

    def find_by_number(self, number: str) -> ReportRecord | None:
        header = self.db.execute('SELECT * FROM reports WHERE number = ?', (number,)).fetchone()
        return self._assemble(header)

    def _assemble(self, header: tuple | None) -> ReportRecord | None:
        if header is None:
            return None
        (number, section_id, shift_id, recording_id, first_slot, last_slot,
         zero, ratio, count, total) = header
        calibration = Calibration(int(zero), Fraction(ratio))
        recording = Recording(shift_id, recording_id, calibration)
        section = Section(section_id, recording, int(first_slot), int(last_slot))
        rows = self.db.execute('''
            SELECT slot, state, raw, micrometres, source_recording, source_slot,
                   source_generation, source_request, source_cell
            FROM report_rows WHERE number = ? ORDER BY position
        ''', (number,))
        worksheet = Worksheet(section, tuple(self._decode_row(row) for row in rows),
                              Totals(count, Fraction(total)))
        return ReportRecord(number, worksheet)

    @staticmethod
    def _encode_row(number: str, position: int, row: WorksheetRow) -> tuple:
        source = row.provenance
        origin = (None, None, None, None, None)
        if source is not None:
            origin = (source.recording_id, str(source.slot), source.generation,
                      source.request_id, source.physical_cell)
        return (number, position, str(row.slot), row.state.value,
                None if row.raw is None else str(row.raw),
                None if row.micrometres is None else str(row.micrometres), *origin)

    @staticmethod
    def _decode_row(row: tuple) -> WorksheetRow:
        slot, state, raw, value, recording, source_slot, generation, request_id, cell = row
        source = None
        origin = (recording, source_slot, generation, request_id, cell)
        if any(field is not None for field in origin):
            if any(field is None for field in origin):
                raise ValueError('archive contains incomplete source identity')
            source = Provenance(recording, int(source_slot), generation, request_id, cell)
        return WorksheetRow(int(slot), SlotState(state), None if raw is None else int(raw),
                            None if value is None else Fraction(value), source)
