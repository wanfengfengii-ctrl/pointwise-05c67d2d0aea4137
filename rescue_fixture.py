"""Fixtures for overlapping sections and the V1 read/commit boundary."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from assessment.coverage import SlotState
from archive.publish import PendingIssue
from archive.reconcile import IssuedReport
from capture.admission import Completion
from device.read_port import ReadResult, ReadTicket
from device.recorder import SlotAvailability
from station import LocalDevice, Station


RECORDING = "rescue-strip"
SHIFT = "rescue-day"
JOB = "rescue-job"
SECTIONS = {"A": (40, 41), "B": (41, 42), "C": (42, 42)}
WEIGHTS = {"A": 1, "B": 1, "C": 1}
BUDGET = 2


def seed_overlap(station):
    """Use a device of capacity three starting at slot 40."""
    station.open_shift(SHIFT, RECORDING, 0, 1, 1)
    for name, interval in SECTIONS.items():
        station.define_section(name, SHIFT, *interval)
    for raw in (8, 11, 13):
        station.record(RECORDING, raw)
    return station.start(RECORDING)


@contextmanager
def deny_next_sql(db, stage):
    """Yield an arming callback for a dedicated test connection."""
    if stage not in ("write", "commit"):
        raise ValueError("stage must be write or commit")
    writes = (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE)
    armed = False

    def authorize(action, argument, *_):
        nonlocal armed
        matched = action in writes if stage == "write" else (
            action == sqlite3.SQLITE_TRANSACTION and argument == "COMMIT"
        )
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


def _receive(station, ticket, *, succeeded=True):
    station.queue_reply(ticket, succeeded=succeeded)
    reply = station.device.events.run_next()
    assert isinstance(reply, ReadResult) and reply.ticket == ticket
    return reply


def probe_rescue(path, *, fail_first=False):
    """Drive one rescue job over the overlap sections and report the outcome."""
    device = LocalDevice(3, 40)
    with Station(path, device) as station:
        connection = seed_overlap(station)
        status = station.register_rescue(JOB, RECORDING, WEIGHTS, BUDGET)
        first = station.advance_rescue(JOB, connection)
        dispatch_order = []
        while first["pending"] is not None:
            ticket = first["pending"]
            dispatch_order.append(ticket.slot)
            if fail_first and len(dispatch_order) == 1:
                station.queue_reply(ticket, succeeded=False)
            else:
                station.queue_reply(ticket)
            result = device.events.run_next()
            assert isinstance(result, ReadResult)
            station.complete_reply(result)
            first = station.advance_rescue(JOB, connection)
        final = station.rescue_status(JOB)
        issued = {}
        for name in final["completed"]:
            report = station.issue(name)
            if isinstance(report, IssuedReport):
                issued[name] = _describe(report)
        pending = {name: station.issue(name).slots
                   for name in SECTIONS if name not in issued}
        return {
            "dispatch_order": dispatch_order,
            "status": {"used": final["used"], "remaining": final["remaining"],
                       "completed": sorted(final["completed"]),
                       "targets": sorted(final["targets"])},
            "issued": issued,
            "pending": pending,
        }


def _overwrite_41(station):
    for raw in (13, 17):
        station.record(RECORDING, raw)


def _describe(issued):
    worksheet = issued.report.worksheet
    return {
        "number": issued.number,
        "total_micrometres": str(worksheet.totals.micrometres),
        "rows": [{"slot": row.slot, "micrometres": None if row.micrometres is None
                  else str(row.micrometres),
                  "request_id": None if row.provenance is None
                  else row.provenance.request_id}
                 for row in worksheet.rows],
    }


def probe_resume(path, stage):
    """Reclaim an accepted-then-failed dispatch, then issue and re-retrieve it."""
    device = LocalDevice(2, 40)
    with Station(path, device) as station:
        station.open_shift(SHIFT, RECORDING, 0, 1, 1)
        station.define_section("B41", SHIFT, 41, 41)
        connection = station.start(RECORDING)
        for raw in (8, 11):
            station.record(RECORDING, raw)
        # A parallel ordinary read of the same slot fails explicitly first.
        ordinary = station.acquire(connection, RECORDING, 41)
        assert isinstance(ordinary, ReadTicket)
        assert station.complete_reply(_receive(station, ordinary, succeeded=False)) \
            is Completion.FAILED
        station.register_rescue(JOB, RECORDING, {"B41": 1}, 1)
        request_read = device.port.request_read
        orphan = []
        with deny_next_sql(station.db, stage) as arm:
            def observe(*args, **kwargs):
                ticket = request_read(*args, **kwargs)
                assert isinstance(ticket, ReadTicket)
                orphan.append(ticket)
                arm()
                return ticket

            with patch.object(device.port, "request_read", side_effect=observe):
                try:
                    station.advance_rescue(JOB, connection)
                except sqlite3.DatabaseError:
                    assert len(orphan) == 1
                else:
                    raise AssertionError("expected the armed SQLite failure")
        pending_before = station.issue("B41")
        if not isinstance(pending_before, PendingIssue):
            raise AssertionError("unresolved slot must keep the section pending")
        _overwrite_41(station)
    with Station(path, device) as reopened:
        status = reopened.rescue_status(JOB)
        if status["used"] != 1 or status["remaining"] != 0:
            raise AssertionError("reopening must reclaim the original deduction")
        ticket = status["pending"]
        assert ticket is not None and ticket.request_id == orphan[0].request_id
        reread = reopened.advance_rescue(JOB, connection)
        if reread["used"] != 1 or reread["pending"] != ticket:
            raise AssertionError("a spent budget must not dispatch a new read")
        reopened.queue_reply(ticket)
        reply = device.events.run_next()
        assert isinstance(reply, ReadResult) and reply.raw == 11
        completion = reopened.complete_reply(reply)
        issued = reopened.issue("B41")
        if not isinstance(issued, IssuedReport):
            raise AssertionError("the reclaimed reply must settle the section")
        duplicated = _receive(reopened, ticket)
        duplicate = reopened.complete_reply(duplicated)
        reopened.terminate(connection)
        reopened.end_recording(RECORDING)
        number = issued.number
    with Station(path) as offline:
        retrieved = offline.retrieve(number)
        if retrieved is None:
            raise AssertionError("the report must be retrievable offline")
        return {
            "stage": stage,
            "before_reopen": getattr(pending_before, "slots", None),
            "reclaimed": {"used": status["used"], "remaining": status["remaining"],
                          "request_id": ticket.request_id},
            "completion": completion.value,
            "duplicate": duplicate.value,
            "issued": _describe(issued),
            "retrieved_total": str(retrieved.report.worksheet.totals.micrometres),
        }



def probe_v1(path, stage):
    """Observe ordinary reads with the original APIs and an empty station."""
    device = LocalDevice(2, 40)
    orphan = []
    with Station(path, device) as station:
        station.open_shift(SHIFT, RECORDING, 0, 1, 1)
        station.define_section("B41", SHIFT, 41, 41)
        connection = station.start(RECORDING)
        for raw in (8, 11):
            station.record(RECORDING, raw)

        ordinary = station.acquire(connection, RECORDING, 41)
        assert isinstance(ordinary, ReadTicket)
        failure = _receive(station, ordinary, succeeded=False)
        assert station.complete_reply(failure) is Completion.FAILED

        if stage == "unavailable":
            _overwrite_41(station)
            assert station.acquire(connection, RECORDING, 41) is SlotAvailability.OVERWRITTEN
        else:
            request_read = device.port.request_read
            with deny_next_sql(station.db, stage) as arm:
                def observe(*args, **kwargs):
                    ticket = request_read(*args, **kwargs)
                    assert isinstance(ticket, ReadTicket)
                    orphan.append(ticket)
                    arm()
                    return ticket

                with patch.object(device.port, "request_read", side_effect=observe):
                    try:
                        station.acquire(connection, RECORDING, 41)
                    except sqlite3.DatabaseError:
                        assert len(orphan) == 1
                    else:
                        raise AssertionError("expected the armed SQLite failure")
            _overwrite_41(station)

    with Station(path, device) as reopened:
        assert reopened.sessions.is_current(connection)
        rows = reopened.db.execute(
            "SELECT request_id, state FROM read_attempts ORDER BY request_id"
        ).fetchall()
        assert rows == [(ordinary.request_id, "failed")]
        assert reopened.find_observation(RECORDING, 41) is None
        issued = reopened.issue("B41")
        row, = issued.report.worksheet.rows
        assert row.state is SlotState.MISSING
        returned_raw = None
        outcome = None
        if orphan:
            reply = _receive(reopened, orphan[0])
            returned_raw = reply.raw
            outcome = reopened.complete_reply(reply)
            assert returned_raw == 11 and outcome is Completion.REJECTED
        return {
            "stage": stage, "station_attempts": rows,
            "device_ticket": orphan[0].request_id if orphan else None,
            "returned_raw": returned_raw, "completion": outcome,
            "slot_41_report_state": row.state,
        }


def probe_contrasts(root):
    """Compare device rejection with acceptance-then-connection-termination."""
    root.mkdir(parents=True, exist_ok=True)
    results = {}
    for label in ("rejected", "terminated"):
        device = LocalDevice(2, 40)
        with Station(root / f"{label}.sqlite3", device) as station:
            station.open_shift(SHIFT, RECORDING, 0, 1, 1)
            station.define_section("B41", SHIFT, 41, 41)
            connection = station.start(RECORDING)
            for raw in (8, 11):
                station.record(RECORDING, raw)
            station.register_rescue(JOB, RECORDING, {"B41": 1}, 1)
            if label == "rejected":
                with patch.object(device.port, "request_read",
                                  return_value=SlotAvailability.OVERWRITTEN):
                    state = station.advance_rescue(JOB, connection)
                results[label] = {"used": state["used"],
                                  "remaining": state["remaining"],
                                  "pending": state["pending"] is not None}
            else:
                state = station.advance_rescue(JOB, connection)
                ticket = state["pending"]
                assert ticket is not None
                station.terminate(connection)
                stale = station.complete_reply(_receive(station, ticket))
                after = station.rescue_status(JOB)
                results[label] = {"used": after["used"],
                                  "remaining": after["remaining"],
                                  "pending": after["pending"] is not None,
                                  "stale": stale.value}
    return results


def main():
    with TemporaryDirectory(prefix="vibration-rescue-fixture-") as directory:
        root = Path(directory)
        device = LocalDevice(3, 40)
        with Station(root / "overlap.sqlite3", device) as station:
            seed_overlap(station)
            readable = [slot for slot in range(40, 43) if
                        device.recorder.slot_availability(RECORDING, slot)
                        is SlotAvailability.READABLE]
            assert readable == [40, 41, 42]
        observations = [probe_v1(root / f"{stage}.sqlite3", stage)
                        for stage in ("unavailable", "write", "commit")]
        rescue = probe_rescue(root / "rescue.sqlite3")
        rescued_failed = probe_rescue(root / "rescue-failed.sqlite3", fail_first=True)
        resume = [probe_resume(root / f"resume-{stage}.sqlite3", stage)
                  for stage in ("write", "commit")]
        contrasts = probe_contrasts(root / "contrast")
        print(json.dumps({"overlap": {"sections": SECTIONS, "weights": WEIGHTS,
                                      "budget": BUDGET, "readable": readable},
                          "ordinary_read_probe": observations,
                          "rescue": rescue, "rescue_failed_first": rescued_failed,
                          "resume": resume, "contrasts": contrasts}, sort_keys=True))


if __name__ == "__main__":
    main()
