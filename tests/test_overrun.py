import sqlite3
from fractions import Fraction

from archive.publish import PendingIssue
from assessment.coverage import SlotState
from capture.admission import Completion
from device.recorder import SlotAvailability
from tests.support import StationCase


class OverrunTests(StationCase):
    def setUp(self):
        super().setUp()
        self.section()
        self.assertEqual(self.record(8, 11), (40, 41))

    def assert_waiting_for_41(self):
        self.assertEqual(self.station.issue('segment'), PendingIssue('segment', (41,)),
                         '班段漏样：41 号读请求在途时不得固定班报')
        self.assertEqual(self.archive_sizes(), (0, 0, 0))

    def assert_report(self, middle, newer, *, delayed=None):
        worksheet = self.issue_report().report.worksheet
        self.assertEqual([row.slot for row in worksheet.rows], [40, 41, 42])
        self.assertEqual([row.micrometres for row in worksheet.rows], [None, middle, Fraction(13)])
        self.assertEqual(worksheet.rows[0].state, SlotState.MISSING)
        self.assertIsNone(worksheet.rows[0].provenance)
        self.assertEqual(worksheet.rows[2].provenance.request_id, newer.request_id)
        self.assertEqual(worksheet.rows[2].provenance.slot, 42)
        self.assertEqual(worksheet.rows[2].provenance.recording_id, self.recording_id)
        if delayed is None:
            self.assertEqual(worksheet.rows[1].state, SlotState.MISSING)
            self.assertIsNone(worksheet.rows[1].provenance)
            self.assertEqual(worksheet.totals.valid_count, 1)
            self.assertEqual(worksheet.totals.micrometres, Fraction(13))
        else:
            self.assertEqual(worksheet.rows[1].state, SlotState.VALID)
            self.assertEqual(worksheet.rows[1].provenance.request_id, delayed.request_id)
            self.assertEqual(worksheet.rows[1].provenance.slot, 41)
            self.assertEqual(worksheet.rows[1].provenance.recording_id, self.recording_id)
            self.assertEqual(worksheet.totals.valid_count, 2)
            self.assertEqual(worksheet.totals.micrometres, Fraction(24))

    def test_capture_after_overwrite(self):
        delayed = self.ticket(41)
        later = self.advance_overrun()
        self.assertIs(self.station.acquire(self.connection, self.recording_id, 41),
                      SlotAvailability.OVERWRITTEN)
        self.assertIs(self.station.complete_reply(self.result(delayed)), Completion.ACCEPTED,
                      '班段漏样：已受理的 41 号回交应在覆写后接纳')
        observation = self.station.find_observation(self.recording_id, 41)
        self.assertIsNotNone(observation, '班段漏样：41 号原样未持久接纳')
        self.assertEqual(observation.raw, 11)
        self.assertEqual(observation.provenance.request_id, delayed.request_id)
        self.assert_report(Fraction(11), later, delayed=delayed)

    def test_issue_before_reply(self):
        delayed = self.ticket(41)
        self.station.queue_reply(delayed, delay=3)
        later = self.advance_overrun()
        self.assert_waiting_for_41()
        reply = self.device.events.run_next()
        self.assertEqual(reply.ticket, delayed)
        self.assertIs(self.station.complete_reply(reply), Completion.ACCEPTED)
        self.assert_report(Fraction(11), later, delayed=delayed)
        self.assertEqual(self.archive_sizes(), (1, 3, 1))

    def test_failed_reply_settles_overwritten_slot(self):
        delayed = self.ticket(41)
        later = self.advance_overrun()
        self.assert_waiting_for_41()
        self.assertIs(self.station.complete_reply(self.result(delayed, succeeded=False)),
                      Completion.FAILED)
        self.assertIsNone(self.station.find_observation(self.recording_id, 41))
        self.assert_report(None, later)

    def test_last_attempt_must_settle(self):
        first, second = self.ticket(41), self.ticket(41)
        later = self.advance_overrun()
        self.assert_waiting_for_41()
        first_failure = self.result(first, succeeded=False)
        self.assertIs(self.station.complete_reply(first_failure), Completion.FAILED)
        self.assert_waiting_for_41()
        self.assertIs(self.station.complete_reply(first_failure), Completion.DUPLICATE)
        self.assert_waiting_for_41()
        self.assertIs(self.station.complete_reply(self.result(second, succeeded=False)),
                      Completion.FAILED)
        self.assert_report(None, later)

    def test_observation_and_settlement_roll_back_together(self):
        delayed = self.ticket(41)
        later = self.advance_overrun()
        reply = self.result(delayed)
        self.install_sql('''
            CREATE TRIGGER refuse_settlement BEFORE UPDATE OF state ON read_attempts
            WHEN NEW.state = 'succeeded'
            BEGIN SELECT RAISE(ABORT, 'settlement write refused'); END;
        ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'settlement write refused'):
            self.station.complete_reply(reply)
        self.assertIsNone(self.station.find_observation(self.recording_id, 41))
        self.assert_waiting_for_41()
        self.install_sql('DROP TRIGGER refuse_settlement;')
        self.assertIs(self.station.complete_reply(reply), Completion.ACCEPTED)
        self.assert_report(Fraction(11), later, delayed=delayed)
