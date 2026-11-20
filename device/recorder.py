from enum import Enum

from device.ring import Ring, SlotSample
from shift.registry import Recording


class SlotAvailability(str, Enum):
    READABLE = 'readable'
    OVERWRITTEN = 'overwritten'
    UNKNOWN = 'unknown'


class Recorder:
    def __init__(self, capacity: int, first_slot: int):
        Ring.validate_geometry(capacity, first_slot)
        self._capacity = capacity
        self._first_slot = first_slot
        self._rings: dict[str, Ring] = {}
        self._ended: set[str] = set()

    def open_recording(self, recording: Recording) -> None:
        if recording.recording_id in self._rings:
            raise ValueError('device recording already exists')
        self._rings[recording.recording_id] = Ring(self._capacity, self._first_slot)

    def has_recording(self, recording_id: str) -> bool:
        return recording_id in self._rings

    def record(self, recording_id: str, raw: int) -> int:
        ring = self._rings[recording_id]
        if recording_id in self._ended:
            raise ValueError('recording has ended')
        return ring.append(raw).slot

    def end_recording(self, recording_id: str) -> None:
        if not self.has_recording(recording_id):
            raise KeyError(recording_id)
        self._ended.add(recording_id)

    def sample_at(self, recording_id: str, slot: int) -> SlotSample | None:
        ring = self._rings.get(recording_id)
        return None if ring is None else ring.sample_at(slot)

    def slot_availability(self, recording_id: str, slot: int) -> SlotAvailability:
        ring = self._rings.get(recording_id)
        if ring is None:
            return SlotAvailability.UNKNOWN
        if ring.sample_at(slot) is not None:
            return SlotAvailability.READABLE
        frontier = ring.frontier()
        if frontier is not None and ring.first_slot <= slot <= frontier:
            return SlotAvailability.OVERWRITTEN
        return SlotAvailability.UNKNOWN
