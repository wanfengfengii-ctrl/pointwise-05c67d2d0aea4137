from dataclasses import dataclass
from enum import Enum

from capture.observations import Observation
from capture.outcomes import SlotEvidence
from device.recorder import SlotAvailability


class SlotState(str, Enum):
    PENDING = 'pending'
    VALID = 'valid'
    MISSING = 'missing'


@dataclass(frozen=True)
class SlotAssessment:
    slot: int
    state: SlotState
    observation: Observation | None = None


def assess_slot(slot: int, evidence: SlotEvidence,
                availability: SlotAvailability,
                unresolved: bool = False) -> SlotAssessment:
    if evidence.observation is not None:
        return SlotAssessment(slot, SlotState.VALID, evidence.observation)
    if unresolved:
        return SlotAssessment(slot, SlotState.PENDING)
    if availability is SlotAvailability.OVERWRITTEN and not evidence.live_attempts:
        return SlotAssessment(slot, SlotState.MISSING)
    return SlotAssessment(slot, SlotState.PENDING)
