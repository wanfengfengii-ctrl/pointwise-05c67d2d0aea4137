import sqlite3
from dataclasses import dataclass

from capture.provenance import Provenance


@dataclass(frozen=True)
class Observation:
    raw: int
    provenance: Provenance


class ObservationStore:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def initialize(self) -> None:
        self.db.execute('''
            CREATE TABLE IF NOT EXISTS observations (
                recording_id TEXT NOT NULL REFERENCES shifts(recording_id),
                slot TEXT NOT NULL,
                raw TEXT NOT NULL,
                generation INTEGER NOT NULL,
                request_id INTEGER NOT NULL,
                physical_cell INTEGER NOT NULL,
                PRIMARY KEY (recording_id, slot),
                FOREIGN KEY (generation, request_id)
                    REFERENCES read_attempts(generation, request_id)
            )
        ''')

    def find(self, recording_id: str, slot: int) -> Observation | None:
        row = self.db.execute('''
            SELECT raw, generation, request_id, physical_cell FROM observations
            WHERE recording_id = ? AND slot = ?
        ''', (recording_id, str(slot))).fetchone()
        if row is None:
            return None
        raw, generation, request_id, physical_cell = row
        return Observation(int(raw), Provenance(recording_id, slot, generation,
                                                request_id, physical_cell))

    def save_first(self, observation: Observation) -> Observation:
        source = observation.provenance
        self.db.execute('''
            INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(recording_id, slot) DO NOTHING
        ''', (source.recording_id, str(source.slot), str(observation.raw),
              source.generation, source.request_id, source.physical_cell))
        saved = self.find(source.recording_id, source.slot)
        if saved is None:
            raise RuntimeError('observation write produced no original')
        if saved.raw != observation.raw:
            raise ValueError('different raw codes for the same original slot')
        return saved
