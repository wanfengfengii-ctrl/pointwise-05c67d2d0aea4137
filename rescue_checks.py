"""Business checks for the rescue job that complement the unit-test suite.

Each check drives the real station/device code and asserts observable results:
rescue re-selection over overlapping sections, re-admission of an accepted
ticket after a station write/commit failure, and the issued/offline handoff.
"""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import rescue_fixture


def _check_selection(root: Path) -> dict:
    result = rescue_fixture.probe_rescue(root / 'reselect.sqlite3')
    status = result['status']
    assert status['used'] == rescue_fixture.BUDGET, 'budget must be fully spent'
    assert status['remaining'] == 0, 'no quota may remain'
    assert status['completed'] == ['B', 'C'], 'overlap must fill B and C'
    assert result['dispatch_order'] == [41, 42], 'shared slot 42 read once'
    middle = result['issued']['B']['rows']
    tail = result['issued']['C']['rows']
    assert [str(row['micrometres']) for row in middle] == ['11', '13'], \
        'section B must total 11 and 13'
    assert result['issued']['B']['total_micrometres'] == '24'
    assert result['issued']['C']['total_micrometres'] == '13'
    assert tail[0]['request_id'] == middle[1]['request_id'], \
        'shared slot 42 must keep one provenance'
    return {'completed': status['completed'], 'used': status['used'],
            'dispatch_order': result['dispatch_order'],
            'B_total': result['issued']['B']['total_micrometres'],
            'C_total': result['issued']['C']['total_micrometres']}


def _check_failed_first(root: Path) -> dict:
    result = rescue_fixture.probe_rescue(root / 'reselect-failed.sqlite3',
                                         fail_first=True)
    assert result['status']['used'] == rescue_fixture.BUDGET
    assert result['status']['completed'] == ['C'], 'a failed first read leaves only C'
    assert result['issued']['C']['total_micrometres'] == '13'
    assert result['pending']['B'] == (41,), 'B still waits for slot 41'
    return {'completed': result['status']['completed'],
            'pending_B': list(result['pending']['B'])}


def _check_resume(root: Path) -> list[dict]:
    checked = []
    for stage in ('write', 'commit'):
        result = rescue_fixture.probe_resume(root / f'resume-{stage}.sqlite3', stage)
        assert result['reclaimed'] == {'used': 1, 'remaining': 0,
                                       'request_id': result['reclaimed']['request_id']}
        assert result['completion'] == 'accepted', 'reclaimed ticket must settle'
        assert result['duplicate'] == 'duplicate', 'replays must not deduct again'
        assert result['issued']['rows'][0]['micrometres'] == '11'
        assert result['retrieved_total'] == '11', 'offline retrieval must match'
        checked.append({'stage': stage, 'used': result['reclaimed']['used'],
                        'completion': result['completion'],
                        'duplicate': result['duplicate'],
                        'retrieved_total': result['retrieved_total']})
    return checked


def _check_contrasts(root: Path) -> dict:
    result = rescue_fixture.probe_contrasts(root / 'contrast')
    rejected = result['rejected']
    assert rejected == {'used': 0, 'remaining': 1, 'pending': False}, \
        'a device rejection must not deduct or leave a pending ticket'
    terminated = result['terminated']
    assert terminated['used'] == 1, 'acceptance before termination still deducts'
    assert terminated['stale'] == 'stale', 'a terminated ticket cannot return samples'
    assert terminated['pending'] is False
    return result


def run() -> dict:
    with TemporaryDirectory(prefix='vibration-rescue-check-') as directory:
        root = Path(directory)
        report = {
            'reselect': _check_selection(root),
            'reselect_failed_first': _check_failed_first(root),
            'resume': _check_resume(root),
            'contrasts': _check_contrasts(root),
        }
    print('RESCUE_CHECK ' + json.dumps(report, ensure_ascii=False, sort_keys=True))
    return report


if __name__ == '__main__':
    run()
