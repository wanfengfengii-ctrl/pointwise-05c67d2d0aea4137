from fractions import Fraction

from assessment.coverage import SlotState
from tests.support import StationCase


class RetrieveTests(StationCase):
    def test_offline_retrieval_returns_original_report_and_receipt(self):
        self.section()
        self.record(8, 11)
        delayed = self.accept(41)
        later = self.advance_overrun()
        issued = self.issue_report()
        rows = issued.report.worksheet.rows
        self.assertEqual([row.state for row in rows], [SlotState.MISSING, SlotState.VALID, SlotState.VALID])
        self.assertEqual([row.micrometres for row in rows], [None, Fraction(11), Fraction(13)])
        self.assertEqual([row.provenance.request_id for row in rows[1:]],
                         [delayed.request_id, later.request_id])
        self.assertEqual(issued.report.worksheet.totals.micrometres, Fraction(24))
        self.station.terminate(self.connection)
        self.station.end_recording(self.recording_id)
        self.station.close()
        with self.open_station() as offline:
            self.assertEqual(offline.retrieve(issued.number), issued)
            self.assertEqual(offline.issue('segment'), issued)
            self.assertIsNone(offline.retrieve('unknown-number'))
        self.assertEqual(self.archive_sizes(), (1, 3, 1))

    def test_initialization_preserves_registered_and_issued_data(self):
        self.section(40, 40)
        self.record(17)
        original_ticket = self.accept(40)
        issued = self.issue_report()
        extra_shift = self.station.open_shift('user-shift', 'user-recording', -2, 3, 7)
        extra_section = self.station.define_section('user-section', 'user-shift', 71, 75)
        self.station.terminate(self.connection)
        self.station.end_recording(self.recording_id)
        self.station.close()
        for _ in range(3):
            with self.open_station() as opened:
                self.assertEqual(opened.shifts.get_shift('user-shift'), extra_shift)
                self.assertEqual(opened.sections.get_section('user-section'), extra_section)
                self.assertEqual(opened.retrieve(issued.number), issued)
                self.assertEqual(opened.receipts.get(issued.number), issued.receipt)
                self.assertEqual(opened.find_observation(self.recording_id, 40).provenance.request_id,
                                 original_ticket.request_id)
        self.assertEqual(self.archive_sizes(), (1, 1, 1))
