import sqlite3

from capture.attempts import AttemptLedger
from device.session import Connection, SessionManager


class CaptureSessions:
    def __init__(self, db: sqlite3.Connection, ledger: AttemptLedger,
                 device: SessionManager | None):
        self.db = db
        self.ledger = ledger
        self.device = device

    def initialize(self) -> None:
        self.db.execute('''
            CREATE TABLE IF NOT EXISTS capture_connections (
                generation INTEGER PRIMARY KEY AUTOINCREMENT,
                recording_id TEXT NOT NULL REFERENCES shifts(recording_id),
                state TEXT NOT NULL CHECK (state IN ('active', 'ended'))
            )
        ''')
        self.db.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_connection_per_recording
            ON capture_connections(recording_id) WHERE state = 'active'
        ''')

    def start(self, recording_id: str) -> Connection:
        return self._open(recording_id, None)

    def reopen(self, old: Connection) -> Connection:
        return self._open(old.recording_id, old)

    def _open(self, recording_id: str, old: Connection | None) -> Connection:
        if self.device is None:
            raise ConnectionError('station has no device attached')
        opened = None
        try:
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                if old is not None and self._state(old) != 'ended':
                    raise ValueError('reopen requires a known ended connection')
                if not self.device.recorder.has_recording(recording_id):
                    raise KeyError(recording_id)
                active = self.db.execute('''
                    SELECT 1 FROM capture_connections
                    WHERE recording_id = ? AND state = 'active'
                ''', (recording_id,)).fetchone()
                if active is not None:
                    raise ValueError('recording already has an active connection')
                cursor = self.db.execute('''
                    INSERT INTO capture_connections(recording_id, state) VALUES (?, 'active')
                ''', (recording_id,))
                opened = self.device.connect(recording_id, cursor.lastrowid)
        except BaseException:
            if opened is not None:
                self.device.terminate(opened)
            raise
        return opened

    def terminate(self, connection: Connection) -> None:
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if self._state(connection) is None:
                raise ValueError('unknown station connection')
            self.db.execute('''
                UPDATE capture_connections SET state = 'ended'
                WHERE generation = ? AND recording_id = ?
            ''', (connection.generation, connection.recording_id))
            self.ledger.void_connection(connection)
        if self.device is not None:
            self.device.terminate(connection)

    def is_current(self, connection: Connection) -> bool:
        return (self.device is not None and self.device.is_active(connection)
                and self._state(connection) == 'active')

    def is_ended(self, connection: Connection) -> bool:
        return self._state(connection) == 'ended'

    def _state(self, connection: Connection) -> str | None:
        row = self.db.execute('''
            SELECT state FROM capture_connections WHERE generation = ? AND recording_id = ?
        ''', (connection.generation, connection.recording_id)).fetchone()
        return None if row is None else row[0]
