import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from archive.reconcile import IssuedReport
from capture.admission import Completion
from device.read_port import ReadResult, ReadTicket
from station import LocalDevice, Station


class StationCase(unittest.TestCase):
    capacity = 2
    first_slot = 40
    recording_id = 'recording'
    shift_id = 'day'
    calibration = (0, 1, 1)

    def setUp(self):
        temporary = TemporaryDirectory(prefix='vibration-test-')
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'station.sqlite3'
        self.device = LocalDevice(self.capacity, self.first_slot)
        self.station = self.open_station(self.device)
        self.shift = self.station.open_shift(self.shift_id, self.recording_id, *self.calibration)
        self.connection = self.station.start(self.recording_id)

    def open_station(self, device=None):
        station = Station(self.path, device)
        self.addCleanup(station.close)
        return station

    def section(self, first=40, last=42, name='segment'):
        return self.station.define_section(name, self.shift_id, first, last)

    def record(self, *raw_values):
        return tuple(self.station.record(self.recording_id, raw) for raw in raw_values)

    def ticket(self, slot):
        ticket = self.station.acquire(self.connection, self.recording_id, slot)
        self.assertIsInstance(ticket, ReadTicket)
        return ticket

    def result(self, ticket, *, succeeded=True):
        self.station.queue_reply(ticket, succeeded=succeeded)
        result = self.device.events.run_next()
        self.assertIsInstance(result, ReadResult)
        return result

    def accept(self, slot):
        ticket = self.ticket(slot)
        self.assertIs(self.station.complete_reply(self.result(ticket)), Completion.ACCEPTED)
        return ticket

    def advance_overrun(self):
        self.assertEqual(self.record(13, 17), (42, 43))
        return self.accept(42)

    def issue_report(self, name='segment'):
        report = self.station.issue(name)
        self.assertIsInstance(report, IssuedReport)
        return report

    def archive_sizes(self):
        with closing(sqlite3.connect(self.path)) as reader:
            reports = reader.execute('SELECT COUNT(*) FROM reports').fetchone()[0]
            rows = reader.execute('SELECT COUNT(*) FROM report_rows').fetchone()[0]
            receipts = reader.execute('SELECT COUNT(*) FROM receipts').fetchone()[0]
        return reports, rows, receipts

    def install_sql(self, sql):
        with closing(sqlite3.connect(self.path)) as writer:
            writer.executescript(sql)
