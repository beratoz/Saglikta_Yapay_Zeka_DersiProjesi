import math
import tempfile
import unittest
from pathlib import Path
from main import parse_line, analyze, analyze_live, Storage


def synthetic(seconds=70):
    return [(i, i * 10, int(65000 + 1000 * math.sin(2 * math.pi * 1.2 * i / 100)),
             int(95000 + 1800 * math.sin(2 * math.pi * 1.2 * i / 100))) for i in range(seconds * 100)]


class CoreTests(unittest.TestCase):
    def test_protocol_rejects_boot_noise_and_invalid_adc(self):
        self.assertIsNone(parse_line('ESP-ROM:esp32c3'))
        self.assertEqual(parse_line('PPG,2,20,10000,12000\r\n'), (2, 20, 10000, 12000))
        with self.assertRaises(ValueError): parse_line('PPG,2,20,10000,999999')

    def test_known_72_bpm_and_flat_signal(self):
        f = analyze(synthetic())
        self.assertAlmostEqual(f['bpm'], 72, delta=1)
        self.assertIsNotNone(f['rmssd_ms'])
        self.assertIsNotNone(analyze(synthetic(60))['rmssd_ms'])
        flat = [(i, i * 10, 60000, 90000) for i in range(7000)]
        self.assertIsNone(analyze(flat)['bpm'])

    def test_missing_sample_does_not_produce_prv(self):
        samples = synthetic()
        del samples[3000]
        f = analyze(samples)
        self.assertEqual(f['gaps'], 1)
        self.assertIsNone(f['rmssd_ms'])

    def test_live_bpm_recovers_but_prv_keeps_older_gap(self):
        samples = synthetic()
        del samples[1000]
        live = analyze_live(samples)
        self.assertAlmostEqual(live['bpm'], 72, delta=1)
        self.assertIsNone(live['rmssd_ms'])
        self.assertIsNone(analyze(samples)['bpm'])

    def test_save_reload_and_dataset_preserve_demo_provenance(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as d:
            store = Storage(d)
            samples = synthetic(30)
            store.save(samples, {'fatigue': 5, 'notes': 'Türkçe ölçüm'}, analyze(samples), 'DEMO')
            row = store.sessions()[0]
            self.assertEqual(store.read(row), samples)
            store.update_labels(row['id'], {'fatigue': 7, 'notes': 'Türkçe ölçüm'})
            self.assertIn('7', store.sessions()[0]['labels'])
            self.assertEqual(store.read(row), samples)
            output = Path(d) / 'dataset.csv'
            store.export_dataset(output)
            self.assertIn('DEMO', output.read_text(encoding='utf-8-sig'))
            self.assertIn('Türkçe ölçüm', output.read_text(encoding='utf-8-sig'))
            store.db.close()


if __name__ == '__main__': unittest.main()
