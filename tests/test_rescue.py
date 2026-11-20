import sqlite3
from contextlib import contextmanager
from unittest.mock import patch

from archive.publish import PendingIssue
from archive.reconcile import IssuedReport
from capture.admission import Completion
from device.recorder import SlotAvailability
from tests.support import StationCase

RECORDING = 'rescue-strip'
SHIFT = 'rescue-day'
SECTIONS = {'A': (40, 41), 'B': (41, 42), 'C': (42, 42)}
WEIGHTS = {'A': 1, 'B': 1, 'C': 1}


@contextmanager
def deny_next_sql(db, stage):
    if stage not in ('write', 'commit'):
        raise ValueError('stage must be write or commit')
    writes = (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE)
    armed = False

    def authorize(action, argument, *_):
        nonlocal armed
        matched = action in writes if stage == 'write' else (
            action == sqlite3.SQLITE_TRANSACTION and argument == 'COMMIT')
        if armed and matched:
            armed = False
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def arm():
        nonlocal armed
        armed = True
        db.set_authorizer(authorize)

    try:
        yield arm
    finally:
        db.set_authorizer(None)


class RescueCase(StationCase):
    capacity = 3
    first_slot = 40
    recording_id = RECORDING
    shift_id = SHIFT

    def setUp(self):
        super().setUp()
        for name, interval in SECTIONS.items():
            self.station.define_section(name, SHIFT, *interval)
        self.assertEqual(self.record(8, 11, 13), (40, 41, 42))

    def deliver(self, ticket, *, succeeded=True):
        self.station.queue_reply(ticket, succeeded=succeeded)
        result = self.device.events.run_next()
        return result

    def settle(self, ticket, *, succeeded=True):
        return self.station.complete_reply(self.deliver(ticket, succeeded=succeeded))

    def overwrite(self, *raw_values):
        return self.record(*raw_values)


