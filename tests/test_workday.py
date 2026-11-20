import json
import unittest
from pathlib import Path

from station import run_workday


class WorkdayTests(unittest.TestCase):
    def test_workday_from_recording_to_offline_handoff(self):
        fixture = Path(__file__).resolve().parents[1] / 'data' / 'workday.json'
        with fixture.open(encoding='utf-8') as stream:
            sample = json.load(stream)
        actual = run_workday(sample)
        expected_report = {
            'number': 'VR-day-040-042',
            'receipt': 'ACK-VR-day-040-042',
            'valid_count': 2,
            'total_micrometres': '24',
            'rows': [
                {'slot': 40, 'state': 'missing', 'raw': None, 'micrometres': None, 'source': None},
                {'slot': 41, 'state': 'valid', 'raw': 11, 'micrometres': '11',
                 'source': {'recording_id': 'strip-01', 'slot': 41, 'generation': 1,
                            'request_id': 1, 'physical_cell': 1}},
                {'slot': 42, 'state': 'valid', 'raw': 13, 'micrometres': '13',
                 'source': {'recording_id': 'strip-01', 'slot': 42, 'generation': 1,
                            'request_id': 2, 'physical_cell': 0}},
            ],
        }
        self.assertEqual(actual['pending_slots'], [41])
        self.assertEqual(actual['issued'], expected_report)
        self.assertEqual(actual['retrieved'], expected_report)
        evidence = {
            'case_id': 'v3',
            'input': sample,
            'expected': {'pending_slots': [41], 'report': expected_report},
            'actual': {'pending_slots': actual['pending_slots'], 'report': actual['issued']},
            'downstream_expected': expected_report,
            'downstream_actual': actual['retrieved'],
            'code_paths': ['device/read_port.py', 'capture/admission.py', 'capture/outcomes.py',
                           'assessment/coverage.py', 'assessment/worksheet.py',
                           'archive/publish.py', 'archive/retrieve.py'],
            'oracle': '手工按两格回卷推导原槽 40、41 已覆盖，只有 41 有旧票据。按零点 0 和比例 1 得 11 + 13 = 24，并按原槽、格号及发读次序核对出处。',
        }
        print('POINTWISE_SCENARIO ' + json.dumps(evidence, ensure_ascii=False, sort_keys=True))
