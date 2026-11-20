import sqlite3
from dataclasses import dataclass
from enum import Enum

from capture.attempts import Attempt, AttemptLedger, AttemptState
from capture.observations import Observation, ObservationStore
from capture.provenance import build_provenance
from capture.reopen import CaptureSessions
from device.read_port import ReadPort, ReadResult, ReadTicket, SlotAvailability
from device.session import Connection


class Completion(str, Enum):
    ACCEPTED = 'accepted'
    FAILED = 'failed'
    DUPLICATE = 'duplicate'
    STALE = 'stale'
    REJECTED = 'rejected'


@dataclass(frozen=True)
class PreparedRead:
    result: ReadResult


class CaptureAdmission:
    def __init__(self, db: sqlite3.Connection, port: ReadPort | None,
                 sessions: CaptureSessions, ledger: AttemptLedger,
                 observations: ObservationStore):
        self.db = db
        self.port = port
        self.sessions = sessions
        self.ledger = ledger
        self.observations = observations

    def acquire(self, connection: Connection, recording_id: str,
                slot: int) -> ReadTicket | Observation | SlotAvailability:
        if type(slot) is not int or slot < 0:
            raise ValueError('slot must be a nonnegative integer')
        if self.port is None or connection.recording_id != recording_id:
            raise ValueError('no device connection for this recording')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if not self.sessions.is_current(connection):
                raise ValueError('connection is no longer current')
            issued = self.port.request_read(connection, recording_id, slot)
            if isinstance(issued, ReadTicket):
                self.ledger.register(issued)
                return issued
            saved = self.observations.find(recording_id, slot)
            return saved if saved is not None else issued

    def prepare_read(self, result: ReadResult) -> PreparedRead | Completion:
        eligible = self._eligible(result)
        return PreparedRead(result) if isinstance(eligible, Attempt) else eligible

    def complete_read(self, reply: ReadResult | PreparedRead) -> Completion:
        result = reply.result if isinstance(reply, PreparedRead) else reply
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            eligible = self._eligible(result)
            if isinstance(eligible, Completion):
                return eligible
            if result.raw is None:
                self.ledger.settle(eligible.ticket, AttemptState.FAILED)
                return Completion.FAILED
            source = build_provenance(eligible, result)
            self.observations.save_first(Observation(result.raw, source))
            self.ledger.settle(eligible.ticket, AttemptState.SUCCEEDED)
            return Completion.ACCEPTED

    def _eligible(self, result: ReadResult) -> Attempt | Completion:
        ticket = result.ticket
        attempt = self.ledger.get_attempt(ticket.connection.generation, ticket.request_id)
        if attempt is None or attempt.ticket != ticket:
            return Completion.REJECTED
        if result.raw is not None and type(result.raw) is not int:
            return Completion.REJECTED
        if attempt.state is AttemptState.VOID or not self.sessions.is_current(ticket.connection):
            return Completion.STALE
        if attempt.state is not AttemptState.ACTIVE:
            return Completion.DUPLICATE
        return attempt