class RescueSelectionTests(RescueCase):
    def test_overlap_shared_slots_coalesce_once(self):
        status = self.station.register_rescue('job', RECORDING, WEIGHTS, 2)
        self.assertEqual(status['used'], 0)
        self.assertEqual(status['remaining'], 2)
        self.assertIsNone(status['pending'])
        self.assertEqual(status['targets'], {'B', 'C'})
        self.assertEqual(status['completed'], set())

        first = self.station.advance_rescue('job', self.connection)
        self.assertEqual(first['used'], 1)
        self.assertEqual(first['remaining'], 1)
        self.assertEqual(first['targets'], {'C'})
        pending = first['pending']
        self.assertIsNotNone(pending)
        self.assertEqual(pending.slot, 41)

        self.assertIs(self.settle(pending), Completion.ACCEPTED)
        second = self.station.advance_rescue('job', self.connection)
        self.assertEqual(second['used'], 2)
        self.assertEqual(second['remaining'], 0)
        self.assertEqual(second['pending'].slot, 42)
        self.assertIs(self.settle(second['pending']), Completion.ACCEPTED)

        final = self.station.rescue_status('job')
        self.assertEqual(final['remaining'], 0)
        self.assertEqual(final['completed'], {'B', 'C'})
        self.assertEqual(final['targets'], set())
        # No further reads once the budget is spent.
        self.assertEqual(self.station.advance_rescue('job', self.connection)['used'], 2)

        middle = self.station.issue('B')
        tail = self.station.issue('C')
        self.assertIsInstance(middle, IssuedReport)
        self.assertIsInstance(tail, IssuedReport)
        self.assertEqual(middle.report.worksheet.totals.micrometres, 24)
        self.assertEqual(tail.report.worksheet.totals.micrometres, 13)
        shared_b = middle.report.worksheet.rows[1]
        only_c = tail.report.worksheet.rows[0]
        self.assertEqual(shared_b.slot, 42)
        self.assertEqual(only_c.slot, 42)
        self.assertEqual(shared_b.provenance, only_c.provenance)

    def test_failed_first_read_switches_to_the_other_slot(self):
        self.station.register_rescue('job', RECORDING, WEIGHTS, 2)
        first = self.station.advance_rescue('job', self.connection)
        self.assertEqual(first['pending'].slot, 41)
        self.assertIs(self.settle(first['pending'], succeeded=False), Completion.FAILED)

        second = self.station.advance_rescue('job', self.connection)
        self.assertEqual(second['used'], 2)
        self.assertEqual(second['pending'].slot, 42)
        self.assertIs(self.settle(second['pending']), Completion.ACCEPTED)

        final = self.station.rescue_status('job')
        self.assertEqual(final['completed'], {'C'})
        self.assertIsInstance(self.station.issue('C'), IssuedReport)
        self.assertEqual(self.station.issue('B'), PendingIssue('B', (41,)))

    def test_previous_ordinary_observation_is_shared_free(self):
        observation = self.station.find_observation(RECORDING, 41)
        self.assertIsNone(observation)
        ticket = self.ticket(41)
        self.assertIs(self.settle(ticket), Completion.ACCEPTED)
        self.station.register_rescue('job', RECORDING, WEIGHTS, 1)
        status = self.station.rescue_status('job')
        self.assertEqual(status['used'], 0)
        self.assertEqual(status['targets'], {'B', 'C'})
        advanced = self.station.advance_rescue('job', self.connection)
        self.assertEqual(advanced['pending'].slot, 42)
        self.assertIs(self.settle(advanced['pending']), Completion.ACCEPTED)
        self.assertEqual(advanced['remaining'], 0)
        self.assertEqual(self.station.rescue_status('job')['completed'], {'B', 'C'})

    def test_ordinary_reads_do_not_consume_budget(self):
        self.station.register_rescue('job', RECORDING, WEIGHTS, 1)
        ordinary = self.ticket(40)
        self.assertIs(self.settle(ordinary), Completion.ACCEPTED)
        self.assertEqual(self.station.rescue_status('job')['used'], 0)

    def test_unreadable_slots_exclude_section_without_deduction(self):
        self.overwrite(13, 17)  # overwrite slot 41, still holds 42
        self.station.register_rescue('job', RECORDING, {'A': 1, 'B': 1, 'C': 1}, 3)
        status = self.station.rescue_status('job')
        self.assertEqual(status['targets'], {'C'})
        self.assertEqual(status['used'], 0)

    def test_budget_prefers_fuller_weight_under_the_cap(self):
        # A costs two slots; C costs one. With budget one only C fits.
        self.station.register_rescue('job', RECORDING, {'A': 5, 'C': 1}, 1)
        self.assertEqual(self.station.rescue_status('job')['targets'], {'C'})
        state = self.station.advance_rescue('job', self.connection)
        self.assertEqual(state['pending'].slot, 42)
        self.assertIs(self.settle(state['pending']), Completion.ACCEPTED)
        final = self.station.rescue_status('job')
        self.assertEqual(final['remaining'], 0)
        self.assertEqual(final['completed'], {'C'})
        self.assertEqual(final['targets'], set())

    def test_overlap_sums_weight_when_union_fits(self):
        self.station.register_rescue('job', RECORDING, {'B': 5, 'C': 1}, 2)
        status = self.station.rescue_status('job')
        self.assertEqual(status['targets'], {'B', 'C'})
        self.assertEqual(status['used'], 0)

    def test_zero_budget_and_query_never_dispatch(self):
        self.station.register_rescue('job', RECORDING, WEIGHTS, 0)
        status = self.station.rescue_status('job')
        self.assertEqual(status['remaining'], 0)
        self.assertIsNone(status['pending'])
        self.assertEqual(status['targets'], set())
        self.assertEqual(self.station.advance_rescue('job', self.connection), status)

    def test_advance_validates_job_and_connection(self):
        self.station.register_rescue('job', RECORDING, WEIGHTS, 2)
        with self.assertRaises(KeyError):
            self.station.advance_rescue('absent', self.connection)
        self.station.open_shift('other', 'other-recording', 0, 1, 1)
        foreign = self.station.start('other-recording')
        with self.assertRaises(ValueError):
            self.station.advance_rescue('job', foreign)
        self.station.terminate(self.connection)
        with self.assertRaises(ValueError):
            self.station.advance_rescue('job', self.connection)


