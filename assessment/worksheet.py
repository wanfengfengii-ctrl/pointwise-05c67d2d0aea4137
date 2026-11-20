from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction

from assessment.convert import to_micrometres
from assessment.coverage import SlotState, assess_slot
from assessment.totals import Totals, summarize_readings
from capture.outcomes import SlotEvidence
from capture.provenance import Provenance
from device.recorder import SlotAvailability
from shift.sections import Section


@dataclass(frozen=True)
class WorksheetRow:
    slot: int
    state: SlotState
    raw: int | None = None
    micrometres: Fraction | None = None
    provenance: Provenance | None = None


@dataclass(frozen=True)
class Worksheet:
    section: Section
    rows: tuple[WorksheetRow, ...]
    totals: Totals

    @property
    def pending_slots(self) -> tuple[int, ...]:
        return tuple(row.slot for row in self.rows if row.state is SlotState.PENDING)


def build_worksheet(section: Section, evidence_at: Callable[[str, int], SlotEvidence],
                    availability_at: Callable[[str, int], SlotAvailability]) -> Worksheet:
    rows = []
    recording = section.recording
    for slot in range(section.first_slot, section.last_slot + 1):
        evidence = evidence_at(recording.recording_id, slot)
        availability = availability_at(recording.recording_id, slot)
        assessment = assess_slot(slot, evidence, availability)
        observation = assessment.observation
        if observation is None:
            rows.append(WorksheetRow(assessment.slot, assessment.state))
        else:
            value = to_micrometres(observation.raw, recording.calibration)
            rows.append(WorksheetRow(assessment.slot, assessment.state, observation.raw,
                                     value, observation.provenance))
    return Worksheet(section, tuple(rows), summarize_readings(row.micrometres for row in rows))
