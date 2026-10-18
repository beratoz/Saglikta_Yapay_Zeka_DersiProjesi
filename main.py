"""PPG veri toplama uygulamasi: python main.py"""
import csv
import json
import math
import random
import shutil
import sqlite3
import sys
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path

import numpy as np
import pyqtgraph as pg
import serial
from serial.tools import list_ports
from scipy.signal import butter, sosfiltfilt, find_peaks
from PySide6.QtCore import QThread, QTimer, Signal, QUrl, Qt
from PySide6.QtGui import QFontDatabase, QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QTabWidget, QGroupBox, QFormLayout,
    QDoubleSpinBox, QLineEdit, QCheckBox, QProgressBar, QMessageBox,
    QFileDialog, QScrollArea, QGridLayout, QFrame, QDialog, QDialogButtonBox,
)

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "settings.json"


class Storage:
    def __init__(self, root=None):
        self.root = Path(root) if root else ROOT / 'data'
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / 'sessions.sqlite3')
        self.db.execute('''CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY, created TEXT, source TEXT, labels TEXT,
            features TEXT, samples INTEGER, file TEXT)''')
        self.db.commit()

    def save(self, samples, labels, features, source, created=None):
        sid = uuid.uuid4().hex[:12]
        created = created or datetime.now().astimezone().isoformat(timespec='seconds')
        path = self.root / f'{sid}.csv'
        temp = path.with_suffix('.tmp')
        try:
            with temp.open('w', newline='', encoding='utf-8-sig') as f:
                w = csv.writer(f)
                w.writerow(['seq', 'device_ms', 'red', 'ir'])
                w.writerows(samples)
            temp.replace(path)
            self.db.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?,?)',
                            (sid, created, source, json.dumps(labels, ensure_ascii=False),
                             json.dumps(features, ensure_ascii=False), len(samples), path.name))
            self.db.commit()
        except Exception:
            self.db.rollback()
            temp.unlink(missing_ok=True)
            path.unlink(missing_ok=True)
            raise
        return sid

    def sessions(self):
        self.db.row_factory = sqlite3.Row
        return [dict(r) for r in self.db.execute('SELECT * FROM sessions ORDER BY created DESC')]

    def update_labels(self, sid, labels):
        with self.db:
            self.db.execute('UPDATE sessions SET labels=? WHERE id=?',
                            (json.dumps(labels, ensure_ascii=False), sid))

    def read(self, row):
        with (self.root / row['file']).open(encoding='utf-8-sig', newline='') as f:
            return [tuple(map(int, r)) for r in list(csv.reader(f))[1:]]

    def export_dataset(self, path):
        rows = []
        for r in self.sessions():
            rows.append(dict(session_id=r['id'], created=r['created'], source=r['source'],
                             **json.loads(r['labels']), **json.loads(r['features'])))
        if not rows:
            raise ValueError('Henüz kayıt yok.')
        columns = list(dict.fromkeys(k for r in rows for k in r))
        with open(path, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.DictWriter(f, fieldnames=columns)
            w.writeheader()
            w.writerows(rows)


def analyze(samples):
    """Exploratory PRV estimates. Never interpolate missing beats silently."""
    empty = dict(bpm=None, rmssd_ms=None, sdnn_ms=None, quality=0,
                 status="En az 15 saniye sinyal bekleniyor", duration_s=0, gaps=0)
    if len(samples) < 2:
        return empty
    t = np.array([s[1] for s in samples], dtype=float) / 1000
    x = np.array([s[3] for s in samples], dtype=float)
    dt = np.diff(t)
    duration = t[-1] - t[0] + (np.median(dt) if np.all(dt > 0) else 0)
    gaps = sum(b[0] != a[0] + 1 for a, b in zip(samples, samples[1:]))
    empty.update(duration_s=round(duration, 2), gaps=int(gaps))
    if duration < 15 or np.any(dt <= 0):
        return empty
    fs = 1 / np.median(dt)
    if fs < 20 or fs > 500 or np.max(dt) > .05 or gaps:
        return dict(empty, status="Örnek kaybı / zamanlama sorunu")
    if np.mean(x) < 1000 or np.any(x >= 262140):
        return dict(empty, status="Parmak teması yok veya sinyal doygun")
    y = sosfiltfilt(butter(3, [.5, 4], fs=fs, btype="bandpass", output="sos"), x)
    amplitude = np.std(y)
    if amplitude < 10:
        return dict(empty, status="Sinyal genliği yetersiz")
    peaks, _ = find_peaks(y, distance=int(fs * .33), prominence=amplitude * .6)
    intervals = np.diff(t[peaks]) * 1000
    if len(intervals) < 10:
        return dict(empty, status="Yeterli atım bulunamadı")
    valid = (intervals > 333) & (intervals < 1500)
    median = np.median(intervals)
    valid &= np.abs(intervals - median) < median * .25
    fraction = np.mean(valid)
    quality = int(100 * fraction)
    result = dict(empty, quality=quality, status="Kullanılabilir (sezgisel)" if fraction >= .9 else "Hareket / düzensiz atım; yeniden ölçün")
    if fraction < .9:
        return result
    result['bpm'] = round(60000 / np.mean(intervals[valid]), 1)
    # PRV is reported only for >=60 s and an entirely accepted beat series.
    if duration >= 60 and np.all(valid):
        result['rmssd_ms'] = round(float(np.sqrt(np.mean(np.diff(intervals) ** 2))), 2)
        result['sdnn_ms'] = round(float(np.std(intervals, ddof=1)), 2)
    return result


def analyze_live(samples):
    result = analyze(samples[-2000:])
    if result['bpm'] is not None and len(samples) >= 6000:
        full = analyze(samples)
        result['rmssd_ms'] = full['rmssd_ms']
        result['sdnn_ms'] = full['sdnn_ms']
    return result


def parse_line(line):
    parts = line.strip().split(',')
    if len(parts) != 5 or parts[0] != 'PPG':
        return None
    seq, ms, red, ir = map(int, parts[1:])
    if min(seq, ms, red, ir) < 0 or max(red, ir) > 262143:
        raise ValueError('Veri aralık dışında')
    return seq, ms, red, ir


class Acquisition(QThread):
    batch = Signal(list)
    message = Signal(str)

    def __init__(self, port=None):
        super().__init__()
        self.port = port

    def run(self):
        connection = None
        buffer = []
        last_emit = time.monotonic()
        last_data = last_emit
        last_warning = last_emit
        streaming = False
        seq = 0
        start = last_emit
        try:
            if self.port:
                connection = serial.Serial(baudrate=115200, timeout=.1)
                connection.port = self.port
                connection.rts = False
                connection.dtr = True
                connection.open()
                self.message.emit(f'{self.port} açık — PPG verisi bekleniyor')
            else:
                self.message.emit('DEMO — sentetik sinyal, gerçek ölçüm değil')
            while not self.isInterruptionRequested():
                if connection:
                    raw = connection.readline(256)
                    try:
                        sample = parse_line(raw.decode('ascii', errors='replace'))
                    except ValueError:
                        sample = None
                    if raw.startswith(b'ERR,'):
                        self.message.emit(raw.decode('ascii', errors='replace').strip())
                    if sample:
                        if not streaming:
                            self.message.emit(f'{self.port} • Canlı veri alınıyor')
                            streaming = True
                        buffer.append(sample)
                        last_data = time.monotonic()
                else:
                    target = int((time.monotonic() - start) * 100)
                    while seq < target:
                        t = seq / 100
                        pulse = math.sin(2 * math.pi * 1.2 * t) + .25 * math.sin(4 * math.pi * 1.2 * t)
                        buffer.append((seq, seq * 10, int(65000 + 1000 * pulse + random.gauss(0, 25)),
                                       int(95000 + 1800 * pulse + random.gauss(0, 35))))
                        seq += 1
                    self.msleep(10)
                now = time.monotonic()
                if buffer and now - last_emit >= .05:
                    self.batch.emit(buffer)
                    buffer = []
                    last_emit = now
                if connection and now - last_data > 3 and now - last_warning > 3:
                    streaming = False
                    self.message.emit('3 saniyedir geçerli PPG verisi yok. Firmware / sensör bağlantısını kontrol edin.')
                    last_warning = now
        except (serial.SerialException, OSError) as e:
            self.message.emit(f'Bağlantı hatası: {e}')
        finally:
            if buffer:
                self.batch.emit(buffer)
            if connection:
                connection.close()


STYLE = '''
QWidget { background:#101723; color:#e3eaf3; font-family:'Segoe UI'; font-size:13px; }
QMainWindow { background:#101723; }
QLabel#title { font-size:29px; font-weight:700; }
QLabel#subtitle { color:#93a5bd; }
QGroupBox { border:1px solid #2b3a50; border-radius:10px; margin-top:14px; padding:15px 10px 10px; font-weight:600; }
QGroupBox::title { subcontrol-origin:margin; left:14px; padding:0 5px; color:#90a8c7; }
QPushButton { background:#24344c; border:1px solid #354c69; border-radius:7px; padding:9px 14px; }
QPushButton:hover { background:#334b6c; }
QPushButton:disabled { color:#627084; background:#192332; }
QPushButton#primary { background:#137d72; border:1px solid #20af9e; font-weight:700; }
QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox { background:#182436; padding:7px; border:1px solid #34465f; border-radius:5px; }
QTabWidget::pane { border:0; }
QTabBar::tab { padding:12px 22px; background:#182436; margin-right:5px; }
QTabBar::tab:selected { color:#5ce2cb; background:#24344c; }
QProgressBar { border:0; background:#24344c; border-radius:5px; text-align:center; min-height:22px; }
QProgressBar::chunk { background:#168d80; border-radius:5px; }
QCheckBox::indicator { width:14px; height:14px; border:1px solid #627b9c; border-radius:3px; }
QCheckBox::indicator:checked { background:#20af9e; }
QFrame#card { background:#182436; border:1px solid #34465f; border-radius:10px; }
QFrame#card QLabel, QFrame#card QCheckBox { background:transparent; }
QFrame#card QPushButton { padding:7px; }
'''


def button(text, callback, primary=False):
    b = QPushButton(text)
    b.clicked.connect(callback)
    if primary:
        b.setObjectName('primary')
    return b


class LabelForm(QWidget):
    """Aynı form yeni ölçümlerde ve kayıt düzenlemede kullanılır."""

    def __init__(self, values=None):
        super().__init__()
        self.original = values or {}
        self.fields = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        basic = QFormLayout()
        layout.addLayout(basic)

        def choice(form, key, title, items, default=None):
            widget = QComboBox()
            for text, value in items:
                widget.addItem(text, value)
            widget.setCurrentIndex(widget.findData(self.original.get(key, default)))
            widget.setPlaceholderText('Seçin')
            self.fields[key] = widget
            form.addRow(title, widget)

        def number(form, key, title, limit, suffix):
            widget = QDoubleSpinBox()
            widget.setRange(-1, limit)
            widget.setDecimals(1 if key == 'sleep_hours' else 0)
            widget.setSpecialValueText('Belirtilmedi')
            widget.setSuffix(suffix)
            value = self.original.get(key)
            widget.setValue(value if value is not None else -1)
            self.fields[key] = widget
            form.addRow(title, widget)

        def text(form, key, title, default=''):
            widget = QLineEdit(str(self.original.get(key) or default))
            self.fields[key] = widget
            form.addRow(title, widget)

        kinds = ['Ders öncesi', 'Ders sonrası', 'Sabah', 'Gece', 'Diğer']
        choice(basic, 'kind', 'Ölçüm', [(x, x) for x in kinds], 'Ders öncesi')
        choice(basic, 'fatigue', 'Zihinsel yorgunluk *', [(f'{i} / 10', i) for i in range(1, 11)])
        hint = QLabel('1: çok düşük   ·   10: çok yüksek')
        hint.setStyleSheet('color:#93a5bd; font-size:12px;')
        basic.addRow('', hint)
        number(basic, 'sleep_hours', 'Son gece uyku', 24, ' saat')
        number(basic, 'study_minutes', 'Çalışma süresi', 1440, ' dk')
        choice(basic, 'difficulty', 'Görev zorluğu', [('Belirtilmedi', None)] + [(f'{i} / 10', i) for i in range(1, 11)])
        text(basic, 'pair_id', 'Önce / sonra kodu', datetime.now().strftime('%Y-%m-%d') + '-1')
        self.fields['pair_id'].setToolTip('Aynı çalışma oturumunun önce ve sonrası için aynı kodu kullanın.')

        toggle = QPushButton('Ek bilgiler')
        toggle.setCheckable(True)
        layout.addWidget(toggle)
        extra = QWidget()
        details = QFormLayout(extra)
        details.setContentsMargins(0, 0, 0, 0)
        text(details, 'task', 'Ders / görev')
        choice(details, 'stress', 'Algılanan stres', [('Belirtilmedi', None)] + [(f'{i} / 10', i) for i in range(1, 11)])
        choice(details, 'posture', 'Ölçüm duruşu', [('Belirtilmedi', None)] + [(x, x) for x in ['Oturarak', 'Uzanarak', 'Ayakta']])
        number(details, 'rest_minutes', 'Önceki dinlenme', 120, ' dk')
        for key, title in [('caffeine', 'Son 4 saatte kafein'), ('exercise', 'Son 2 saatte egzersiz')]:
            choice(details, key, title, [('Belirtilmedi', None), ('Hayır', False), ('Evet', True)])
        text(details, 'notes', 'Not')
        layout.addWidget(extra)
        extra.hide()
        toggle.toggled.connect(extra.setVisible)

    def values(self):
        result = dict(self.original)
        for key, widget in self.fields.items():
            if isinstance(widget, QComboBox):
                result[key] = widget.currentData()
            elif isinstance(widget, QDoubleSpinBox):
                result[key] = widget.value() if widget.value() >= 0 else None
            else:
                result[key] = widget.text().strip()
        if result['fatigue'] is None:
            raise ValueError('Zihinsel yorgunluk puanını seçin.')
        return result


class Window(QMainWindow):
    def __init__(self, data_root=None):
        super().__init__()
        # Explicit font loading also supports headless Windows render checks.
        font_path = Path('C:/Windows/Fonts/segoeui.ttf')
        if font_path.exists():
            QFontDatabase.addApplicationFont(str(font_path))
        self.setWindowTitle('PPG Laboratuvarı • Zihinsel Yorgunluk Araştırması')
        self.resize(1280, 850)
        if data_root is None and CONFIG.exists():
            try:
                data_root = json.loads(CONFIG.read_text(encoding='utf-8')).get('folder')
            except (ValueError, OSError):
                pass
        self.store = Storage(data_root)
        self.worker = None
        self.live = deque(maxlen=6500)
        self.recording = False
        self.pending = None
        self.last_sample_at = 0
        self.last_seq = None
        self.setStyleSheet(STYLE)
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 18, 24, 18)
        title = QLabel('PPG Laboratuvarı')
        title.setObjectName('title')
        layout.addWidget(title)
        folder_bar = QHBoxLayout()
        self.folder_label = QLabel(str(self.store.root.resolve()))
        self.folder_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.folder_label.setWordWrap(True)
        folder_bar.addWidget(QLabel('Kayıt klasörü:'))
        folder_bar.addWidget(self.folder_label, 1)
        folder_bar.addWidget(button('Klasörü aç', self.open_folder))
        self.folder_btn = button('Değiştir', self.choose_folder)
        folder_bar.addWidget(self.folder_btn)
        layout.addLayout(folder_bar)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.build_live()
        self.build_history()
        self.status = QLabel('Hazır • Önce COM portuna bağlanın veya demo modunu açın.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.setCentralWidget(root)
        self.refresh_ports()
        self.refresh_history()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(100)
        self.analysis_counter = 0

    def build_live(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        connection = QHBoxLayout()
        self.port = QComboBox()
        self.port.setMinimumWidth(260)
        connection.addWidget(self.port)
        self.refresh_btn = button('Portları yenile', self.refresh_ports)
        connection.addWidget(self.refresh_btn)
        self.connect_btn = button('Bağlan', self.toggle_connection, True)
        connection.addWidget(self.connect_btn)
        self.demo_btn = button('Demo başlat', self.start_demo)
        connection.addWidget(self.demo_btn)
        connection.addStretch()
        self.mode = QLabel('BAĞLANTI YOK')
        connection.addWidget(self.mode)
        outer.addLayout(connection)
        content = QHBoxLayout()
        self.form_box = QGroupBox('Ölçüm bilgileri')
        self.form_box.setFixedWidth(360)
        form_container = QVBoxLayout(self.form_box)
        form_scroll = QScrollArea()
        form_scroll.setWidgetResizable(True)
        form_scroll.setFrameShape(QFrame.Shape.NoFrame)
        form_content = QWidget()
        form = QVBoxLayout(form_content)
        form.setContentsMargins(0, 0, 0, 0)
        form_scroll.setWidget(form_content)
        form_container.addWidget(form_scroll)
        self.label_form = LabelForm()
        form.addWidget(self.label_form)
        self.duration = QComboBox()
        for name, seconds in [('30 saniye', 30), ('1 dakika', 60), ('2 dakika', 120), ('5 dakika', 300)]:
            self.duration.addItem(name, seconds)
        self.duration.setCurrentIndex(3)
        form.addWidget(QLabel('Kayıt süresi'))
        form.addWidget(self.duration)
        form.addStretch()
        content.addWidget(self.form_box)
        right = QVBoxLayout()
        self.metrics = QLabel('Nabız: — BPM    |    Sinyal: bekleniyor    |    RMSSD: —')
        self.metrics.setStyleSheet('font-size:16px; color:#5ce2cb; padding:10px;')
        self.metrics.setWordWrap(True)
        right.addWidget(self.metrics)
        pg.setConfigOptions(antialias=True, background='#121e2e', foreground='#95a8c1')
        self.plot = pg.PlotWidget()
        self.plot.setLabel('left', 'Ham ADC')
        self.plot.setLabel('bottom', 'Zaman', units='s')
        self.plot.addLegend()
        self.plot.showGrid(x=True, y=True, alpha=.15)
        self.red_curve = self.plot.plot(pen=pg.mkPen('#ff6e86', width=2), name='RED')
        self.ir_curve = self.plot.plot(pen=pg.mkPen('#52dcc5', width=2), name='IR')
        right.addWidget(self.plot, 1)
        self.progress = QProgressBar()
        right.addWidget(self.progress)
        controls = QHBoxLayout()
        self.record_btn = button('●  Kaydı başlat', self.start_record, True)
        self.stop_btn = button('Durdur', self.stop_record)
        self.save_btn = button('Kaydı kaydet', self.save_record)
        self.discard_btn = button('Kaydı sil', self.discard_record)
        self.stop_btn.setEnabled(False); self.save_btn.setEnabled(False); self.discard_btn.setEnabled(False)
        for b in [self.record_btn, self.stop_btn, self.save_btn, self.discard_btn]: controls.addWidget(b)
        right.addLayout(controls)
        content.addLayout(right, 1)
        outer.addLayout(content, 1)
        self.tabs.addTab(page, 'Canlı ölçüm')

    def build_history(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        actions = QHBoxLayout()
        actions.addWidget(button('Yenile', self.refresh_history))
        actions.addWidget(button('Veri seti dışa aktar', self.export_dataset))
        actions.addWidget(button('Seçilen iki kaydı karşılaştır', self.compare))
        actions.addStretch()
        layout.addLayout(actions)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        cards = QWidget()
        self.card_grid = QGridLayout(cards)
        self.card_grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        scroll.setWidget(cards)
        layout.addWidget(scroll, 2)
        self.history_plot = pg.PlotWidget()
        self.history_plot.setLabel('bottom', 'Zaman', units='s')
        self.history_plot.setLabel('left', 'IR')
        layout.addWidget(self.history_plot, 1)
        self.history_info = QLabel('')
        self.history_info.setWordWrap(True)
        layout.addWidget(self.history_info)
        self.tabs.addTab(page, 'Kayıtlar')

    def open_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.root.resolve())))

    def choose_folder(self):
        if self.recording or self.pending:
            self.alert('Önce ölçümü durdurup kaydedin veya silin.')
            return
        path = QFileDialog.getExistingDirectory(self, 'Kayıt klasörü', str(self.store.root))
        if path:
            self.switch_folder(path)

    def switch_folder(self, path, persist=True):
        if self.recording or self.pending:
            return False
        try:
            new_store = Storage(Path(path).resolve())
            if persist:
                temp = CONFIG.with_suffix('.tmp')
                temp.write_text(json.dumps({'folder': str(new_store.root)}, ensure_ascii=False), encoding='utf-8')
                temp.replace(CONFIG)
        except Exception as e:
            if 'new_store' in locals():
                new_store.db.close()
            self.alert(f'Klasör açılamadı: {e}')
            return False
        self.store.db.close()
        self.store = new_store
        self.folder_label.setText(str(self.store.root))
        self.history_plot.clear()
        self.history_info.clear()
        self.refresh_history()
        self.status.setText('Kayıt klasörü değişti. Önceki kayıtlar eski klasörde duruyor.')
        return True

    def refresh_ports(self):
        self.port.clear()
        for p in list_ports.comports():
            self.port.addItem(f'{p.device} — {p.description}', p.device)

    def toggle_connection(self):
        if self.worker:
            self.disconnect()
        elif self.port.currentData():
            self.start_source(self.port.currentData())
        else:
            self.alert('COM portu bulunamadı. USB veri kablosunu ve Aygıt Yöneticisi’ni kontrol edin.')

    def start_demo(self):
        if not self.worker: self.start_source(None)

    def start_source(self, port):
        self.live.clear(); self.last_seq = None; self.last_sample_at = 0
        self.source = port or 'DEMO'
        self.worker = Acquisition(port)
        self.worker.batch.connect(self.receive)
        self.worker.message.connect(self.status.setText)
        self.worker.finished.connect(self.source_finished)
        self.worker.start()
        self.connect_btn.setText('Bağlantıyı kes')
        self.demo_btn.setEnabled(False); self.port.setEnabled(False); self.refresh_btn.setEnabled(False)
        self.mode.setText('DEMO • SENTETİK VERİ' if not port else f'CİHAZ • {port}')

    def disconnect(self):
        if self.recording: self.stop_record()
        if self.worker:
            self.worker.requestInterruption()
            self.worker.wait(1500)

    def source_finished(self):
        if self.recording: self.stop_record()
        self.worker = None
        self.connect_btn.setText('Bağlan')
        self.demo_btn.setEnabled(True); self.port.setEnabled(True); self.refresh_btn.setEnabled(True)
        self.mode.setText('BAĞLANTI KAPALI')

    def receive(self, samples):
        for s in samples:
            if self.last_seq is not None and s[0] <= self.last_seq:
                if self.recording:
                    self.stop_record()
                    self.status.setText('Cihaz sırası sıfırlandı; kayıt durduruldu. Kısmi kaydı inceleyin.')
                self.live.clear()
            elif self.last_seq is not None and s[0] != self.last_seq + 1:
                self.live.clear()
                self.status.setText('Örnek kesintisi algılandı; canlı analiz yeniden başlıyor.')
            self.last_seq = s[0]
            self.live.append(s)
            if self.recording:
                if self.record_start_ms is None: self.record_start_ms = s[1]
                if s[1] - self.record_start_ms < self.target_seconds * 1000:
                    self.record_samples.append(s)
                else:
                    self.stop_record()
        self.last_sample_at = time.monotonic()

    def labels(self):
        return self.label_form.values()

    def start_record(self):
        if self.pending:
            self.alert('Önce bekleyen kaydı kaydedin veya silin.'); return
        if not self.worker or time.monotonic() - self.last_sample_at > 2:
            self.alert('Kayıt için önce canlı PPG verisi alınmalı.'); return
        try:
            self.record_labels = self.labels()
        except ValueError as e:
            self.alert(str(e))
            return
        self.record_created = datetime.now().astimezone().isoformat(timespec='seconds')
        self.record_source = self.source
        self.target_seconds = self.duration.currentData()
        self.record_start_ms = None; self.record_samples = []; self.recording = True
        self.progress.setValue(0)
        self.form_box.setEnabled(False); self.record_btn.setEnabled(False); self.stop_btn.setEnabled(True)
        self.status.setText('Kayıt sürüyor. Parmağınızı sabit tutun.')

    def stop_record(self):
        if not self.recording: return
        self.recording = False
        if self.record_samples:
            self.pending = (self.record_samples, self.record_labels, self.record_source)
            self.save_btn.setEnabled(True); self.discard_btn.setEnabled(True)
        self.form_box.setEnabled(True); self.record_btn.setEnabled(True); self.stop_btn.setEnabled(False)
        self.status.setText(f'Kayıt durdu: {len(self.record_samples)} örnek. Kaydet düğmesine basın.')

    def save_record(self):
        if not self.pending: return
        samples, labels, source = self.pending
        try:
            features = analyze(samples)
            features['requested_duration_s'] = self.target_seconds
            features['complete'] = len(samples) >= self.target_seconds * 100 * .98 and features['gaps'] == 0
            sid = self.store.save(samples, labels, features, source, self.record_created)
            self.discard_record(); self.refresh_history()
            self.status.setText(f'Kayıt kaydedildi • {sid} • {len(samples)} örnek')
        except Exception as e: self.alert(f'Kaydedilemedi: {e}')

    def discard_record(self):
        self.pending = None
        self.save_btn.setEnabled(False); self.discard_btn.setEnabled(False)

    def tick(self):
        self.analysis_counter += 1
        if self.live:
            data = np.array(list(self.live)[-1500:])
            t = (data[:, 1] - data[-1, 1]) / 1000
            self.red_curve.setData(t, data[:, 2]); self.ir_curve.setData(t, data[:, 3])
            if self.analysis_counter % 20 == 0:
                if time.monotonic() - self.last_sample_at > 2:
                    self.metrics.setText('Veri akışı kesildi • Son değerler güncel değil')
                else:
                    f = analyze_live(list(self.live))
                    self.metrics.setText(f"Nabız: {f['bpm'] or '—'} BPM  |  Kalite: %{f['quality']}  |  RMSSD: {f['rmssd_ms'] or '—'} ms\n{f['status']}")
        if self.recording:
            elapsed = (self.record_samples[-1][1] - self.record_start_ms) / 1000 if self.record_samples else 0
            self.progress.setValue(min(100, int(100 * elapsed / self.target_seconds)))
            self.progress.setFormat(f'{elapsed:.1f} / {self.target_seconds} saniye')
            if time.monotonic() - self.last_sample_at > 3:
                self.stop_record()
                self.status.setText('Veri akışı kesildi. Kısmi kayıt durduruldu; kaydedebilir veya silebilirsiniz.')

    def refresh_history(self):
        self.rows = self.store.sessions()
        while self.card_grid.count():
            item = self.card_grid.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self.card_checks = {}
        if not self.rows:
            self.card_grid.addWidget(QLabel('Henüz kayıt yok.'), 0, 0)
        for i, row in enumerate(self.rows):
            labels = json.loads(row['labels'])
            features = json.loads(row['features'])
            card = QFrame()
            card.setObjectName('card')
            card.setFixedSize(285, 235)
            box = QVBoxLayout(card)
            date = datetime.fromisoformat(row['created']).strftime('%d.%m.%Y  •  %H:%M')
            check = QCheckBox(date)
            self.card_checks[row['id']] = check
            box.addWidget(check)
            box.addWidget(QLabel(labels.get('kind', 'Ölçüm')))
            box.addWidget(QLabel(f"Yorgunluk: {labels.get('fatigue', '—')}/10   •   {features['duration_s']:.0f} sn"))
            bpm = features.get('bpm')
            box.addWidget(QLabel(f"Nabız: {bpm if bpm is not None else '—'} BPM"))
            state = 'Demo' if row['source'] == 'DEMO' else ('Tam kayıt' if features.get('complete') else 'Kısmi kayıt')
            box.addWidget(QLabel(f"{state}  •  {labels.get('pair_id', '')}"))
            box.addStretch()
            actions = QHBoxLayout()
            actions.addWidget(button('İncele', lambda checked=False, r=row: self.inspect_record(r)))
            actions.addWidget(button('Düzenle', lambda checked=False, r=row: self.edit_record(r)))
            actions.addWidget(button('CSV', lambda checked=False, r=row: self.export_raw(r)))
            box.addLayout(actions)
            self.card_grid.addWidget(card, i // 3, i % 3)

    def selected(self):
        return [row for row in self.rows if self.card_checks[row['id']].isChecked()]

    def inspect_record(self, row):
        try:
            data = np.array(self.store.read(row))
            self.history_plot.clear()
            self.history_plot.plot((data[:, 1] - data[0, 1]) / 1000, data[:, 3], pen='#52dcc5')
            labels = json.loads(row['labels'])
            features = json.loads(row['features'])
            self.history_info.setText(
                f"Uyku: {labels.get('sleep_hours', '—')} saat  •  Ders: {labels.get('study_minutes', '—')} dk  •  "
                f"Zorluk: {labels.get('difficulty', '—')}/10\n"
                f"RMSSD: {features.get('rmssd_ms') or '—'} ms  •  SDNN: {features.get('sdnn_ms') or '—'} ms\n"
                f"{labels.get('notes', '')}\n{self.store.root / row['file']}")
        except Exception as e:
            self.alert(str(e))

    def edit_record(self, row):
        dialog = QDialog(self)
        dialog.setWindowTitle('Ölçüm etiketleri')
        dialog.setMinimumWidth(420)
        layout = QVBoxLayout(dialog)
        form = LabelForm(json.loads(row['labels']))
        layout.addWidget(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('Kaydet')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('Vazgeç')
        layout.addWidget(buttons)
        def save():
            try:
                self.store.update_labels(row['id'], form.values())
                dialog.accept()
            except Exception as e:
                self.alert(str(e))
        buttons.accepted.connect(save)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec():
            self.refresh_history()
            self.history_info.clear()
            self.status.setText('Etiketler güncellendi.')

    def export_raw(self, row):
        path, _ = QFileDialog.getSaveFileName(self, 'Ham CSV kaydet', row['file'], 'CSV (*.csv)')
        if path:
            try:
                original = self.store.root / row['file']
                if Path(path).resolve() != original.resolve():
                    shutil.copyfile(original, path)
                self.status.setText('CSV dışa aktarıldı.')
            except Exception as e:
                self.alert(str(e))

    def export_dataset(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Veri seti kaydet', 'ppg_dataset.csv', 'CSV (*.csv)')
        if path:
            try:
                self.store.export_dataset(path); self.status.setText('Etiketli veri seti dışa aktarıldı.')
            except Exception as e: self.alert(str(e))

    def compare(self):
        rows = self.selected()
        if len(rows) != 2: self.alert('İki kayıt kartının tarih kutusunu işaretleyin.'); return
        a, b = sorted(rows, key=lambda r: r['created'])
        la, lb = json.loads(a['labels']), json.loads(b['labels'])
        if not la['pair_id'] or la['pair_id'] != lb['pair_id']:
            self.alert('Karşılaştırma için eşleştirme kodları aynı olmalı.'); return
        if a['source'] != b['source']:
            self.alert('Karşılaştırma için veri kaynakları aynı olmalı.'); return
        fa, fb = json.loads(a['features']), json.loads(b['features'])
        lines = [f"{la['kind']} → {lb['kind']} • {la['pair_id']}"]
        names = {'fatigue': 'Yorgunluk', 'bpm': 'Nabız', 'rmssd_ms': 'RMSSD', 'sdnn_ms': 'SDNN'}
        for key in names:
            x, y = (la.get(key), lb.get(key)) if key == 'fatigue' else (fa.get(key), fb.get(key))
            lines.append(f'{names[key]}: {x} → {y}' + (f'  (fark: {y-x:+.2f})' if x is not None and y is not None else ''))
        self.history_info.setText('\n'.join(lines))

    def alert(self, text):
        QMessageBox.information(self, 'PPG Laboratuvarı', text)

    def closeEvent(self, event):
        if self.recording or self.pending:
            answer = QMessageBox.question(self, 'Kaydedilmemiş ölçüm', 'Kaydedilmemiş ölçüm var. Kaydı silerek kapatılsın mı?')
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore(); return
        self.disconnect()
        self.store.db.close()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    window = Window()
    window.show()
    sys.exit(app.exec())



if __name__ == '__main__':
    main()
