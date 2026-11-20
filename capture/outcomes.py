from dataclasses import dataclass

from capture.attempts import Attempt, AttemptLedger
from capture.observations import Observation, ObservationStore
from capture.reopen import CaptureSessions


@dataclass(frozen=True)
class SlotEvidence:
    observation: Observation | None
    live_attempts: tuple[Attempt, ...]


def read_slot_evidence(recording_id: str, slot: int, *, observations: ObservationStore,
                       ledger: AttemptLedger, sessions: CaptureSessions) -> SlotEvidence:
    """Read using the caller's station transaction and serial device event boundary."""
    observation = observations.find(recording_id, slot)
    live = tuple(attempt for attempt in ledger.active_for_slot(recording_id, slot)
                 if sessions.is_current(attempt.ticket.connection))
    return SlotEvidence(observation, live)
