from fractions import Fraction

from archive.publish import PendingIssue
from assessment.coverage import SlotState
from capture.admission import Completion, PreparedRead
from capture.attempts import AttemptState
from tests.support import StationCase


class ReopenTests(StationCase):
    def setUp(self):
        super().setUp()
        self.section()
        self.record(8, 11)

    def resume_station(self, old):
        self.station.close()
        self.station = self.open_station(self.device)
        self.connection = self.station.reopen(old)
        self.assertNotEqual(old.generation, self.connection.generation)

    def test_terminate_before_observation_commit(self):
        old = self.connection
        delayed = self.ticket(41)
        later = self.advance_overrun()
        result = self.result(delayed)
        prepared = self.station.prepare_reply(result)
        self.assertIsInstance(prepared, PreparedRead)
        self.assertEqual(self.station.issue('segment'), PendingIssue('segment', (41,)))
        self.assertIsNone(self.station.find_observation(self.recording_id, 41))
        self.station.terminate(old)
        self.resume_station(old)
        onward = self.accept(43)
        self.assertIs(self.station.complete_reply(prepared), Completion.STALE)
        self.assertIs(self.station.complete_reply(result), Completion.STALE)
        self.assertIsNone(self.station.find_observation(self.recording_id, 41))
        self.assertEqual(self.station.find_observation(self.recording_id, 43).provenance.request_id,
                         onward.request_id)
        worksheet = self.issue_report().report.worksheet
        self.assertEqual([row.state for row in worksheet.rows],
                         [SlotState.MISSING, SlotState.MISSING, SlotState.VALID])
        self.assertEqual(worksheet.rows[2].provenance.request_id, later.request_id)
        self.assertEqual((worksheet.totals.valid_count, worksheet.totals.micrometres),
                         (1, Fraction(13)))

    def test_terminate_after_commit_before_acknowledgement(self):
        old = self.connection
        delayed = self.ticket(41)
        self.advance_overrun()
        result = self.result(delayed)
        acknowledgements = []
        self.assertIs(self.station.complete_reply(result, acknowledge=acknowledgements.append),
                      Completion.ACCEPTED)
        self.assertEqual(acknowledgements, [])
        original = self.station.find_observation(self.recording_id, 41)
        self.assertEqual(original.raw, 11)
        self.station.terminate(old)
        self.resume_station(old)
        self.device.events.run_next()
        self.assertEqual(acknowledgements, [Completion.ACCEPTED])
        self.assertIs(self.station.complete_reply(result), Completion.STALE)
        self.assertEqual(self.station.find_observation(self.recording_id, 41), original)
        self.assertEqual(self.record(19), (44,))
        self.accept(44)
        worksheet = self.issue_report().report.worksheet
        self.assertEqual([row.micrometres for row in worksheet.rows],
                         [None, Fraction(11), Fraction(13)])
        self.assertEqual(worksheet.rows[1].provenance, original.provenance)
        self.assertEqual(worksheet.rows[1].provenance.generation, old.generation)
        self.assertEqual((worksheet.totals.valid_count, worksheet.totals.micrometres),
                         (2, Fraction(24)))

    def test_reopen_failure_preserves_station(self):
        old = self.connection
        self.accept(40)
        original = self.station.find_observation(self.recording_id, 40)
        delayed = self.ticket(41)
        self.advance_overrun()
        with self.assertRaises(ValueError):
            self.station.reopen(old)
        self.station.terminate(old)
        self.device.sessions.set_accepting(False)
        try:
            with self.assertRaises(ConnectionError):
                self.station.reopen(old)
            self.assertEqual(self.station.find_observation(self.recording_id, 40), original)
            attempt = self.station.attempts.get_attempt(old.generation, delayed.request_id)
            self.assertIs(attempt.state, AttemptState.VOID)
            self.assertIs(self.station.complete_reply(self.result(delayed)), Completion.STALE)
        finally:
            self.device.sessions.set_accepting(True)
        self.resume_station(old)
        self.accept(43)
        self.assertEqual(self.station.find_observation(self.recording_id, 40), original)
        worksheet = self.issue_report().report.worksheet
        self.assertEqual([row.micrometres for row in worksheet.rows],
                         [Fraction(8), None, Fraction(13)])
        self.assertEqual(worksheet.totals.micrometres, Fraction(21))
