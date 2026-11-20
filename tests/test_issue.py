import sqlite3
from fractions import Fraction

from archive.publish import PendingIssue
from assessment.coverage import SlotState
from tests.support import StationCase


class IssueTests(StationCase):
    calibration = (0, 1, 2)

    def test_exact_frozen_scale(self):
        self.section(40, 41)
        self.record(3, 5)
        self.accept(40)
        self.accept(41)
        issued = self.issue_report()
        worksheet = issued.report.worksheet
        self.assertEqual([row.slot for row in worksheet.rows], [40, 41])
        self.assertEqual([row.micrometres for row in worksheet.rows],
                         [Fraction(3, 2), Fraction(5, 2)])
        self.assertEqual(worksheet.totals.valid_count, 2)
        self.assertEqual(worksheet.totals.micrometres, Fraction(4))
        self.assertEqual(self.station.retrieve(issued.number), issued)

    def test_section_binding(self):
        fixed = self.section(40, 41)
        self.assertEqual(self.section(40, 41), fixed)
        with self.assertRaises(ValueError):
            self.section(40, 42)
        with self.assertRaises(ValueError):
            self.section(41, 40, name='reverse')
        with self.assertRaises(KeyError):
            self.station.define_section('unknown', 'absent', 40, 41)
        self.assertEqual(self.station.sections.get_section('segment'), fixed)
        self.assertIsNone(self.station.sections.get_section('reverse'))
        with self.assertRaises(KeyError):
            self.station.issue('absent')
        self.assertEqual(self.archive_sizes(), (0, 0, 0))

    def test_zero_and_empty(self):
        recording = self.station.open_shift('second', 'second-run', 2, -1, -2)
        self.assertEqual(recording.calibration.ratio, Fraction(1, 2))
        self.shift_id = recording.shift_id
        self.recording_id = recording.recording_id
        self.connection = self.station.start(self.recording_id)
        self.section(40, 41, name='zero-and-missing')
        self.section(41, 41, name='all-missing')
        self.record(2, 7)
        ticket = self.accept(40)
        self.record(8, 9)
        mixed = self.issue_report('zero-and-missing').report.worksheet
        self.assertEqual([row.state for row in mixed.rows], [SlotState.VALID, SlotState.MISSING])
        self.assertEqual(mixed.rows[0].micrometres, Fraction(0))
        self.assertEqual(mixed.rows[0].provenance.request_id, ticket.request_id)
        self.assertIsNone(mixed.rows[1].micrometres)
        self.assertIsNone(mixed.rows[1].provenance)
        self.assertEqual((mixed.totals.valid_count, mixed.totals.micrometres), (1, Fraction(0)))
        empty = self.issue_report('all-missing').report.worksheet
        self.assertEqual((empty.totals.valid_count, empty.totals.micrometres), (0, Fraction(0)))
        self.assertIs(empty.rows[0].state, SlotState.MISSING)

    def test_unwritten_and_resident_without_sample_stay_pending(self):
        self.section(39, 41)
        self.record(3)
        self.assertEqual(self.station.issue('segment'), PendingIssue('segment', (39, 40, 41)))
        self.accept(40)
        self.station.terminate(self.connection)
        self.station.end_recording(self.recording_id)
        self.assertEqual(self.station.issue('segment'), PendingIssue('segment', (39, 41)))
        with self.open_station() as offline:
            self.assertEqual(offline.issue('segment'), PendingIssue('segment', (39, 41)))
        self.assertEqual(self.archive_sizes(), (0, 0, 0))

    def test_report_rows_roll_back_if_receipt_write_fails(self):
        self.section(40, 40)
        self.record(3)
        self.accept(40)
        self.install_sql('''
            CREATE TRIGGER refuse_receipt BEFORE INSERT ON receipts
            BEGIN SELECT RAISE(ABORT, 'receipt write refused'); END;
        ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'receipt write refused'):
            self.station.issue('segment')
        self.assertEqual(self.archive_sizes(), (0, 0, 0))
        self.assertEqual(self.station.find_observation(self.recording_id, 40).raw, 3)
        self.install_sql('DROP TRIGGER refuse_receipt;')
        issued = self.issue_report()
        self.assertEqual(issued.report.worksheet.totals.micrometres, Fraction(3, 2))
        self.assertEqual(self.archive_sizes(), (1, 1, 1))
        self.station.terminate(self.connection)
        self.station.end_recording(self.recording_id)
        with self.open_station() as offline:
            self.assertEqual(offline.retrieve(issued.number), issued)

    def test_commit_failure_does_not_publish(self):
        self.section(40, 40)
        self.record(5)
        self.accept(40)
        self.install_sql('''
            CREATE TABLE commit_parent (token TEXT PRIMARY KEY);
            CREATE TABLE commit_child (
                token TEXT REFERENCES commit_parent(token) DEFERRABLE INITIALLY DEFERRED
            );
            CREATE TRIGGER defer_receipt_check AFTER INSERT ON receipts
            BEGIN INSERT INTO commit_child VALUES (NEW.token); END;
        ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'FOREIGN KEY'):
            self.station.issue('segment')
        self.assertEqual(self.archive_sizes(), (0, 0, 0))
        self.install_sql('DROP TRIGGER defer_receipt_check;')
        self.assertEqual(self.issue_report().report.worksheet.totals.micrometres, Fraction(5, 2))

    def test_repeat_issue_reuses_report_and_first_receipt(self):
        self.section(40, 40)
        self.record(3)
        self.accept(40)
        issued = self.issue_report()
        self.record(8, 9)
        self.assertEqual(self.issue_report(), issued)
        self.assertEqual(self.archive_sizes(), (1, 1, 1))
        self.assertEqual(issued.receipt.report_number, issued.number)
