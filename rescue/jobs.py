import json
import sqlite3
from dataclasses import dataclass

from shift.config import require_identifier
from shift.registry import ShiftRegistry


@dataclass(frozen=True)
class RescueJob:
    job_id: str
    recording_id: str
    weights: dict[str, int]
    budget: int


@dataclass(frozen=True)
class RescueRead:
    generation: int
    request_id: int
    slot: int
    physical_cell: int


@dataclass(frozen=True)
class DispatchMarker:
    dispatch_id: int
    job_id: str
    slot: int
    generation: int


def normalize_weights(weights: object) -> dict[str, int]:
    if not isinstance(weights, dict):
        raise ValueError('weights must be a mapping of section IDs to weights')
    normalized: dict[str, int] = {}
    for section_id, weight in weights.items():
        require_identifier(section_id, 'section_id')
        if type(weight) is not int or weight <= 0:
            raise ValueError('weights must be positive integers')
        normalized[section_id] = weight
    return dict(sorted(normalized.items()))


def normalize_budget(budget: object) -> int:
    if type(budget) is not int or budget < 0:
        raise ValueError('budget must be a nonnegative integer')
    return budget


class RescueStore:
    def __init__(self, db: sqlite3.Connection, shifts: ShiftRegistry):
        self.db = db
        self.shifts = shifts

    def initialize(self) -> None:
        self.db.execute('''
            CREATE TABLE IF NOT EXISTS rescue_jobs (
                job_id TEXT NOT NULL PRIMARY KEY,
                recording_id TEXT NOT NULL UNIQUE REFERENCES shifts(recording_id),
                weights TEXT NOT NULL,
                budget INTEGER NOT NULL CHECK (budget >= 0)
            )
        ''')
        self.db.execute('''
            CREATE TABLE IF NOT EXISTS rescue_reads (
                job_id TEXT NOT NULL REFERENCES rescue_jobs(job_id),
                generation INTEGER NOT NULL,
                request_id INTEGER NOT NULL,
                slot TEXT NOT NULL,
                physical_cell INTEGER NOT NULL,
                PRIMARY KEY (job_id, generation, request_id),
                FOREIGN KEY (generation, request_id)
                    REFERENCES read_attempts(generation, request_id)
            )
        ''')
        self.db.execute('''
            CREATE TABLE IF NOT EXISTS rescue_dispatches (
                dispatch_id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES rescue_jobs(job_id),
                slot TEXT NOT NULL,
                generation INTEGER NOT NULL
            )
        ''')
        self.db.execute('''
            CREATE INDEX IF NOT EXISTS rescue_dispatches_job
            ON rescue_dispatches(job_id)
        ''')

    # -- jobs ---------------------------------------------------------------

    def get_job(self, job_id: str) -> RescueJob | None:
        row = self.db.execute(
            'SELECT job_id, recording_id, weights, budget FROM rescue_jobs WHERE job_id = ?',
            (job_id,),
        ).fetchone()
        return None if row is None else self._decode(row)

    def get_by_recording(self, recording_id: str) -> RescueJob | None:
        row = self.db.execute(
            'SELECT job_id, recording_id, weights, budget FROM rescue_jobs WHERE recording_id = ?',
            (recording_id,),
        ).fetchone()
        return None if row is None else self._decode(row)

    def create_job(self, job_id: str, recording_id: str,
                   weights: dict[str, int], budget: int) -> RescueJob:
        encoded = json.dumps(weights, sort_keys=True, separators=(',', ':'))
        self.db.execute('INSERT INTO rescue_jobs VALUES (?, ?, ?, ?)',
                        (job_id, recording_id, encoded, budget))
        return RescueJob(job_id, recording_id, dict(weights), budget)

    # -- accepted reads (one deduction each) --------------------------------

    def add_read(self, job_id: str, generation: int, request_id: int,
                 slot: int, physical_cell: int) -> None:
        self.db.execute('''
            INSERT OR IGNORE INTO rescue_reads
            VALUES (?, ?, ?, ?, ?)
        ''', (job_id, generation, request_id, str(slot), physical_cell))

    def has_read(self, job_id: str, generation: int, request_id: int) -> bool:
        row = self.db.execute('''
            SELECT 1 FROM rescue_reads
            WHERE job_id = ? AND generation = ? AND request_id = ?
        ''', (job_id, generation, request_id)).fetchone()
        return row is not None

    def read_count(self, job_id: str) -> int:
        row = self.db.execute(
            'SELECT COUNT(*) FROM rescue_reads WHERE job_id = ?', (job_id,),
        ).fetchone()
        return int(row[0])

    def reads(self, job_id: str) -> tuple[RescueRead, ...]:
        rows = self.db.execute('''
            SELECT generation, request_id, slot, physical_cell
            FROM rescue_reads WHERE job_id = ? ORDER BY generation, request_id
        ''', (job_id,))
        return tuple(RescueRead(generation, request_id, int(slot), physical_cell)
                     for generation, request_id, slot, physical_cell in rows)

    # -- dispatch markers ---------------------------------------------------

    def add_dispatch(self, job_id: str, slot: int, generation: int) -> DispatchMarker:
        cursor = self.db.execute(
            'INSERT INTO rescue_dispatches(job_id, slot, generation) VALUES (?, ?, ?)',
            (job_id, str(slot), generation),
        )
        return DispatchMarker(cursor.lastrowid, job_id, slot, generation)

    def drop_dispatch(self, dispatch_id: int) -> None:
        self.db.execute('DELETE FROM rescue_dispatches WHERE dispatch_id = ?',
                        (dispatch_id,))

    def requesting(self, job_id: str | None = None) -> tuple[DispatchMarker, ...]:
        if job_id is None:
            rows = self.db.execute('''
                SELECT dispatch_id, job_id, slot, generation
                FROM rescue_dispatches ORDER BY dispatch_id
            ''')
        else:
            rows = self.db.execute('''
                SELECT dispatch_id, job_id, slot, generation
                FROM rescue_dispatches WHERE job_id = ? ORDER BY dispatch_id
            ''', (job_id,))
        return tuple(DispatchMarker(dispatch_id, job_id_, int(slot), generation)
                     for dispatch_id, job_id_, slot, generation in rows)

    def unresolved_slots(self, recording_id: str) -> frozenset[int]:
        rows = self.db.execute('''
            SELECT d.slot FROM rescue_dispatches d
            JOIN rescue_jobs j ON j.job_id = d.job_id
            WHERE j.recording_id = ?
        ''', (recording_id,))
        return frozenset(int(slot) for slot, in rows)

    @staticmethod
    def _decode(row: tuple) -> RescueJob:
        job_id, recording_id, encoded, budget = row
        weights = {str(key): int(value) for key, value in json.loads(encoded).items()}
        return RescueJob(job_id, recording_id, weights, int(budget))
