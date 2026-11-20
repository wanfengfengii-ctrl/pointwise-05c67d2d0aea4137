from dataclasses import dataclass

from capture.attempts import Attempt
from device.read_port import ReadResult


@dataclass(frozen=True)
class Provenance:
    recording_id: str
    slot: int
    generation: int
    request_id: int
    physical_cell: int


def build_provenance(attempt: Attempt, result: ReadResult) -> Provenance:
    ticket = attempt.ticket
    if result.ticket != ticket or type(result.raw) is not int:
        raise ValueError('successful reply does not match its registered read')
    return Provenance(ticket.recording_id, ticket.slot, ticket.connection.generation,
                      ticket.request_id, ticket.physical_cell)
