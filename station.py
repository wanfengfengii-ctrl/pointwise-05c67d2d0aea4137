import argparse
import json
import sqlite3
from collections.abc import Callable
from dataclasses import asdict
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory

from archive.publish import PendingIssue, Publisher
from archive.receipts import ReceiptStore
from archive.reconcile import IssuedReport
from archive.records import ReportStore
from archive.retrieve import Archive
from capture.admission import CaptureAdmission, Completion, PreparedRead
from capture.attempts import AttemptLedger
from capture.observations import Observation, ObservationStore
from capture.outcomes import read_slot_evidence
from capture.reopen import CaptureSessions
from device.events import EventQueue
from device.read_port import ReadPort, ReadResult, ReadTicket
from device.recorder import Recorder, SlotAvailability
from device.session import Connection, SessionManager
from shift.config import ShiftConfig
from shift.registry import Recording, ShiftRegistry
from shift.sections import Section, SectionRegistry
from rescue.jobs import RescueStore
from rescue.service import RescueService


class LocalDevice:
    def __init__(self, capacity: int, first_slot: int = 0):
        self.recorder = Recorder(capacity, first_slot)
        self.sessions = SessionManager(self.recorder)
        self.events = EventQueue()
        self.port = ReadPort(self.recorder, self.sessions, self.events)


class Station:
    def __init__(self, path: str | Path, device: LocalDevice | None = None):
        self.device = device
        self.db = sqlite3.connect(path, isolation_level=None)
        try:
            self.db.execute('PRAGMA foreign_keys = ON')
            self._initialize()
        except BaseException:
            self.db.close()
            raise

    def _initialize(self) -> None:
        db = self.db
        self.shifts = ShiftRegistry(db)
        self.sections = SectionRegistry(db, self.shifts)
        self.attempts = AttemptLedger(db)
        self.sessions = CaptureSessions(db, self.attempts,
                                        None if self.device is None else self.device.sessions)
        self.observations = ObservationStore(db)
        self.reports = ReportStore(db)
        self.receipts = ReceiptStore(db)
        self.rescue_store = RescueStore(db, self.shifts)
        with db:
            db.execute('BEGIN IMMEDIATE')
            for owner in (self.shifts, self.sections, self.sessions, self.attempts,
                          self.observations, self.reports, self.receipts):
                owner.initialize()
            self.rescue_store.initialize()
        evidence_at = partial(read_slot_evidence, observations=self.observations,
                              ledger=self.attempts, sessions=self.sessions)
        self.capture = CaptureAdmission(db, None if self.device is None else self.device.port,
                                        self.sessions, self.attempts, self.observations)
        self.rescue = RescueService(db, self.device, self.sessions, self.attempts,
                                    self.observations, self.reports, self.sections,
                                    self.shifts, self.rescue_store)
        self.publisher = Publisher(db, self.sections, self.reports, self.receipts,
                                   evidence_at, self._availability,
                                   pending_at=self._rescue_pending)
        self.archive = Archive(self.reports, self.receipts)

    def _device(self) -> LocalDevice:
        if self.device is None:
            raise ConnectionError('station has no device attached')
        return self.device

    def _availability(self, recording_id: str, slot: int) -> SlotAvailability:
        if self.device is None:
            return SlotAvailability.UNKNOWN
        return self.device.recorder.slot_availability(recording_id, slot)

    def _rescue_pending(self, recording_id: str) -> frozenset[int]:
        return self.rescue.unresolved_slots(recording_id)

    def __enter__(self) -> 'Station':
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def close(self) -> None:
        self.db.close()

    def open_shift(self, shift_id: str, recording_id: str, zero: int,
                   numerator: int, denominator: int) -> Recording:
        recorder = self._device().recorder
        prior = self.shifts.get_recording(recording_id)
        if prior is None and recorder.has_recording(recording_id):
            raise ValueError('the attached device already has this recording')
        recording = self.shifts.open_shift(ShiftConfig(shift_id, recording_id, zero,
                                                     numerator, denominator))
        if prior is None:
            recorder.open_recording(recording)
        return recording

    def define_section(self, section_id: str, shift_id: str,
                       first_slot: int, last_slot: int) -> Section:
        return self.sections.define_section(section_id, shift_id, first_slot, last_slot)

    def record(self, recording_id: str, raw: int) -> int:
        return self._device().recorder.record(recording_id, raw)

    def end_recording(self, recording_id: str) -> None:
        self._device().recorder.end_recording(recording_id)

    def start(self, recording_id: str) -> Connection:
        return self.sessions.start(recording_id)

    def terminate(self, connection: Connection) -> None:
        self.sessions.terminate(connection)
        self.rescue.note_termination(connection)

    def reopen(self, connection: Connection) -> Connection:
        return self.sessions.reopen(connection)

    def acquire(self, connection: Connection, recording_id: str,
                slot: int) -> ReadTicket | Observation | SlotAvailability:
        return self.capture.acquire(connection, recording_id, slot)

    def queue_reply(self, ticket: ReadTicket, *, succeeded: bool = True, delay: int = 0) -> int:
        return self._device().port.deliver_result(ticket, succeeded=succeeded, delay=delay)

    def prepare_reply(self, result: ReadResult) -> PreparedRead | Completion:
        self.rescue.reconcile_recording(result.ticket.recording_id)
        return self.capture.prepare_read(result)

    def complete_reply(self, reply: ReadResult | PreparedRead, *,
                       acknowledge: Callable[[Completion], object] | None = None) -> Completion:
        events = self._device().events if acknowledge is not None else None
        ticket = reply.result.ticket if isinstance(reply, PreparedRead) else reply.ticket
        self.rescue.reconcile_recording(ticket.recording_id)
        result = self.capture.complete_read(reply)
        if events is not None:
            events.schedule(partial(acknowledge, result))
        return result

    def find_observation(self, recording_id: str, slot: int) -> Observation | None:
        return self.observations.find(recording_id, slot)

    def issue(self, section_id: str) -> IssuedReport | PendingIssue:
        return self.publisher.issue(section_id)

    def rescue_status(self, job_id: str) -> dict:
        return self.rescue.status(job_id)

    def register_rescue(self, job_id: str, recording_id: str, weights,
                        budget) -> dict:
        return self.rescue.register(job_id, recording_id, weights, budget)

    def advance_rescue(self, job_id: str, connection) -> dict:
        return self.rescue.advance(job_id, connection)

    def retrieve(self, number: str) -> IssuedReport | None:
        return self.archive.retrieve(number)