class RescueRegistrationTests(RescueCase):
    def test_idempotent_and_conflicting_registration(self):
        first = self.station.register_rescue('job', RECORDING, WEIGHTS, 2)
        again = self.station.register_rescue('job', RECORDING,
                                             {'C': 1, 'B': 1, 'A': 1}, 2)
        self.assertEqual(first, again)
        for weights, budget in (({'A': 2, 'B': 1, 'C': 1}, 2),
                                (WEIGHTS, 3),
                                ({'A': 1}, 2)):
            with self.subTest(weights=weights, budget=budget), self.assertRaises(ValueError):
                self.station.register_rescue('job', RECORDING, weights, budget)
        with self.assertRaises(ValueError):
            self.station.register_rescue('other', RECORDING, WEIGHTS, 2)

    def test_unknown_and_misbound_inputs(self):
        with self.assertRaises(KeyError):
            self.station.register_rescue('job', 'absent-recording', {'A': 1}, 1)
        with self.assertRaises(KeyError):
            self.station.register_rescue('job', RECORDING, {'nope': 1}, 1)
        self.station.open_shift('other', 'other-recording', 0, 1, 1)
        self.station.define_section('elsewhere', 'other', 40, 41)
        with self.assertRaises(ValueError):
            self.station.register_rescue('job', RECORDING, {'elsewhere': 1}, 1)
        for weights, budget in (({'A': 0}, 1), ({'A': True}, 1), ({'A': 1}, -1),
                                ({'A': 1}, True), (['A'], 1)):
            with self.subTest(weights=weights, budget=budget), self.assertRaises(ValueError):
                self.station.register_rescue('job', RECORDING, weights, budget)
        with self.assertRaises(KeyError):
            self.station.rescue_status('absent')
        with self.assertRaises(ValueError):
            self.station.register_rescue(' ', RECORDING, WEIGHTS, 1)

    def test_issued_section_cannot_be_registered(self):
        self.station.issue('A')  # A contains slots 40, 41 which stay pending
        self.assertIsInstance(self.station.issue('A'), PendingIssue)
        self.station.define_section('D', SHIFT, 40, 40)
        self.accept(40)
        issued = self.station.issue('D')
        self.assertIsInstance(issued, IssuedReport)
        with self.assertRaises(ValueError):
            self.station.register_rescue('job', RECORDING, {'D': 1}, 1)

    def test_one_job_per_recording(self):
        self.station.register_rescue('job', RECORDING, {'A': 1}, 1)
        with self.assertRaises(ValueError):
            self.station.register_rescue('another', RECORDING, {'A': 1}, 1)


