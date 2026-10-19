import csv
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ppg import CsvStore, analyze, parse_line, signal_data


def synthetic(seconds=65):
    return [(i, i * 10, int(65000 + 1000 * math.sin(2 * math.pi * 1.2 * i / 100)),
             int(95000 + 1800 * math.sin(2 * math.pi * 1.2 * i / 100))) for i in range(int(seconds * 100))]


class Tests(unittest.TestCase):
    def test_protocol(self):
        self.assertEqual(parse_line('PPG,237,2370,1266,1437'), (237, 2370, 1266, 1437))
        self.assertIsNone(parse_line('ESP-ROM:esp32c3'))
        with self.assertRaises(ValueError):
            parse_line('PPG,1,10,300000,0')

    def test_known_pulse_prv_and_ibi(self):
        f, peaks, ibis, accepted = analyze(synthetic())
        self.assertAlmostEqual(f['bpm'], 72, delta=1)
        self.assertIsNotNone(f['prv_rmssd_ms'])
        self.assertEqual(len(ibis), len(peaks) - 1)
        self.assertTrue(accepted.all())
        self.assertEqual(f['sample_rate_hz'], 100.)
        self.assertIsNone(analyze(synthetic(30))[0]['prv_rmssd_ms'])

    def test_missing_and_timestamp_reset_reject_prv(self):
        samples = synthetic()
        del samples[3000]
        result = analyze(samples)[0]
        self.assertEqual(result['missing_samples'], 1)
        self.assertIsNone(result['bpm'])
        self.assertIsNone(result['prv_rmssd_ms'])
        samples[4000] = (samples[4000][0], 0, 60000, 90000)
        self.assertGreater(analyze(samples)[0]['discontinuities'], 1)

    def test_flat_signal_is_not_a_pulse(self):
        samples = [(i, i * 10, 65000, 95000) for i in range(6500)]
        self.assertIsNone(analyze(samples)[0]['bpm'])
        self.assertEqual(analyze(samples)[0]['quality'], 0)

    def test_filter_preserves_raw_and_separates_segments(self):
        samples = synthetic(15)
        original = list(samples)
        del samples[700:710]
        view = signal_data(samples)
        self.assertEqual(len(view['breaks']), 1)
        self.assertTrue(np.isfinite(view['filtered']).all())
        self.assertEqual(samples[:700], original[:700])
        np.testing.assert_array_equal(view['raw'][:, 1], [s[3] for s in samples])
        self.assertIsNone(signal_data([]))

    def test_csv_roundtrip_and_label_edit_preserve_raw_and_ibi(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as d:
            store = CsvStore(d)
            samples = synthetic()
            path = store.save(samples, {'phase': 'before', 'fatigue_before': 3, 'mental_fatigue': 3,
                                       'note': 'Türkçe not\nikinci satır', 'caffeine': False}, 'DEMO', 65)
            raw, meta = store.read(path)
            self.assertEqual(raw, samples)
            self.assertEqual(meta['source'], 'DEMO')
            self.assertEqual(meta['caffeine'], 'False')
            self.assertEqual(meta['note'], 'Türkçe not\nikinci satır')
            with path.open(newline='', encoding='utf-8-sig') as f:
                before = list(csv.DictReader(f))
            self.assertTrue(any(row['ibi_ms'] for row in before))
            store.update_labels(path, {'fatigue_before': 5, 'mental_fatigue': 5, 'note': 'Yeni not'})
            raw2, meta2 = store.read(path)
            self.assertEqual(raw2, samples)
            self.assertEqual(meta2['fatigue_before'], '5')
            with path.open(newline='', encoding='utf-8-sig') as f:
                after = list(csv.DictReader(f))
            self.assertEqual([r['ibi_ms'] for r in before], [r['ibi_ms'] for r in after])
            self.assertTrue(all(r['note'] == 'Yeni not' for r in after))
            self.assertEqual(len(store.list_records()[0]), 1)

    def test_failed_replace_leaves_original_intact(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as d:
            store = CsvStore(d)
            path = store.save(synthetic(1), {'fatigue_before': 2}, 'DEMO', 1)
            before = path.read_bytes()
            with patch('ppg.os.replace', side_effect=PermissionError('locked')):
                with self.assertRaises(PermissionError):
                    store.update_labels(path, {'fatigue_before': 8})
            self.assertEqual(before, path.read_bytes())
            self.assertFalse(list(Path(d).glob('*.tmp')))


if __name__ == '__main__':
    unittest.main()