def _describe_report(issued: IssuedReport) -> dict:
    worksheet = issued.report.worksheet
    return {
        'number': issued.number,
        'receipt': issued.receipt.token,
        'valid_count': worksheet.totals.valid_count,
        'total_micrometres': str(worksheet.totals.micrometres),
        'rows': [
            {'slot': row.slot, 'state': row.state.value, 'raw': row.raw,
             'micrometres': None if row.micrometres is None else str(row.micrometres),
             'source': None if row.provenance is None else asdict(row.provenance)}
            for row in worksheet.rows
        ],
    }


def _take_ticket(station: Station, connection: Connection, slot: int) -> ReadTicket:
    ticket = station.acquire(connection, connection.recording_id, slot)
    if not isinstance(ticket, ReadTicket):
        raise RuntimeError(f'workday sample could not start a read at slot {slot}')
    return ticket


def _receive(station: Station, device: LocalDevice) -> None:
    result = device.events.run_next()
    if not isinstance(result, ReadResult) or station.complete_reply(result) is not Completion.ACCEPTED:
        raise RuntimeError('workday sample did not persist a device reply')


def run_workday(sample: dict) -> dict:
    device = LocalDevice(sample['capacity'], sample['first_slot'])
    recording_id = sample['recording_id']
    section_id = sample['section_id']
    with TemporaryDirectory(prefix='vibration-workday-') as directory:
        path = Path(directory) / 'station.sqlite3'
        with Station(path, device) as station:
            shift = station.open_shift(sample['shift_id'], recording_id, *sample['calibration'])
            station.define_section(section_id, shift.shift_id, *sample['section_interval'])
            connection = station.start(recording_id)
            for raw in sample['initial_raw']:
                station.record(recording_id, raw)
            delayed = _take_ticket(station, connection, sample['delayed_slot'])
            station.queue_reply(delayed, delay=1)
            for raw in sample['advance_raw']:
                station.record(recording_id, raw)
            later = _take_ticket(station, connection, sample['recent_slot'])
            station.queue_reply(later)
            _receive(station, device)
            waiting = station.issue(section_id)
            if not isinstance(waiting, PendingIssue):
                raise RuntimeError('workday sample expected a pending section before the delayed reply')
            _receive(station, device)
            issued = station.issue(section_id)
            if not isinstance(issued, IssuedReport):
                raise RuntimeError('workday sample did not issue a report after settlement')
            station.terminate(connection)
            station.end_recording(recording_id)
        with Station(path) as station:
            retrieved = station.retrieve(issued.number)
            if retrieved is None:
                raise RuntimeError('workday sample could not retrieve its report offline')
            return {'pending_slots': list(waiting.slots), 'issued': _describe_report(issued),
                    'retrieved': _describe_report(retrieved)}


