from dataclasses import dataclass


@dataclass(frozen=True)
class SlotSample:
    slot: int
    raw: int
    physical_cell: int


class Ring:
    def __init__(self, capacity: int, first_slot: int):
        self.validate_geometry(capacity, first_slot)
        self.first_slot = first_slot
        self._next_slot = first_slot
        self._cells: list[SlotSample | None] = [None] * capacity
        self._overwritten_through: int | None = None

    @staticmethod
    def validate_geometry(capacity: int, first_slot: int) -> None:
        if type(capacity) is not int or capacity <= 0:
            raise ValueError('capacity must be a positive integer')
        if type(first_slot) is not int or first_slot < 0:
            raise ValueError('first slot must be a nonnegative integer')

    def _cell_for(self, slot: int) -> int:
        return (slot - self.first_slot) % len(self._cells)

    def append(self, raw: int) -> SlotSample:
        if type(raw) is not int:
            raise ValueError('the recorder requires an integer raw code')
        slot = self._next_slot
        cell = self._cell_for(slot)
        previous = self._cells[cell]
        if previous is not None:
            self._overwritten_through = previous.slot
        sample = SlotSample(slot, raw, cell)
        self._cells[cell] = sample
        self._next_slot += 1
        return sample

    def sample_at(self, slot: int) -> SlotSample | None:
        if type(slot) is not int or slot < 0:
            raise ValueError('slot must be a nonnegative integer')
        if not self.first_slot <= slot < self._next_slot:
            return None
        sample = self._cells[self._cell_for(slot)]
        if sample is not None and sample.slot == slot:
            return sample
        return None

    def frontier(self) -> int | None:
        return self._overwritten_through
