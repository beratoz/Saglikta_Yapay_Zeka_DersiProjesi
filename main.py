"""Run with: python main.py"""
import sys
import time
import uuid
from collections import deque
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from serial.tools import list_ports
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QFontDatabase
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QLabel, QPushButton, QComboBox,
    QDoubleSpinBox, QSpinBox, QPlainTextEdit, QVBoxLayout, QHBoxLayout,
    QTabWidget, QStackedWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QMessageBox, QDialog, QDialogButtonBox, QProgressBar, QLineEdit,
)
from ppg import CsvStore, Acquisition, LABEL_KEYS, analyze, signal_data, now

ROOT = Path(__file__).resolve().parent
DAY_PERIODS = [('Sabah', 'morning'), ('Öğle', 'noon'), ('Akşam', 'evening')]
STYLE = '''
QWidget { color:#e0e6ea; font-family:'Segoe UI'; font-size:12px; }
QMainWindow, QWidget#workspace { background:#101519; }
QLabel { background:transparent; }
QLabel#brand { font-size:16px; font-weight:600; color:#eff4f6; padding-right:18px; }
QLabel#muted { color:#9eafb9; font-size:11px; }
QLabel#heading { font-size:13px; font-weight:600; color:#edf2f4; }
QLabel#value { font-family:'Consolas'; font-size:26px; font-weight:600; }
QLabel#status { color:#9bd3c5; }
QLabel#error { color:#f0a396; }
QFrame#panel { background:#181f25; border:1px solid #303b44; border-radius:4px; }
QFrame#metric { background:#181f25; border:1px solid #303b44; border-radius:4px; }
QPushButton { background:#222d35; border:1px solid #41505c; padding:6px 10px; border-radius:3px; }
QPushButton:hover { background:#2c3943; border-color:#738894; }
QPushButton:pressed, QPushButton:checked { background:#354c52; }
QPushButton#primary { background:#2b6963; border-color:#5a9e91; color:#f1faf7; }
QPushButton#danger { color:#edb8ae; }
QPushButton:disabled { color:#74818c; background:#1b242b; border-color:#303b44; }
QPushButton:focus, QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus, QLineEdit:focus, QPlainTextEdit:focus { border:2px solid #83c9ba; }
QComboBox, QDoubleSpinBox, QSpinBox, QLineEdit, QPlainTextEdit { background:#11191f; border:1px solid #44515b; padding:5px 7px; border-radius:3px; selection-background-color:#376a67; }
QComboBox { padding-right:22px; }
QComboBox::drop-down { border:0; width:20px; }
QComboBox QAbstractItemView { background:#1c2730; color:#e0e6ea; selection-background-color:#315854; }
QTabWidget::pane { border:0; }
QTabBar::tab { background:transparent; color:#a0b0bb; padding:10px 18px; border-bottom:2px solid transparent; }
QTabBar::tab:selected { color:#d8eee8; border-bottom:2px solid #7bc4b2; }
QTabBar::tab:focus { border:1px solid #83c9ba; }
QTableWidget { background:#151d23; alternate-background-color:#1b252c; selection-background-color:#2a454b; selection-color:#edf6f4; border:1px solid #303b44; gridline-color:#303b44; }
QTableWidget::item { padding:8px; }
QHeaderView::section { background:#222d35; color:#b7c7cf; font-size:11px; padding:9px; border:0; border-bottom:1px solid #44515b; }
QProgressBar { background:#26353c; border:0; min-height:4px; max-height:4px; }
QProgressBar::chunk { background:#7bc4b2; }
QDialog { background:#181f25; }
QToolTip { background:#283a43; color:#edf6f4; border:1px solid #607681; padding:7px; }
'''


def label(text, role=None, wrap=False):
    widget = QLabel(text)
    if role:
        widget.setObjectName(role)
    widget.setWordWrap(wrap)
    return widget


def button(text, callback, role=None):
    widget = QPushButton(text)
    widget.clicked.connect(callback)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    if role:
        widget.setObjectName(role)
    return widget


def frame():
    widget = QFrame()
    widget.setObjectName('panel')
    return widget


def display(value, suffix=''):
    return f'{value}{suffix}' if value is not None and value != '' else '—'


def measurement_name(row, full=False):
    period = dict((value, text) for text, value in DAY_PERIODS).get(row.get('day_period'), '')
    phase = ('Görev öncesi' if row['phase'] == 'before' else 'Görev sonrası') if full else ('Önce' if row['phase'] == 'before' else 'Sonra')
    return f'{period} · {phase}' if period else phase


