import sqlite3
from dataclasses import dataclass
from enum import Enum

from device.read_port import ReadTicket
from device.session import Connection


class AttemptState(str, Enum):
    ACTIVE = 'active'
    SUCCEEDED = 'succeeded'
    FAILED = 'failed'
    VOID = 'void'


@dataclass(frozen=True)
class Attempt:
    ticket: ReadTicket
    state: AttemptState


class AttemptLedger:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def initialize(self) -> None:
        self.db.execute('''
            CREATE TABLE IF NOT EXISTS read_attempts (
                generation INTEGER NOT NULL REFERENCES capture_connections(generation),
                request_id INTEGER NOT NULL,
                recording_id TEXT NOT NULL REFERENCES shifts(recording_id),
                slot TEXT NOT NULL,
                physical_cell INTEGER NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('active', 'succeeded', 'failed', 'void')),
                PRIMARY KEY (generation, request_id)
            )
        ''')
        self.db.execute('''
            CREATE INDEX IF NOT EXISTS read_attempts_slot
            ON read_attempts(recording_id, slot, state)
        ''')

    def register(self, ticket: ReadTicket) -> Attempt:
        self.db.execute(
            'INSERT INTO read_attempts VALUES (?, ?, ?, ?, ?, ?)',
            (ticket.connection.generation, ticket.request_id, ticket.recording_id,
             str(ticket.slot), ticket.physical_cell, AttemptState.ACTIVE.value),
        )
        return Attempt(ticket, AttemptState.ACTIVE)

    def get_attempt(self, generation: int, request_id: int) -> Attempt | None:
        row = self.db.execute('''
            SELECT * FROM read_attempts WHERE generation = ? AND request_id = ?
        ''', (generation, request_id)).fetchone()
        return None if row is None else self._decode(row)

    def active_for_slot(self, recording_id: str, slot: int) -> tuple[Attempt, ...]:
        rows = self.db.execute('''
            SELECT * FROM read_attempts
            WHERE recording_id = ? AND slot = ? AND state = 'active'
            ORDER BY generation, request_id
        ''', (recording_id, str(slot)))
        return tuple(self._decode(row) for row in rows)

    def settle(self, ticket: ReadTicket, state: AttemptState) -> None:
        if state not in (AttemptState.SUCCEEDED, AttemptState.FAILED):
            raise ValueError('a reply must settle as success or failure')
        cursor = self.db.execute('''
            UPDATE read_attempts SET state = ?
            WHERE generation = ? AND request_id = ? AND state = 'active'
        ''', (state.value, ticket.connection.generation, ticket.request_id))
        if cursor.rowcount != 1:
            raise RuntimeError('read attempt was not active at settlement')

    def void_connection(self, connection: Connection) -> None:
        self.db.execute('''
            UPDATE read_attempts SET state = 'void'
            WHERE generation = ? AND recording_id = ? AND state = 'active'
        ''', (connection.generation, connection.recording_id))

    @staticmethod
    def _decode(row: tuple) -> Attempt:
        generation, request_id, recording_id, slot, physical_cell, state = row
        ticket = ReadTicket(Connection(recording_id, generation), request_id,
                            int(slot), physical_cell)
        return Attempt(ticket, AttemptState(state))
