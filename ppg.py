"""PPG serial acquisition, exploratory PRV and self-contained CSV records."""
import csv
import math
import os
import random
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np
import serial
from scipy.signal import butter, find_peaks, sosfiltfilt
from PySide6.QtCore import QThread, Signal

FORMAT = 'ppg-research-v1'
LABEL_KEYS = ['task_id', 'day_period', 'phase', 'fatigue_before', 'fatigue_after', 'mental_fatigue',
              'sleep_hours', 'sleepiness', 'stress', 'physical_fatigue', 'task_difficulty',
              'study_minutes', 'caffeine', 'exercise', 'note', 'label_time', 'labels_updated_at']
FEATURE_KEYS = ['sample_rate_hz', 'duration_s', 'sample_count', 'missing_samples',
                'discontinuities', 'quality', 'bpm', 'prv_rmssd_ms', 'prv_sdnn_ms',
                'analysis_status', 'requested_duration_s', 'complete']
RAW_KEYS = ['seq', 'device_ms', 'elapsed_s', 'red', 'ir', 'peak', 'ibi_ms', 'ibi_accepted']
HEADER = RAW_KEYS + ['format', 'record_id', 'created_at', 'source', 'baud_rate'] + LABEL_KEYS + FEATURE_KEYS


def now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def parse_line(line):
    parts = line.strip().split(',')
    if len(parts) != 5 or parts[0] != 'PPG':
        return None
    seq, ms, red, ir = map(int, parts[1:])
    if min(seq, ms, red, ir) < 0 or max(red, ir) > 262143:
        raise ValueError('Geçersiz PPG sayımı')
    return seq, ms, red, ir


def signal_data(samples):
    """Filter contiguous segments separately; NaN separates gaps in plotted curves."""
    if len(samples) == 0:
        return None
    a = np.asarray(samples, dtype=float)
    t = (a[:, 1] - a[0, 1]) / 1000
    dt = np.diff(t)
    ds = np.diff(a[:, 0])
    breaks = np.flatnonzero((ds != 1) | (dt <= 0) | (dt > .05)) + 1
    positive = dt[(dt > 0) & (dt <= .05)]
    fs = float(1 / np.median(positive)) if positive.size else None
    filtered = np.full((len(a), 2), np.nan)
    if fs is not None and 20 <= fs <= 500:
        sos = butter(3, [.5, 4], fs=fs, btype='bandpass', output='sos')
        for indices in np.split(np.arange(len(a)), breaks):
            if len(indices) >= max(32, int(fs)):
                filtered[indices] = sosfiltfilt(sos, a[indices, 2:4], axis=0)
    return dict(a=a, t=t, raw=a[:, 2:4], filtered=filtered, fs=fs, breaks=breaks,
                missing=int(np.sum(np.maximum(ds - 1, 0))))


def analyze(samples):
    result = dict(sample_rate_hz=None, duration_s=0., sample_count=len(samples),
                  missing_samples=0, discontinuities=0, quality=None, bpm=None,
                  prv_rmssd_ms=None, prv_sdnn_ms=None, analysis_status='Sinyal bekleniyor')
    peaks = np.array([], dtype=int)
    ibis = np.array([], dtype=float)
    accepted = np.array([], dtype=bool)
    view = signal_data(samples)
    if view is None:
        return result, peaks, ibis, accepted
    fs, t = view['fs'], view['t']
    duration = max(0., float(t[-1] + (1 / fs if fs else 0)))
    result.update(sample_rate_hz=round(fs, 3) if fs else None, duration_s=round(duration, 3),
                  missing_samples=view['missing'], discontinuities=len(view['breaks']))
    if duration < 15:
        result['analysis_status'] = 'Nabız için en az 15 sn veri gerekir'
        return result, peaks, ibis, accepted
    if view['breaks'].size or fs is None or not 20 <= fs <= 500:
        result['analysis_status'] = 'Örnek / zaman kesintisi; PRV hesaplanmadı'
        return result, peaks, ibis, accepted
    y = view['filtered'][:, 1]
    amplitude = float(np.nanstd(y))
    if not np.isfinite(y).all() or amplitude < 10 or np.any(view['raw'] >= 262140):
        result.update(quality=0, analysis_status='Sinyal zayıf veya doygun; teması kontrol edin')
        return result, peaks, ibis, accepted
    peaks, _ = find_peaks(y, distance=max(1, int(fs * .33)), prominence=amplitude * .6)
    ibis = np.diff(t[peaks]) * 1000
    if len(ibis) < 10:
        result.update(quality=0, analysis_status='Yeterli düzenli atım bulunamadı')
        return result, peaks, ibis, accepted
    median = np.median(ibis)
    accepted = (ibis > 333) & (ibis < 1500) & (np.abs(ibis - median) < median * .25)
    ratio = float(np.mean(accepted))
    result['quality'] = round(100 * ratio)
    if ratio < .9:
        result['analysis_status'] = 'Düzensiz sinyal; hareket ve teması kontrol edin'
        return result, peaks, ibis, accepted
    result['bpm'] = round(float(60000 / np.mean(ibis[accepted])), 1)
    result['analysis_status'] = 'Atımlar izleniyor · Kalite sezgisel bir göstergedir'
    if duration >= 60 and np.all(accepted):
        result['prv_rmssd_ms'] = round(float(np.sqrt(np.mean(np.diff(ibis) ** 2))), 3)
        result['prv_sdnn_ms'] = round(float(np.std(ibis, ddof=1)), 3)
    else:
        result['analysis_status'] += ' · PRV için 60 sn kesintisiz, kabul edilmiş veri gerekir'
    return result, peaks, ibis, accepted


class CsvStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def atomic_write(path, header, rows):
        path = Path(path)
        temp_name = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', newline='', encoding='utf-8-sig',
                                             dir=path.parent, suffix='.tmp', delete=False) as f:
                temp_name = f.name
                writer = csv.DictWriter(f, fieldnames=header, extrasaction='raise')
                writer.writeheader()
                writer.writerows(rows)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_name, path)
        finally:
            if temp_name and Path(temp_name).exists():
                Path(temp_name).unlink()

    def save(self, samples, labels, source, requested, created=None, baud=115200):
        if not samples:
            raise ValueError('Boş kayıt kaydedilemez')
        created = created or now()
        features, peaks, ibis, accepted = analyze(samples)
        features['requested_duration_s'] = requested
        features['complete'] = (features['duration_s'] >= requested * .98 and
                                features['missing_samples'] == 0 and features['discontinuities'] == 0)
        record_id = uuid.uuid4().hex[:10]
        stamp = datetime.fromisoformat(created).strftime('%Y%m%d_%H%M%S')
        path = self.root / f'{stamp}_{record_id}.csv'
        meta = dict(format=FORMAT, record_id=record_id, created_at=created,
                    source=source, baud_rate=baud, **features)
        meta.update({key: labels.get(key) for key in LABEL_KEYS})
        peak_set = set(peaks)
        intervals = {int(p): (round(float(ibi), 3), bool(accepted[i]) if len(accepted) else '')
                     for i, (p, ibi) in enumerate(zip(peaks[1:], ibis))}
        def rows():
            for i, (seq, ms, red, ir) in enumerate(samples):
                ibi, valid = intervals.get(i, ('', ''))
                yield dict(seq=seq, device_ms=ms, elapsed_s=round((ms - samples[0][1]) / 1000, 3),
                           red=red, ir=ir, peak=i in peak_set, ibi_ms=ibi, ibi_accepted=valid, **meta)
        self.atomic_write(path, HEADER, rows())
        return path

    def list_records(self):
        records, errors = [], []
        for path in self.root.glob('*.csv'):
            try:
                with path.open(newline='', encoding='utf-8-sig') as f:
                    row = next(csv.DictReader(f), None)
                if row and row.get('format') == FORMAT:
                    required = {'record_id', 'created_at', 'phase', 'source', 'duration_s',
                                'sample_count', 'missing_samples', 'analysis_status'}
                    if not required.issubset(row):
                        errors.append((path.name, 'Zorunlu CSV sütunları eksik'))
                        continue
                    row['path'] = str(path)
                    records.append(row)
            except (OSError, csv.Error, UnicodeError) as e:
                errors.append((path.name, str(e)))
        records.sort(key=lambda r: r['created_at'], reverse=True)
        return records, errors

    def read(self, path):
        with Path(path).open(newline='', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            first = None
            samples = []
            for row in reader:
                if first is None:
                    first = row
                samples.append(tuple(int(row[k]) for k in ('seq', 'device_ms', 'red', 'ir')))
        if not first or first.get('format') != FORMAT:
            raise ValueError('Bu dosya uygulamanın CSV kayıt biçiminde değil')
        return samples, first

    def update_labels(self, path, labels):
        # Preserve every raw value, feature and unknown column verbatim.
        with Path(path).open(newline='', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            header = list(reader.fieldnames or [])
            rows = list(reader)
        if not rows or rows[0].get('format') != FORMAT:
            raise ValueError('Geçersiz CSV kaydı')
        for key in LABEL_KEYS:
            if key not in header:
                header.append(key)
        for row in rows:
            row.update({key: labels.get(key, row.get(key, '')) for key in LABEL_KEYS})
            row['labels_updated_at'] = now()
        self.atomic_write(path, header, rows)


class Acquisition(QThread):
    samples = Signal(list)
    message = Signal(str)
    def __init__(self, port=None, baud=115200):
        super().__init__()
        self.port, self.baud = port, baud

    def run(self):
        connection = None
        buffer = []
        start = time.monotonic()
        emitted = start
        seq = 0
        first_sample = True
        try:
            if self.port:
                connection = serial.Serial(baudrate=self.baud, timeout=.1)
                connection.port = self.port
                connection.dtr, connection.rts = True, False
                connection.open()
                self.message.emit(f'{self.port} açık · Sinyal bekleniyor')
            else:
                self.message.emit('DEMO · Sentetik sinyal, gerçek ölçüm değil')
            while not self.isInterruptionRequested():
                if connection:
                    raw = connection.readline(512)
                    if raw:
                        text = raw.decode('ascii', errors='replace').strip()
                        try:
                            sample = parse_line(text)
                        except ValueError:
                            sample = None
                        if sample:
                            buffer.append(sample)
                            if first_sample:
                                self.message.emit(f'{self.port} · Canlı PPG verisi alınıyor')
                                first_sample = False
                        elif text.startswith('ERR,'):
                            self.message.emit(text)
                else:
                    target = int((time.monotonic() - start) * 100)
                    while seq < target:
                        t = seq / 100
                        pulse = math.sin(2 * math.pi * 1.2 * t) + .2 * math.sin(4 * math.pi * 1.2 * t)
                        buffer.append((seq, seq * 10, int(65000 + 1100 * pulse + random.gauss(0, 8)),
                                       int(95000 + 1800 * pulse + random.gauss(0, 10))))
                        seq += 1
                    self.msleep(10)
                if buffer and time.monotonic() - emitted >= .08:
                    self.samples.emit(buffer)
                    buffer = []
                    emitted = time.monotonic()
        except (OSError, serial.SerialException) as e:
            self.message.emit(f'Bağlantı açılamadı / kesildi: {e}')
        finally:
            if buffer:
                self.samples.emit(buffer)
            if connection:
                connection.close()
