"""Fixtures for overlapping sections and the V1 read/commit boundary."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from assessment.coverage import SlotState
from capture.admission import Completion
from device.read_port import ReadResult, ReadTicket
from device.recorder import SlotAvailability
from station import LocalDevice, Station


RECORDING = "rescue-strip"
SHIFT = "rescue-day"
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


def _overwrite_41(station):
    for raw in (13, 17):
        station.record(RECORDING, raw)


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
        print(json.dumps({"overlap": {"sections": SECTIONS, "weights": WEIGHTS,
                                      "budget": BUDGET, "readable": readable},
                          "ordinary_read_probe": observations}, sort_keys=True))


if __name__ == "__main__":
    main()
