import unittest

from build_m88_ai_database import outright_quality


def fixture(discovered, captured, failed, floor, ratio):
    return {
        'exact_operator_odds': True,
        'markets': [{} for _ in range(captured)],
        'failures': [{} for _ in range(failed)],
        'coverage': {
            'discovery_floor': floor,
            'discovered_markets': discovered,
            'capture_ratio': ratio,
        },
    }


class M88FloorContractTests(unittest.TestCase):
    def test_35_board_with_one_capture_failure_passes(self):
        self.assertTrue(outright_quality(fixture(35, 34, 1, 25, 34/35))['ok'])

    def test_36_board_with_one_capture_failure_passes(self):
        self.assertTrue(outright_quality(fixture(36, 35, 1, 28, 35/36))['ok'])

    def test_snapshot_cannot_lower_floor_to_25(self):
        q = outright_quality(fixture(34, 34, 0, 25, 1.0))
        self.assertEqual(q['discovery_floor'], 35)
        self.assertFalse(q['ok'])

    def test_low_coverage_rejected(self):
        self.assertFalse(outright_quality(fixture(35, 31, 4, 35, 31/35))['ok'])

if __name__ == '__main__':
    unittest.main()
