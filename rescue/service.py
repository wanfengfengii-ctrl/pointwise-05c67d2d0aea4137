from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from archive.records import ReportStore
from capture.attempts import AttemptLedger, AttemptState
from capture.observations import ObservationStore
from capture.reopen import CaptureSessions
from device.read_port import ReadTicket
from device.recorder import SlotAvailability
from rescue.jobs import (DispatchMarker, RescueJob, RescueStore,
                         normalize_budget, normalize_weights)
from shift.config import require_identifier
from shift.registry import ShiftRegistry
from shift.sections import SectionRegistry


class RescueService:
    def __init__(self, db: sqlite3.Connection, device, sessions: CaptureSessions,
                 ledger: AttemptLedger, observations: ObservationStore,
                 reports: ReportStore, sections: SectionRegistry,
                 shifts: ShiftRegistry, store: RescueStore):
        self.db = db
        self.device = device
        self.sessions = sessions
        self.ledger = ledger
        self.observations = observations
        self.reports = reports
        self.sections = sections
        self.shifts = shifts
        self.store = store

    # -- public entry points -------------------------------------------------

    def register(self, job_id: str, recording_id: str, weights: object,
                 budget: object) -> dict:
        require_identifier(job_id, 'job_id')
        require_identifier(recording_id, 'recording_id')
        normalized = normalize_weights(weights)
        normalized_budget = normalize_budget(budget)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            existing = self.store.get_job(job_id)
            if existing is not None:
                if (existing.recording_id != recording_id
                        or existing.weights != normalized
                        or existing.budget != normalized_budget):
                    raise ValueError('job ID already has a different rescue plan')
                job = existing
            else:
                job = self._create(job_id, recording_id, normalized, normalized_budget)
        return self.status(job.job_id)

    def advance(self, job_id: str, connection) -> dict:
        job = self._require_job(job_id)
        if self.device is None:
            raise ConnectionError('station has no device attached')
        if connection.recording_id != job.recording_id:
            raise ValueError('connection does not belong to the job recording')
        if not self.sessions.is_current(connection):
            raise ValueError('connection is no longer current')
        self._reconcile(job)
        state = self._status(job)
        if state['pending'] is not None or state['remaining'] <= 0:
            return state
        _, missing = self._select(job, state['remaining'])
        if not missing:
            return state
        self._dispatch(job, connection, min(missing))
        return self._status(job)

    def status(self, job_id: str) -> dict:
        job = self._require_job(job_id)
        self._reconcile(job)
        return self._status(job)

    def unresolved_slots(self, recording_id: str) -> frozenset[int]:
        """Slots whose rescue acceptance/result is still unresolved."""
        job = self.store.get_by_recording(recording_id)
        if job is None:
            return frozenset()
        self._reconcile(job)
        unresolved = set(self.store.unresolved_slots(recording_id))
        for read in self.store.reads(job.job_id):
            attempt = self.ledger.get_attempt(read.generation, read.request_id)
            if attempt is not None and attempt.state is AttemptState.ACTIVE:
                # Canceled connections void their attempts, so a merely
                # detached station still keeps this slot waiting.
                unresolved.add(read.slot)
        return frozenset(unresolved)

    def reconcile_recording(self, recording_id: str) -> None:
        job = self.store.get_by_recording(recording_id)
        if job is not None:
            self._reconcile(job)

    def note_termination(self, connection) -> None:
        """A terminated connection can never deliver a result for its tickets."""
        job = self.store.get_by_recording(connection.recording_id)
        if job is None:
            return
        self._reconcile(job)
        with self._transaction():
            for marker in self.store.requesting(job.job_id):
                if marker.generation == connection.generation:
                    self.store.drop_dispatch(marker.dispatch_id)

    # -- registration -------------------------------------------------------

    def _create(self, job_id: str, recording_id: str, weights: dict[str, int],
                budget: int) -> RescueJob:
        if self.shifts.get_recording(recording_id) is None:
            raise KeyError(recording_id)
        if self.store.get_by_recording(recording_id) is not None:
            raise ValueError('recording already has a rescue job')
        for section_id in weights:
            section = self.sections.get_section(section_id)
            if section is None:
                raise KeyError(section_id)
            if section.recording.recording_id != recording_id:
                raise ValueError('section is bound to another recording')
            if self.reports.find_by_section(section_id) is not None:
                raise ValueError('section is already issued')
        return self.store.create_job(job_id, recording_id, weights, budget)

    def _require_job(self, job_id: str) -> RescueJob:
        require_identifier(job_id, 'job_id')
        job = self.store.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    # -- reconciliation of device acceptance ---------------------------------

    def _accepted_job_tickets(self, job: RescueJob) -> tuple[ReadTicket, ...]:
        if self.device is None:
            return ()
        return self.device.port.accepted_tickets(job.recording_id, purpose=job.job_id)

    @contextmanager
    def _transaction(self):
        if self.db.in_transaction:
            yield
        else:
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                yield

    def _reconcile(self, job: RescueJob) -> None:
        """Fold device-accepted tickets into the station books before any decision.

        A ticket the device accepted survives a station write/commit failure and
        a lost station handle: re-attaching the same device re-admits the same
        ticket and its deduction even when the original slot is overwritten.
        """
        if self.device is None:
            return
        accepted = self._accepted_job_tickets(job)
        markers = self.store.requesting(job.job_id)
        with self._transaction():
            known = {(read.generation, read.request_id)
                     for read in self.store.reads(job.job_id)}
            for ticket in accepted:
                key = (ticket.connection.generation, ticket.request_id)
                self._register_attempt(ticket)
                if key not in known:
                    self.store.add_read(job.job_id, ticket.connection.generation,
                                        ticket.request_id, ticket.slot,
                                        ticket.physical_cell)
                    known.add(key)
            for marker in markers:
                # The device accepted this dispatch (it is in `accepted` and
                # now on the books) or the device never accepted it; either way
                # the marker has served its purpose.
                self.store.drop_dispatch(marker.dispatch_id)

    def _register_attempt(self, ticket: ReadTicket) -> None:
        key = (ticket.connection.generation, ticket.request_id)
        if self.ledger.get_attempt(*key) is not None:
            return
        self.ledger.register(ticket)
        if not self.sessions.is_current(ticket.connection):
            self.ledger.void_connection(ticket.connection)

    def _used(self, job: RescueJob) -> int:
        keys = {(read.generation, read.request_id)
                for read in self.store.reads(job.job_id)}
        keys.update((ticket.connection.generation, ticket.request_id)
                    for ticket in self._accepted_job_tickets(job))
        return len(keys)

    # -- selection -----------------------------------------------------------

    def _pending_ticket(self, job: RescueJob) -> ReadTicket | None:
        best: ReadTicket | None = None
        for read in self.store.reads(job.job_id):
            attempt = self.ledger.get_attempt(read.generation, read.request_id)
            if attempt is None or attempt.state is not AttemptState.ACTIVE:
                continue
            if not self.sessions.is_current(attempt.ticket.connection):
                continue
            ticket = attempt.ticket
            if best is None or (ticket.connection.generation, ticket.request_id) > (
                    best.connection.generation, best.request_id):
                best = ticket
        return best

    def _status(self, job: RescueJob) -> dict:
        used = self._used(job)
        remaining = max(job.budget - used, 0)
        targets, _ = self._select(job, remaining)
        completed: set[str] = set()
        for section_id in job.weights:
            section = self.sections.get_section(section_id)
            if section is None:
                continue
            if all(self.observations.find(job.recording_id, slot) is not None
                   for slot in range(section.first_slot, section.last_slot + 1)):
                completed.add(section_id)
        return {'used': used, 'remaining': remaining,
                'pending': self._pending_ticket(job),
                'targets': targets, 'completed': completed}

    def _select(self, job: RescueJob, remaining: int):
        completed: set[str] = set()
        candidates: list[tuple[str, int, frozenset[int]]] = []
        for section_id, weight in job.weights.items():
            section = self.sections.get_section(section_id)
            if section is None:
                continue
            missing: set[int] = set()
            blocked = False
            for slot in range(section.first_slot, section.last_slot + 1):
                if self.observations.find(job.recording_id, slot) is not None:
                    continue
                missing.add(slot)
                if self._availability(job.recording_id, slot) is not SlotAvailability.READABLE:
                    blocked = True
            if not missing:
                completed.add(section_id)
            elif not blocked and len(missing) <= remaining:
                candidates.append((section_id, weight, frozenset(missing)))
        if remaining <= 0 or not candidates:
            return set(), frozenset()
        chosen, union = self._best_subset(candidates, remaining)
        return set(chosen), union

    @staticmethod
    def _best_subset(candidates, remaining):
        ordered = sorted(candidates, key=lambda item: (-item[1], item[0]))
        weights = [item[1] for item in ordered]
        total = len(ordered)
        suffix = [0] * (total + 1)
        for index in range(total - 1, -1, -1):
            suffix[index] = suffix[index + 1] + weights[index]
        best = {'weight': 0, 'cost': 0, 'ids': ()}
        chosen: list[str] = []

        def visit(index: int, union: frozenset[int], weight: int, cost: int) -> None:
            if weight + suffix[index] < best['weight']:
                return
            if index == total:
                ids = tuple(sorted(chosen))
                if (weight > best['weight']
                        or (weight == best['weight'] and cost < best['cost'])
                        or (weight == best['weight'] and cost == best['cost']
                            and ids < best['ids'])):
                    best.update(weight=weight, cost=cost, ids=ids)
                return
            section_id, section_weight, section_missing = ordered[index]
            merged = union | section_missing
            if len(merged) <= remaining:
                chosen.append(section_id)
                visit(index + 1, merged, weight + section_weight, len(merged))
                chosen.pop()
            visit(index + 1, union, weight, cost)

        visit(0, frozenset(), 0, 0)
        chosen_ids = best['ids']
        if not chosen_ids:
            return set(), frozenset()
        union: set[int] = set()
        for section_id, _, section_missing in ordered:
            if section_id in chosen_ids:
                union |= section_missing
        return set(chosen_ids), frozenset(sorted(union))

    def _availability(self, recording_id: str, slot: int) -> SlotAvailability:
        if self.device is None:
            return SlotAvailability.UNKNOWN
        return self.device.recorder.slot_availability(recording_id, slot)

    # -- dispatch ------------------------------------------------------------

    def _dispatch(self, job: RescueJob, connection, slot: int) -> None:
        device = self.device
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            marker = self.store.add_dispatch(job.job_id, slot, connection.generation)
        try:
            issued = device.port.request_read(connection, job.recording_id, slot,
                                              purpose=job.job_id)
        except BaseException:
            self._drop_marker(marker)
            raise
        if not isinstance(issued, ReadTicket):
            self._drop_marker(marker)
            return
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self._register_attempt(issued)
            self.store.add_read(job.job_id, issued.connection.generation,
                                issued.request_id, issued.slot, issued.physical_cell)
            self.store.drop_dispatch(marker.dispatch_id)

    def _drop_marker(self, marker: DispatchMarker) -> None:
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self.store.drop_dispatch(marker.dispatch_id)