class RescueResumeTests(RescueCase):
    def _arm_after_accept(self, stage):
        device = self.device

        @contextmanager
        def manager():
            request_read = device.port.request_read
            accepted = []

            with deny_next_sql(self.station.db, stage) as arm:
                def observe(*args, **kwargs):
                    ticket = request_read(*args, **kwargs)
                    accepted.append(ticket)
                    arm()
                    return ticket

                with patch.object(device.port, 'request_read', side_effect=observe):
                    yield accepted

        return manager()

    def test_write_failure_reclaims_ticket_after_overwrite(self):
        self.station.define_section('B41', SHIFT, 41, 41)
        self.station.register_rescue('job', RECORDING, {'B41': 1}, 1)
        with self._arm_after_accept('write') as accepted:
            with self.assertRaises(sqlite3.DatabaseError):
                self.station.advance_rescue('job', self.connection)
            self.assertEqual(len(accepted), 1)

        self.assertEqual(self.station.issue('B41'), PendingIssue('B41', (41,)))
        self.overwrite(13, 17)
        self.assertIs(self.device.recorder.slot_availability(RECORDING, 41),
                      SlotAvailability.OVERWRITTEN)

        old = self.connection
        self.station.close()
        self.station = self.open_station(self.device)
        status = self.station.rescue_status('job')
        self.assertEqual(status['used'], 1)
        self.assertEqual(status['remaining'], 0)
        self.assertEqual(status['pending'].slot, 41)
        self.assertEqual(self.station.issue('B41'), PendingIssue('B41', (41,)))
        advanced = self.station.advance_rescue('job', old)
        self.assertEqual(advanced['used'], 1)
        self.assertEqual(advanced['remaining'], 0)

        self.assertIs(self.settle(status['pending']), Completion.ACCEPTED)
        issued = self.station.issue('B41')
        self.assertIsInstance(issued, IssuedReport)
        row, = issued.report.worksheet.rows
        self.assertEqual((row.raw, str(row.micrometres)), (11, '11'))
        self.assertEqual(row.provenance.request_id, status['pending'].request_id)

        duplicate = self.deliver(status['pending'])
        self.assertIs(self.station.complete_reply(duplicate), Completion.DUPLICATE)
        self.assertEqual(self.station.rescue_status('job')['used'], 1)

    def test_commit_failure_reclaims_ticket_after_overwrite(self):
        self.station.define_section('B41', SHIFT, 41, 41)
        self.station.register_rescue('job', RECORDING, {'B41': 1}, 1)
        with self._arm_after_accept('commit') as accepted:
            with self.assertRaises(sqlite3.DatabaseError):
                self.station.advance_rescue('job', self.connection)
            self.assertEqual(len(accepted), 1)
        self.overwrite(13, 17)
        self.station.close()
        self.station = self.open_station(self.device)
        status = self.station.rescue_status('job')
        self.assertEqual((status['used'], status['remaining']), (1, 0))
        self.assertIs(self.settle(status['pending']), Completion.ACCEPTED)
        issued = self.station.issue('B41')
        self.assertEqual(issued.report.worksheet.totals.micrometres, 11)

    def test_connection_termination_voids_ticket_but_keeps_deduction(self):
        self.station.define_section('B41', SHIFT, 41, 41)
        self.station.define_section('C42', SHIFT, 42, 42)
        self.station.register_rescue('job', RECORDING, {'B41': 1, 'C42': 1}, 2)
        first = self.station.advance_rescue('job', self.connection)
        self.assertEqual(first['pending'].slot, 41)
        old = self.connection
        self.station.terminate(old)
        status = self.station.rescue_status('job')
        self.assertEqual(status['used'], 1)
        self.assertIsNone(status['pending'])
        self.assertIs(self.settle(first['pending']), Completion.STALE)
        self.assertEqual(self.station.rescue_status('job')['used'], 1)

        self.connection = self.station.reopen(old)
        resumed = self.station.advance_rescue('job', self.connection)
        self.assertEqual(resumed['used'], 2)
        self.assertEqual(resumed['remaining'], 0)
        self.assertEqual(resumed['pending'].connection.generation,
                         self.connection.generation)
        # Retry the very slot whose ticket went stale, since it is never filled.
        self.assertEqual(resumed['pending'].slot, 41)

    def test_device_rejection_does_not_deduct(self):
        self.station.register_rescue('job', RECORDING, {'C': 1}, 1)
        with patch.object(self.device.port, 'request_read',
                          return_value=SlotAvailability.OVERWRITTEN):
            status = self.station.advance_rescue('job', self.connection)
        self.assertEqual(status['used'], 0)
        self.assertEqual(status['remaining'], 1)
        self.assertIsNone(status['pending'])

        # The device still never accepted a read; the slot is doomed for this pass.
        self.assertIs(self.station.rescue_status('job')['used'], 0)

    def test_offline_station_keeps_unresolved_slot_pending(self):
        self.station.define_section('B41', SHIFT, 41, 41)
        self.station.register_rescue('job', RECORDING, {'B41': 1}, 1)
        with self._arm_after_accept('write'):
            with self.assertRaises(sqlite3.DatabaseError):
                self.station.advance_rescue('job', self.connection)
        self.overwrite(13, 17)
        self.station.close()
        with self.open_station() as offline:
            self.assertEqual(offline.issue('B41'), PendingIssue('B41', (41,)))

    def test_offline_station_waits_for_an_unanswered_live_ticket(self):
        self.station.define_section('B41', SHIFT, 41, 41)
        self.station.register_rescue('job', RECORDING, {'B41': 1}, 1)
        self.station.advance_rescue('job', self.connection)
        self.overwrite(13, 17)
        self.station.close()
        with self.open_station() as offline:
            self.assertEqual(offline.issue('B41'), PendingIssue('B41', (41,)))

    def test_terminate_then_offline_issue_does_not_wait(self):
        self.station.define_section('B41', SHIFT, 41, 41)
        self.station.register_rescue('job', RECORDING, {'B41': 1}, 1)
        pending = self.station.advance_rescue('job', self.connection)['pending']
        self.station.terminate(self.connection)
        self.overwrite(13, 17)
        self.assertIs(self.settle(pending), Completion.STALE)
        issued = self.station.issue('B41')
        self.assertIsInstance(issued, IssuedReport)
        self.assertEqual(issued.report.worksheet.totals.valid_count, 0)


class RescueHandoffTests(RescueCase):
    def test_reissued_section_and_offline_retrieval(self):
        self.station.define_section('Tail', SHIFT, 42, 42)
        self.station.register_rescue('job', RECORDING, {'Tail': 1}, 1)
        advancing = self.station.advance_rescue('job', self.connection)
        self.assertEqual(advancing['pending'].slot, 42)
        self.assertIs(self.settle(advancing['pending']), Completion.ACCEPTED)
        issued = self.station.issue('Tail')
        self.assertIsInstance(issued, IssuedReport)
        self.assertEqual(issued.report.worksheet.totals.micrometres, 13)
        self.assertEqual(self.station.issue('Tail'), issued)
        self.assertEqual(self.station.rescue_status('job')['completed'], {'Tail'})

        # Re-registering the finished job returns its current status.
        self.assertEqual(self.station.register_rescue('job', RECORDING, {'Tail': 1}, 1),
                         self.station.rescue_status('job'))

        self.station.terminate(self.connection)
        self.station.end_recording(RECORDING)
        self.station.close()
        with self.open_station() as offline:
            self.assertEqual(offline.retrieve(issued.number), issued)
            self.assertIsNone(offline.retrieve('VR-missing'))
            self.assertEqual(offline.rescue_status('job')['completed'], {'Tail'})
