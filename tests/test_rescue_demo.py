import unittest

from station import run_rescue_demo


class RescueDemoTests(unittest.TestCase):
    def test_rescue_demo_reports_and_retrieves(self):
        result = run_rescue_demo()
        self.assertEqual(result['used'], 2)
        self.assertEqual(result['remaining'], 0)
        self.assertEqual(result['completed'], ['B', 'C'])
        self.assertEqual(result['read_slots'], [41, 42])
        self.assertEqual(result['issued']['B']['total_micrometres'], '24')
        self.assertEqual(result['issued']['C']['total_micrometres'], '13')
        shared = result['issued']['B']['rows'][1]['source']
        tail = result['issued']['C']['rows'][0]['source']
        self.assertEqual(shared, tail, 'shared slot 42 must share one provenance')
        self.assertEqual(result['retrieved'], result['issued'],
                         'offline retrieval must match')
