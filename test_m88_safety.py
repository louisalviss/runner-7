import unittest

from m88_safety import adaptive_discovery_floor, configured_min_discovery, outright_quality


class M88SafetyTests(unittest.TestCase):
    def test_adaptive_floor_tracks_last_good_without_blind_absolute_lowering(self):
        self.assertEqual(adaptive_discovery_floor(43, 0.80), 35)
        self.assertEqual(adaptive_discovery_floor(30, 0.80), 25)

    def test_configured_floor_never_drops_below_absolute_minimum(self):
        self.assertEqual(configured_min_discovery('35'), 35)
        self.assertEqual(configured_min_discovery('12'), 25)
        self.assertEqual(configured_min_discovery(None), 40)

    def test_quality_accepts_high_coverage_current_board(self):
        outs={
            'exact_operator_odds': True,
            'markets': [{}] * 34,
            'failures': [{}],
            'coverage': {'discovered_markets': 35, 'capture_ratio': 34/35},
        }
        got=outright_quality(outs)
        self.assertTrue(got['ok'])
        self.assertEqual(got['required_captured'], 32)

    def test_quality_rejects_low_coverage_even_if_discovery_floor_passes(self):
        outs={
            'exact_operator_odds': True,
            'markets': [{}] * 31,
            'failures': [{}] * 4,
            'coverage': {'discovered_markets': 35, 'capture_ratio': 31/35},
        }
        self.assertFalse(outright_quality(outs)['ok'])


if __name__ == '__main__':
    unittest.main()
