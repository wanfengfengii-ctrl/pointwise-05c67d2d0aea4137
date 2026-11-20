from dataclasses import dataclass
from itertools import count

from device.events import EventQueue
from device.recorder import Recorder, SlotAvailability
from device.ring import SlotSample
from device.session import Connection, SessionManager


@dataclass(frozen=True)
class ReadTicket:
    connection: Connection
    request_id: int
    slot: int
    physical_cell: int

    @property
    def recording_id(self) -> str:
        return self.connection.recording_id


@dataclass(frozen=True)
class ReadResult:
    ticket: ReadTicket
    raw: int | None


class ReadPort:
    def __init__(self, recorder: Recorder, sessions: SessionManager, events: EventQueue):
        self.recorder = recorder
        self.sessions = sessions
        self.events = events
        self._request_ids = count(1)
        self._accepted: dict[ReadTicket, SlotSample] = {}

    def request_read(self, connection: Connection, recording_id: str,
                     slot: int) -> ReadTicket | SlotAvailability:
        if connection.recording_id != recording_id or not self.sessions.is_active(connection):
            raise ValueError('device connection does not authorize this recording')
        sample = self.recorder.sample_at(recording_id, slot)
        if sample is None:
            return self.recorder.slot_availability(recording_id, slot)
        ticket = ReadTicket(connection, next(self._request_ids), sample.slot, sample.physical_cell)
        self._accepted[ticket] = sample
        return ticket

    def deliver_result(self, ticket: ReadTicket, *, succeeded: bool = True,
                       delay: int = 0) -> int:
        sample = self._accepted.get(ticket)
        if sample is None:
            raise ValueError('device has no such accepted read')
        result = ReadResult(ticket, sample.raw if succeeded else None)
        return self.events.schedule(lambda: result, delay=delay)
