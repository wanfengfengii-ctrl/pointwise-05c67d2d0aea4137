from dataclasses import replace

from capture.admission import Completion
from capture.observations import Observation
from device.recorder import SlotAvailability
from tests.support import StationCase


class CaptureTests(StationCase):
    def test_sequential_zero_and_reused_cell(self):
        self.assertEqual(self.record(0), (40,))
        first = self.accept(40)
        self.assertEqual(self.record(9, 21), (41, 42))
        latest = self.accept(42)
        self.assertEqual(first.physical_cell, latest.physical_cell)
        self.assertNotEqual(first.slot, latest.slot)
        zero = self.station.find_observation(self.recording_id, 40)
        self.assertEqual(zero.raw, 0)
        self.assertEqual(zero.provenance.request_id, first.request_id)
        self.assertEqual(self.station.find_observation(self.recording_id, 42).raw, 21)
        self.assertIs(self.device.recorder.slot_availability(self.recording_id, 40),
                      SlotAvailability.OVERWRITTEN)
        known = self.station.acquire(self.connection, self.recording_id, 40)
        self.assertIsInstance(known, Observation)
        self.assertEqual(known, zero)
        self.record(33)
        self.assertIs(self.station.acquire(self.connection, self.recording_id, 41),
                      SlotAvailability.OVERWRITTEN)

    def test_shift_binding(self):
        self.assertEqual(self.station.open_shift(self.shift_id, self.recording_id, 0, 2, 2),
                         self.shift)
        for arguments in ((self.shift_id, self.recording_id, 1, 1, 1),
                          (self.shift_id, 'another-recording', 0, 1, 1),
                          ('night', self.recording_id, 0, 1, 1),
                          ('spare', 'spare-recording', 0, 1, 0),
                          ('spare', 'spare-recording', True, 1, 1)):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                self.station.open_shift(*arguments)
        with self.assertRaises(KeyError):
            self.station.record('another-recording', 7)
        with self.assertRaises(ValueError):
            self.station.record(self.recording_id, False)
        self.assertEqual(self.record(7, 8), (40, 41))
        self.station.end_recording(self.recording_id)
        with self.assertRaises(ValueError):
            self.record(9)

    def test_first_provenance(self):
        self.record(11)
        first_request = self.ticket(40)
        first_completion = self.ticket(40)
        for ticket in (first_completion, first_request):
            self.assertIs(self.station.complete_reply(self.result(ticket)), Completion.ACCEPTED)
        self.assertIs(self.station.complete_reply(self.result(first_request)), Completion.DUPLICATE)
        observation = self.station.find_observation(self.recording_id, 40)
        self.assertEqual(observation.raw, 11)
        self.assertEqual(observation.provenance.request_id, first_completion.request_id)
        self.section(40, 40)
        worksheet = self.issue_report().report.worksheet
        self.assertEqual(worksheet.totals.valid_count, 1)
        self.assertEqual(worksheet.rows[0].provenance, observation.provenance)

    def test_foreign_callback(self):
        self.record(9, 11)
        ticket = self.ticket(40)
        genuine = self.result(ticket)
        wrong_recording = replace(ticket.connection, recording_id='other')
        wrong_generation = replace(ticket.connection, generation=ticket.connection.generation + 1)
        for foreign in (replace(ticket, slot=41), replace(ticket, connection=wrong_recording),
                        replace(ticket, connection=wrong_generation),
                        replace(ticket, physical_cell=1), replace(ticket, request_id=100)):
            with self.subTest(foreign=foreign):
                self.assertIs(self.station.complete_reply(replace(genuine, ticket=foreign)),
                              Completion.REJECTED)
        for slot in (40, 41):
            self.assertIsNone(self.station.find_observation(self.recording_id, slot))
        with self.assertRaises(ValueError):
            self.station.acquire(self.connection, 'other', 40)
        self.assertIs(self.station.complete_reply(genuine), Completion.ACCEPTED)
        self.assertEqual(self.station.find_observation(self.recording_id, 40).raw, 9)