class StudyDuration(QWidget):
    """Keep CSV compatibility: two visible controls, one total-minute value."""
    def __init__(self, minutes=None):
        super().__init__()
        box = QHBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(4)
        self.hours, self.minutes = QSpinBox(), QSpinBox()
        for widget, limit, suffix, name in [(self.hours, 24, ' sa', 'Çalışma süresi saat'),
                                             (self.minutes, 59, ' dk', 'Çalışma süresi dakika')]:
            widget.setRange(-1, limit)
            widget.setSpecialValueText('—')
            widget.setSuffix(suffix)
            widget.setAccessibleName(name)
            widget.setValue(-1)
            box.addWidget(widget, 1)
        if minutes not in (None, ''):
            total = max(0, min(1440, int(float(minutes))))
            self.hours.setValue(total // 60)
            self.minutes.setValue(total % 60)
        self.hours.valueChanged.connect(lambda _: self.sync(self.hours))
        self.minutes.valueChanged.connect(lambda _: self.sync(self.minutes))
        self.sync(None)

    def sync(self, changed):
        self.hours.blockSignals(True)
        self.minutes.blockSignals(True)
        if changed is not None:
            other = self.minutes if changed is self.hours else self.hours
            if changed.value() < 0:
                other.setValue(-1)
            elif other.value() < 0:
                other.setValue(0)
        self.minutes.setMaximum(0 if self.hours.value() == 24 else 59)
        self.hours.blockSignals(False)
        self.minutes.blockSignals(False)

    def value(self):
        return None if self.hours.value() < 0 else self.hours.value() * 60 + self.minutes.value()


class Labels(QWidget):
    def __init__(self, values=None, parent=None):
        super().__init__(parent)
        self.original = dict(values or {})
        self.task_id = self.original.get('task_id') or uuid.uuid4().hex[:10]
        self.fields = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        phase_row = QHBoxLayout()
        phase_row.addWidget(label('Ölçüm', 'muted'))
        period = QComboBox()
        period.addItem('Seçin', None)
        for text, value in DAY_PERIODS:
            period.addItem(text, value)
        period.setCurrentIndex(max(0, period.findData(self.original.get('day_period'))))
        period.setAccessibleName('Günün bölümü')
        period.setToolTip('Sabah, öğle veya akşam ölçümü')
        self.fields['day_period'] = period
        phase_row.addWidget(period, 1)
        phase = QComboBox()
        phase.addItem('Görev öncesi', 'before')
        phase.addItem('Görev sonrası', 'after')
        phase.setCurrentIndex(max(0, phase.findData(self.original.get('phase', 'before'))))
        phase.setAccessibleName('Ölçüm aşaması')
        self.fields['phase'] = phase
        phase_row.addWidget(phase, 2)
        root.addLayout(phase_row)

        def rating(key, title, hint=None):
            widget = QComboBox()
            widget.addItem('Belirtilmedi', None)
            for i in range(1, 11):
                widget.addItem(str(i), i)
            value = self.original.get(key)
            widget.setCurrentIndex(widget.findData(int(float(value))) if value not in (None, '') else 0)
            widget.setAccessibleName(title)
            widget.setToolTip(hint or '1 çok düşük · 10 çok yüksek')
            self.fields[key] = widget
            return widget

        def number(key, title, maximum, suffix, decimals=0):
            widget = QDoubleSpinBox()
            widget.setRange(-1, maximum)
            widget.setDecimals(decimals)
            widget.setSpecialValueText('Belirtilmedi')
            widget.setSuffix(suffix)
            value = self.original.get(key)
            widget.setValue(float(value) if value not in (None, '') else -1)
            widget.setAccessibleName(title)
            self.fields[key] = widget
            return widget

        def pair(first, second):
            row = QHBoxLayout()
            row.setSpacing(10)
            for caption, widget in (first, second):
                box = QVBoxLayout()
                box.setSpacing(3)
                caption_label = label(caption, 'muted')
                caption_label.setBuddy(widget)
                box.addWidget(caption_label)
                box.addWidget(widget)
                row.addLayout(box, 1)
            root.addLayout(row)

        pair(('Zihinsel yorgunluk · önce /10', rating('fatigue_before', 'Görev öncesi zihinsel yorgunluk')),
             ('Zihinsel yorgunluk · sonra /10', rating('fatigue_after', 'Görev sonrası zihinsel yorgunluk')))
        self.error = label('', 'error', True)
        self.error.hide()
        root.addWidget(self.error)
        pair(('Son gece uyku', number('sleep_hours', 'Son gece uyku süresi', 24, ' saat', 1)),
             ('Uykululuk /10', rating('sleepiness', 'Uykululuk')))
        pair(('Algılanan stres /10', rating('stress', 'Algılanan stres')),
             ('Fiziksel yorgunluk /10', rating('physical_fatigue', 'Fiziksel yorgunluk')))
        study_duration = StudyDuration(self.original.get('study_minutes'))
        self.fields['study_minutes'] = study_duration
        pair(('Görev zorluğu /10', rating('task_difficulty', 'Görev zorluğu')),
             ('Çalışma süresi', study_duration))
        def yes_no(key, title):
            widget = QComboBox()
            for text, value in [('Belirtilmedi', None), ('Hayır', False), ('Evet', True)]:
                widget.addItem(text, value)
            value = self.original.get(key)
            if isinstance(value, str):
                value = {'True': True, 'False': False}.get(value)
            widget.setCurrentIndex(max(0, widget.findData(value)))
            widget.setAccessibleName(title)
            self.fields[key] = widget
            return widget
        pair(('Son 4 saatte kafein', yes_no('caffeine', 'Son 4 saatte kafein')),
             ('Son 2 saatte egzersiz', yes_no('exercise', 'Son 2 saatte egzersiz')))
        note_label = label('Ölçümü etkileyen durum / kısa not', 'muted')
        notes = QPlainTextEdit(str(self.original.get('note') or ''))
        notes.setFixedHeight(48)
        notes.setAccessibleName('Ölçüm notu')
        note_label.setBuddy(notes)
        root.addWidget(note_label)
        root.addWidget(notes)
        self.fields['note'] = notes
        root.addWidget(label('Puanlar öz değerlendirmedir. Bilinmeyen alanları boş bırakın.', 'muted', True))
        root.addStretch()
        for key in ('phase', 'fatigue_before', 'fatigue_after'):
            self.fields[key].currentIndexChanged.connect(lambda *_: self.error.hide())

    def values(self):
        result = {key: self.original.get(key) for key in LABEL_KEYS}
        for key, widget in self.fields.items():
            if isinstance(widget, QComboBox):
                result[key] = widget.currentData()
            elif isinstance(widget, StudyDuration):
                result[key] = widget.value()
            elif isinstance(widget, QDoubleSpinBox):
                result[key] = widget.value() if widget.value() >= 0 else None
            else:
                result[key] = widget.toPlainText().strip()
        selected = 'fatigue_before' if result['phase'] == 'before' else 'fatigue_after'
        if result[selected] is None:
            self.error.setText('Seçilen aşamaya ait zihinsel yorgunluk puanını girin.')
            self.error.show()
            self.fields[selected].setFocus()
            raise ValueError(self.error.text())
        result['mental_fatigue'] = result[selected]
        result['task_id'] = self.task_id
        result['label_time'] = result.get('label_time') or now()
        result['labels_updated_at'] = now()
        return result


class Traces(QFrame):
    def __init__(self, live=True):
        super().__init__()
        self.setObjectName('panel')
        self.live = live
        self.samples = []
        self.result = None
        self.frozen = False
        self.last_drawn = None
        box = QVBoxLayout(self)
        box.setContentsMargins(10, 8, 10, 8)
        box.setSpacing(4)
        tools = QHBoxLayout()
        tools.addWidget(label('PPG SİNYALİ', 'heading'), 1)
        self.mode = QComboBox()
        self.mode.addItems(['Nabız bileşeni · 0,5–4 Hz', 'Ham RED / IR'])
        self.mode.setAccessibleName('Grafik görünümü')
        tools.addWidget(self.mode)
        self.window = QComboBox()
        for text, seconds in [('10 sn', 10), ('15 sn', 15), ('30 sn', 30), ('60 sn', 60)]:
            self.window.addItem(text, seconds)
        if not live:
            self.window.addItem('Tüm kayıt', None)
        self.window.setCurrentIndex(1 if live else 4)
        self.window.setAccessibleName('Grafik zaman penceresi')
        tools.addWidget(self.window)
        self.pause = button('Dondur', self.toggle_pause)
        self.pause.setCheckable(True)
        self.pause.setVisible(live)
        tools.addWidget(self.pause)
        tools.addWidget(button('Sığdır', self.fit))
        box.addLayout(tools)
        self.ir_plot = self.plot('IR', '#89d2c3')
        self.red_plot = self.plot('RED', '#dfb980')
        self.red_plot.setXLink(self.ir_plot)
        self.ir_curve = self.ir_plot.plot(pen=pg.mkPen('#89d2c3', width=1.6))
        self.red_curve = self.red_plot.plot(pen=pg.mkPen('#dfb980', width=1.5))
        box.addWidget(self.ir_plot, 2)
        box.addWidget(self.red_plot, 2)
        self.ibi_plot = self.plot('IBI', '#b2bdcf', 'ms')
        self.ibi_plot.setToolTip('Kabul edilmiş atımlar arası süreler · PPG tabanlı PRV')
        self.ibi_curve = self.ibi_plot.plot(pen=None, symbol='o', symbolSize=4,
                                          symbolBrush='#b2bdcf', symbolPen=None)
        box.addWidget(self.ibi_plot, 1)
        self.caption = label('Sinyal bekleniyor · Grafik filtresi ham CSV verisini değiştirmez.', 'muted')
        box.addWidget(self.caption)
        self.mode.currentIndexChanged.connect(self.redraw)
        self.window.currentIndexChanged.connect(self.redraw)

    @staticmethod
    def plot(title, color, units='ADC'):
        widget = pg.PlotWidget(background='#11191f')
        widget.setMinimumHeight(65)
        widget.setMenuEnabled(False)
        widget.setMouseEnabled(x=True, y=True)
        widget.showGrid(x=True, y=True, alpha=.10)
        for name in ('left', 'bottom'):
            axis = widget.getAxis(name)
            axis.setPen('#394953')
            axis.setTextPen('#9eb0bb')
            axis.enableAutoSIPrefix(False)
        widget.setLabel('left', title, units=units, color=color)
        widget.setLabel('bottom', 'Zaman', units='s', color='#9eb0bb')
        widget.getAxis('left').setWidth(65)
        return widget

    def sizeHint(self):
        from PySide6.QtCore import QSize
        return QSize(600, 380)

    def set_samples(self, samples, result=None):
        self.samples = list(samples)
        if result is not None:
            self.set_result(result, samples)
        if not self.frozen:
            self.redraw()

    def set_result(self, result, samples):
        self.result = result
        peaks = result[1]
        self.ibi_times = np.asarray([s[1] for s in samples], dtype=float)[peaks[1:]] / 1000 if len(peaks) > 1 else np.array([])

    def redraw(self, *_):
        if self.frozen:
            return
        samples = self.samples
        seconds = self.window.currentData()
        if samples and seconds is not None:
            cutoff = samples[-1][1] - seconds * 1000
            samples = [s for s in samples if s[1] >= cutoff]
        view = signal_data(samples)
        self.last_drawn = view
        if view is None:
            self.ir_curve.clear()
            self.red_curve.clear()
            self.ibi_curve.clear()
            self.caption.setText('Sinyal bekleniyor · Ham veri değiştirilmez.')
            return
        t = view['t'].copy()
        if self.live:
            t -= t[-1]
        else:
            t += (samples[0][1] - self.samples[0][1]) / 1000
        values = view['filtered'].copy() if self.mode.currentIndex() == 0 else view['raw'].copy()
        for offset, index in enumerate(view['breaks']):
            t = np.insert(t, index + offset, np.nan)
            values = np.insert(values, index + offset, np.nan, axis=0)
        self.ir_curve.setData(t, values[:, 1], connect='finite')
        self.red_curve.setData(t, values[:, 0], connect='finite')
        self.caption.setText('IR ve RED ayrı ölçeklerde · Ham veri korunur' +
                             (f" · {len(view['breaks'])} kesinti" if len(view['breaks']) else ''))
        if self.result:
            features, peaks, ibis, accepted = self.result
            if len(peaks) > 1 and len(accepted) == len(ibis):
                if self.samples:
                    origin = self.samples[-1][1] / 1000 if self.live else self.samples[0][1] / 1000
                    self.ibi_curve.setData((self.ibi_times - origin)[accepted], ibis[accepted])
            else:
                self.ibi_curve.clear()

    def toggle_pause(self):
        self.frozen = self.pause.isChecked()
        self.pause.setText('Sürdür' if self.frozen else 'Dondur')
        self.mode.setEnabled(not self.frozen)
        self.window.setEnabled(not self.frozen)
        if self.frozen:
            self.caption.setText('Görüntü donduruldu · Veri alımı ve kayıt sürüyor.')
        else:
            self.redraw()

    def fit(self):
        for plot in (self.ir_plot, self.red_plot, self.ibi_plot):
            plot.enableAutoRange()


class Window(QMainWindow):
    def __init__(self, root=None):
        super().__init__()
        for font_name in ('segoeui.ttf', 'consola.ttf'):
            path = Path('C:/Windows/Fonts') / font_name
            if path.exists():
                QFontDatabase.addApplicationFont(str(path))
        QApplication.instance().setFont(QFont('Segoe UI', 9))
        self.store = CsvStore(root or ROOT / 'kayitlar')
        self.worker = None
        self.buffer = deque(maxlen=6500)
        self.pending = None
        self.recording = False
        self.last_seen = 0
        self.sequence = None
        self.last_ms = None
        self.gaps = 0
        self.total = 0
        self.counter = 0
        self.active_record = None
        self.latest_analysis = None
        self.setWindowTitle('PPG · Ölçüm ve etiketleme')
        self.setMinimumSize(1100, 700)
        self.resize(1366, 768)
        self.setStyleSheet(STYLE)
        self.build()
        self.refresh_ports()
        self.refresh_records()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(200)

    def build(self):
        root = QWidget()
        root.setObjectName('workspace')
        box = QVBoxLayout(root)
        box.setContentsMargins(12, 6, 12, 8)
        box.setSpacing(7)
        header = QHBoxLayout()
        header.addWidget(label('PPG / Ölçüm', 'brand'))
        self.connection_state = label('Bağlantı kapalı', 'status')
        header.addWidget(self.connection_state)
        header.addStretch()
        header.addWidget(button('CSV klasörü', self.open_folder))
        box.addLayout(header)
        self.tabs = QTabWidget()
        self.live_page()
        self.archive_page()
        box.addWidget(self.tabs, 1)
        self.status = label('ESP32 USB bağlantısını seçin · Kayıtlar yalnızca bu bilgisayarda saklanır.', 'muted')
        box.addWidget(self.status)
        self.setCentralWidget(root)

    def live_page(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 5, 0, 0)
        outer.setSpacing(7)
        connection = QHBoxLayout()
        connection.addWidget(label('ESP32 USB', 'muted'))
        self.port = QComboBox()
        self.port.setMinimumWidth(160)
        self.port.setMaximumWidth(350)
        self.port.setAccessibleName('COM portu')
        connection.addWidget(self.port, 1)
        self.baud = QComboBox()
        self.baud.addItems(['115200', '230400', '460800', '921600'])
        self.baud.setAccessibleName('Baud hızı')
        connection.addWidget(self.baud)
        self.refresh_btn = button('Yenile', self.refresh_ports)
        self.connect_btn = button('Bağlan', self.toggle_connection)
        self.demo_btn = button('Demo', self.start_demo)
        for control in (self.refresh_btn, self.connect_btn, self.demo_btn):
            connection.addWidget(control)
        self.flow = label('Veri bekleniyor', 'muted')
        connection.addWidget(self.flow, 1, Qt.AlignmentFlag.AlignRight)
        outer.addLayout(connection)
        body = QHBoxLayout()
        body.setSpacing(10)
        monitor = QVBoxLayout()
        monitor.setSpacing(5)
        values = QHBoxLayout()
        values.setSpacing(8)
        self.metric_values = {}
        for key, title in [('bpm', 'Nabız · BPM'), ('quality', 'Sinyal kontrolü · %'),
                           ('prv_rmssd_ms', 'PRV · RMSSD / ms'), ('prv_sdnn_ms', 'PRV · SDNN / ms')]:
            card = QFrame()
            card.setObjectName('metric')
            card_box = QVBoxLayout(card)
            card_box.setContentsMargins(10, 6, 10, 6)
            card_box.setSpacing(1)
            card_box.addWidget(label(title, 'muted'))
            value = label('—', 'value')
            value.setAccessibleName(title)
            card_box.addWidget(value)
            self.metric_values[key] = value
            values.addWidget(card, 1)
        monitor.addLayout(values)
        self.signal_status = label('Nabız için 15 sn, PRV için en az 60 sn kabul edilmiş sinyal gerekir.', 'muted')
        self.signal_status.setWordWrap(True)
        monitor.addWidget(self.signal_status)
        self.live_traces = Traces()
        monitor.addWidget(self.live_traces, 1)
        body.addLayout(monitor, 1)
        self.label_panel = frame()
        self.label_panel.setFixedWidth(365)
        form = QVBoxLayout(self.label_panel)
        form.setContentsMargins(10, 10, 10, 10)
        form.setSpacing(7)
        heading = QHBoxLayout()
        heading.addWidget(label('Ölçüm etiketleri', 'heading'), 1)
        self.new_task_btn = button('Yeni çalışma', self.new_task)
        heading.addWidget(self.new_task_btn)
        form.addLayout(heading)
        self.labels = Labels()
        form.addWidget(self.labels, 1)
        duration_row = QHBoxLayout()
        duration_row.addWidget(label('Kayıt süresi', 'muted'))
        self.duration = QComboBox()
        for text, seconds in [('30 sn · deneme', 30), ('1 dakika', 60), ('2 dakika', 120), ('5 dakika', 300), ('10 dakika', 600)]:
            self.duration.addItem(text, seconds)
        self.duration.setCurrentIndex(2)
        self.duration.setAccessibleName('Kayıt süresi')
        duration_row.addWidget(self.duration, 1)
        form.addLayout(duration_row)
        body.addWidget(self.label_panel)
        outer.addLayout(body, 1)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setValue(0)
        outer.addWidget(self.progress)
        controls = QHBoxLayout()
        self.record_status = label('Kayda hazır', 'muted')
        controls.addWidget(self.record_status, 1)
        self.record_btn = button('Kaydı başlat', self.start_record, 'primary')
        self.stop_btn = button('Durdur', self.stop_record)
        self.save_btn = button('CSV kaydet', self.save_record)
        self.discard_btn = button('Vazgeç', self.discard_record, 'danger')
        for control in (self.record_btn, self.stop_btn, self.save_btn, self.discard_btn):
            controls.addWidget(control)
        self.stop_btn.setEnabled(False)
        self.save_btn.setEnabled(False)
        self.discard_btn.setEnabled(False)
        outer.addLayout(controls)
        self.tabs.addTab(page, 'Canlı izleme')

    def archive_page(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 5, 0, 0)
        self.archive = QStackedWidget()
        listing = QWidget()
        listing_box = QVBoxLayout(listing)
        listing_box.setContentsMargins(0, 0, 0, 0)
        tools = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('Tarih, aşama veya not ara…')
        self.search.setAccessibleName('CSV kayıtlarını ara')
        self.search.textChanged.connect(self.filter_records)
        tools.addWidget(self.search, 1)
        tools.addWidget(button('Yenile', self.refresh_records))
        self.open_btn = button('Sinyali incele', self.open_selected)
        tools.addWidget(self.open_btn)
        listing_box.addLayout(tools)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(['TARİH / SAAT', 'AŞAMA', 'ÖNCE /10', 'SONRA /10', 'SÜRE · sn', 'KAYNAK', 'KALİTE · %', 'CSV DOSYASI'])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self.update_selection)
        self.table.itemDoubleClicked.connect(lambda *_: self.open_selected())
        listing_box.addWidget(self.table, 1)
        self.archive_summary = label('Henüz CSV kaydı yok.', 'muted')
        listing_box.addWidget(self.archive_summary)
        self.archive.addWidget(listing)
        detail = QWidget()
        detail_box = QVBoxLayout(detail)
        detail_box.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        bar.addWidget(button('Kayıtlara dön', lambda: self.archive.setCurrentIndex(0)))
        self.detail_title = label('Kayıt inceleme', 'heading')
        bar.addWidget(self.detail_title, 1)
        bar.addWidget(button('Etiketleri düzenle', self.edit_record))
        detail_box.addLayout(bar)
        self.detail_info = label('', 'muted', True)
        detail_box.addWidget(self.detail_info)
        self.history_traces = Traces(live=False)
        detail_box.addWidget(self.history_traces, 1)
        self.archive.addWidget(detail)
        outer.addWidget(self.archive, 1)
        self.tabs.addTab(page, 'CSV kayıtları')

    def refresh_ports(self):
        previous = self.port.currentData()
        self.port.clear()
        ports = sorted(list_ports.comports(), key=lambda p: p.vid is None)
        for port in ports:
            self.port.addItem(f'{port.device} · {port.description}', port.device)
        if not ports:
            self.port.addItem('USB / COM portu bulunamadı', None)
        if previous and self.port.findData(previous) >= 0:
            self.port.setCurrentIndex(self.port.findData(previous))

    def toggle_connection(self):
        if self.worker:
            self.disconnect()
        elif self.port.currentData():
            self.start_source(self.port.currentData())
        else:
            self.status.setText('COM portu bulunamadı. USB kablosunu kontrol edip Yenile deyin.')

    def start_demo(self):
        if not self.worker:
            self.start_source(None)

    def start_source(self, port):
        self.buffer.clear()
        self.last_seen = 0
        self.sequence = self.last_ms = None
        self.gaps = self.total = 0
        self.latest_analysis = None
        self.live_traces.pause.setChecked(False)
        self.live_traces.toggle_pause()
        self.live_traces.result = None
        self.live_traces.set_samples([])
        self.clear_metrics('Sinyal bekleniyor')
        self.source = port or 'DEMO'
        self.worker = Acquisition(port, int(self.baud.currentText()))
        self.worker.samples.connect(self.receive)
        self.worker.message.connect(self.status.setText)
        self.worker.finished.connect(self.source_finished)
        self.worker.start()
        self.connect_btn.setText('Bağlantıyı kes')
        for control in (self.port, self.baud, self.refresh_btn, self.demo_btn):
            control.setEnabled(False)
        self.connection_state.setText('DEMO · SENTETİK' if port is None else f'{port} · Bağlanıyor')

    def disconnect(self):
        if self.recording:
            self.stop_record()
        if self.worker:
            self.worker.requestInterruption()
            self.worker.wait(1500)

    def source_finished(self):
        if self.sender() is not self.worker:
            return
        if self.recording:
            self.stop_record()
        self.worker = None
        self.last_seen = 0
        self.connect_btn.setText('Bağlan')
        for control in (self.port, self.baud, self.refresh_btn, self.demo_btn):
            control.setEnabled(True)
        self.connection_state.setText('Bağlantı kapalı')
        self.clear_metrics('Akış kapalı · Son sinyal grafikte korunuyor')

    def receive(self, samples):
        if not samples:
            return
        for sample in samples:
            seq, ms, _, _ = sample
            reset = self.sequence is not None and (seq <= self.sequence or ms <= self.last_ms)
            if reset:
                if self.recording:
                    self.stop_record()
                self.buffer.clear()
                self.latest_analysis = None
                self.live_traces.result = None
                self.live_traces.ibi_curve.clear()
                self.gaps += 1
                self.clear_metrics('Cihaz saati/sırası sıfırlandı · Yeni akış')
            elif self.sequence is not None and (seq != self.sequence + 1 or ms - self.last_ms > 50):
                self.gaps += 1
                self.clear_metrics('Veri kesintisi algılandı')
            self.buffer.append(sample)
            self.sequence, self.last_ms = seq, ms
            self.total += 1
            if self.recording:
                if self.record_start_ms is None:
                    self.record_start_ms = ms
                if ms - self.record_start_ms < self.target * 1000:
                    self.record_samples.append(sample)
                else:
                    self.progress.setValue(100)
                    self.stop_record()
        self.last_seen = time.monotonic()

    def clear_metrics(self, text):
        for value in self.metric_values.values():
            value.setText('—')
        self.signal_status.setText(text)

    def tick(self):
        self.counter += 1
        age = time.monotonic() - self.last_seen if self.last_seen else None
        if self.worker:
            source = 'DEMO · SENTETİK' if self.source == 'DEMO' else self.source
            self.connection_state.setText(source + (' · Veri alınıyor' if age is not None and age < 2 else ' · Veri bekleniyor'))
        self.flow.setText(f"{self.total:,} örnek · {self.gaps} kesinti · Son veri {age:.1f} sn" if age is not None else 'Veri bekleniyor')
        if age is None or age > 2:
            self.clear_metrics('Güncel veri yok · Sinyal ve USB bağlantısını kontrol edin')
        elif self.counter % 10 == 0 and self.buffer:
            snapshot = list(self.buffer)
            live = analyze(snapshot[-2000:])
            full = analyze(snapshot)
            features = dict(live[0])
            features['prv_rmssd_ms'] = full[0]['prv_rmssd_ms']
            features['prv_sdnn_ms'] = full[0]['prv_sdnn_ms']
            for key, widget in self.metric_values.items():
                widget.setText(display(features[key]))
            status = features['analysis_status'].split(' · PRV için')[0]
            if full[0]['prv_rmssd_ms'] is not None:
                status += f" · PRV: son {full[0]['duration_s']:.0f} sn"
            elif features['bpm'] is not None:
                status += ' · PRV için 60 sn kesintisiz, kabul edilmiş veri bekleniyor'
            self.signal_status.setText(status)
            self.latest_analysis = full
            self.live_traces.set_result(full, snapshot)
        if self.tabs.currentIndex() == 0:
            self.live_traces.set_samples(list(self.buffer))
        if self.recording:
            elapsed = (self.record_samples[-1][1] - self.record_start_ms) / 1000 if self.record_samples else 0
            self.progress.setValue(min(100, int(100 * elapsed / self.target)))
            self.record_status.setText(f'Kayıt · {elapsed:.1f} / {self.target} sn · {len(self.record_samples):,} örnek')
            if age is None or age > 3:
                self.stop_record()
                self.status.setText('Veri akışı kesildi. Kısmi ölçümü kaydedebilir veya vazgeçebilirsiniz.')

    def start_record(self):
        if self.pending:
            self.status.setText('Önce bekleyen kaydı kaydedin veya Vazgeç deyin.')
            return
        if not self.worker or not self.last_seen or time.monotonic() - self.last_seen > 2:
            self.status.setText('Kayda başlamak için önce canlı sensör veya demo verisi alın.')
            return
        try:
            self.start_labels = self.labels.values()
        except ValueError as e:
            self.status.setText(str(e))
            return
        self.created = now()
        self.record_source = self.source
        self.record_baud = int(self.baud.currentText())
        self.target = self.duration.currentData()
        self.record_start_ms = None
        self.record_samples = []
        self.recording = True
        self.label_panel.setEnabled(False)
        self.record_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress.setValue(0)
        self.status.setText('Ölçüm kaydediliyor · Parmağınızı sabit tutun.')

    def stop_record(self):
        if not self.recording:
            return
        self.recording = False
        self.pending = list(self.record_samples) if self.record_samples else None
        self.label_panel.setEnabled(True)
        self.new_task_btn.setEnabled(not bool(self.pending))
        self.stop_btn.setEnabled(False)
        self.save_btn.setEnabled(bool(self.pending))
        self.discard_btn.setEnabled(bool(self.pending))
        self.record_btn.setEnabled(not bool(self.pending))
        self.record_status.setText(f'Kaydetmeyi bekliyor · {len(self.record_samples):,} örnek')
        self.status.setText('Ölçüm durdu. Etiketleri kontrol edip CSV kaydet deyin.')

    def save_record(self):
        if not self.pending:
            return
        try:
            labels = self.labels.values()
            labels['label_time'] = self.start_labels['label_time']
            path = self.store.save(self.pending, labels, self.record_source, self.target, self.created, self.record_baud)
            self.clear_pending()
            self.refresh_records()
            self.status.setText(f'CSV kaydedildi · {path.name}')
        except ValueError as e:
            self.status.setText(str(e))
        except Exception as e:
            self.alert(f'CSV kaydedilemedi. Ölçüm bellekte korunuyor.\n{e}')

    def clear_pending(self):
        self.pending = None
        self.save_btn.setEnabled(False)
        self.discard_btn.setEnabled(False)
        self.record_btn.setEnabled(True)
        self.new_task_btn.setEnabled(True)
        self.progress.setValue(0)
        self.record_status.setText('Kayda hazır')

    def discard_record(self):
        if self.pending and QMessageBox.question(self, 'Kaydedilmemiş ölçüm', 'Bu ölçüm kaydedilmeden silinsin mi?') != QMessageBox.StandardButton.Yes:
            return
        self.clear_pending()

    def new_task(self):
        if self.recording or self.pending:
            return
        self.labels.task_id = uuid.uuid4().hex[:10]
        self.labels.original = {}
        for key in ('fatigue_before', 'fatigue_after'):
            self.labels.fields[key].setCurrentIndex(0)
        self.labels.fields['phase'].setCurrentIndex(0)
        self.status.setText('Yeni çalışma açıldı · Önce ve sonra kayıtları aynı çalışmaya bağlanacak.')

    def refresh_records(self):
        self.records, errors = self.store.list_records()
        self.active_record = None
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.records))
        for i, row in enumerate(self.records):
            values = [row['created_at'].replace('T', ' ')[:19], measurement_name(row),
                      row.get('fatigue_before'), row.get('fatigue_after'), row.get('duration_s'),
                      row['source'], row.get('quality'), Path(row['path']).name]
            for col, value in enumerate(values):
                item = QTableWidgetItem(display(value))
                item.setData(Qt.ItemDataRole.UserRole, i)
                item.setToolTip(row.get('note', ''))
                self.table.setItem(i, col, item)
            self.table.setRowHeight(i, 38)
        self.table.blockSignals(False)
        self.open_btn.setEnabled(False)
        self.filter_records()
        self.archive_summary.setText(f'{len(self.records)} CSV kaydı · {self.store.root}' + (f' · {len(errors)} dosya okunamadı' if errors else ''))

    def filter_records(self, *_):
        text = self.search.text().casefold().strip()
        for i, row in enumerate(getattr(self, 'records', [])):
            haystack = f"{row['created_at']} {row.get('note', '')} {measurement_name(row)}".casefold()
            self.table.setRowHidden(i, bool(text and text not in haystack))
        self.update_selection()

    def update_selection(self):
        row = self.table.currentRow()
        valid = 0 <= row < len(getattr(self, 'records', [])) and not self.table.isRowHidden(row)
        self.active_record = self.records[row] if valid else None
        self.open_btn.setEnabled(valid)

    def open_selected(self):
        if not self.active_record:
            return
        try:
            samples, row = self.store.read(self.active_record['path'])
            result = analyze(samples)
            self.history_traces.result = None
            self.history_traces.set_samples(samples, result)
            self.history_traces.fit()
            self.detail_title.setText(Path(self.active_record['path']).name)
            self.detail_info.setText(
                f"{row['created_at']} · {measurement_name(row, full=True)} · {row['source']} · {row['duration_s']} sn\n"
                f"Yorgunluk önce {display(row.get('fatigue_before'))}/10 · sonra {display(row.get('fatigue_after'))}/10 · "
                f"Nabız {display(row.get('bpm'))} BPM · RMSSD {display(row.get('prv_rmssd_ms'))} ms · SDNN {display(row.get('prv_sdnn_ms'))} ms\n"
                f"{row['sample_count']} örnek · Eksik örnek {row['missing_samples']} · Kalite {display(row.get('quality'))}% · {row['analysis_status']}")
            self.archive.setCurrentIndex(1)
        except Exception as e:
            self.alert(f'Kayıt açılamadı: {e}')

    def edit_record(self):
        if not self.active_record:
            return
        path = self.active_record['path']
        dialog = QDialog(self)
        dialog.setWindowTitle('CSV etiketlerini düzenle')
        dialog.resize(420, 580)
        box = QVBoxLayout(dialog)
        form = Labels(self.active_record)
        box.addWidget(form, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('Kaydet')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('Vazgeç')
        box.addWidget(buttons)
        def save():
            try:
                self.store.update_labels(path, form.values())
                dialog.accept()
            except ValueError:
                pass
            except Exception as e:
                self.alert(f'CSV güncellenemedi: {e}')
        buttons.accepted.connect(save)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec():
            self.refresh_records()
            index = next(i for i, record in enumerate(self.records) if record['path'] == path)
            self.table.selectRow(index)
            self.update_selection()
            self.open_selected()
            self.status.setText('Etiketler CSV içinde güncellendi · Ham sinyal korundu.')

    def open_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.root.resolve())))

    def alert(self, text):
        QMessageBox.information(self, 'PPG', text)

    def closeEvent(self, event):
        if self.recording or self.pending:
            answer = QMessageBox.question(self, 'Kaydedilmemiş ölçüm', 'Kaydedilmemiş ölçüm var. Kaydetmeden kapatılsın mı?')
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.disconnect()
        if self.worker and self.worker.isRunning():
            event.ignore()
            self.status.setText('Bağlantı kapanıyor; pencereyi tekrar kapatın.')
            return
        self.timer.stop()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setFont(QFont('Segoe UI', 9))
    pg.setConfigOptions(antialias=True)
    window = Window()
    window.showMaximized()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