def run_rescue_demo() -> dict:
    """Demonstrate a rescue job: overlap selection, issued reports, offline retrieval."""
    device = LocalDevice(3, 40)
    recording_id = 'rescue-strip'
    sections = {'A': (40, 41), 'B': (41, 42), 'C': (42, 42)}
    with TemporaryDirectory(prefix='vibration-rescue-') as directory:
        path = Path(directory) / 'station.sqlite3'
        with Station(path, device) as station:
            station.open_shift('rescue-day', recording_id, 0, 1, 1)
            for name, interval in sections.items():
                station.define_section(name, 'rescue-day', *interval)
            for raw in (8, 11, 13):
                station.record(recording_id, raw)
            connection = station.start(recording_id)
            station.register_rescue('rescue-job', recording_id,
                                    {name: 1 for name in sections}, 2)
            reads = []
            while True:
                state = station.advance_rescue('rescue-job', connection)
                ticket = state['pending']
                if ticket is None:
                    break
                reads.append(ticket.slot)
                station.queue_reply(ticket)
                result = device.events.run_next()
                if not isinstance(result, ReadResult):
                    raise RuntimeError('rescue demo expected a device reply')
                if station.complete_reply(result) is not Completion.ACCEPTED:
                    raise RuntimeError('rescue demo did not persist a device reply')
            final = station.rescue_status('rescue-job')
            issued = {}
            for name in sorted(final['completed']):
                report = station.issue(name)
                if not isinstance(report, IssuedReport):
                    raise RuntimeError('rescue demo expected issued sections')
                issued[name] = _describe_report(report)
            station.terminate(connection)
            station.end_recording(recording_id)
        with Station(path) as station:
            retrieved = {name: _describe_report(station.retrieve(report['number']))
                         for name, report in issued.items()}
        return {'read_slots': reads,
                'used': final['used'], 'remaining': final['remaining'],
                'completed': sorted(final['completed']),
                'targets': sorted(final['targets']),
                'issued': issued, 'retrieved': retrieved}


def main() -> None:
    parser = argparse.ArgumentParser(description='Run a local vibration station workday')
    subcommands = parser.add_subparsers(dest='command', required=True)
    demo = subcommands.add_parser('demo', help='run a workday against a temporary station')
    demo.add_argument('sample', type=Path, nargs='?')
    args = parser.parse_args()
    if args.sample is None:
        print(json.dumps(run_rescue_demo(), ensure_ascii=False, sort_keys=True))
        return
    with args.sample.open(encoding='utf-8') as stream:
        sample = json.load(stream)
    result = run_workday(sample)
    result['rescue'] = run_rescue_demo()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
