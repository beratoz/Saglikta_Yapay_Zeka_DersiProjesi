"""Headless GUI workflows; only temporary CSV files and synthetic signals."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import csv
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QScrollArea
from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from main import Window, Labels, StudyDuration
from ppg import analyze
from test_ppg import synthetic

app = QApplication([])
app.setStyle('Fusion')
root = Path(__file__).resolve().parents[1]
out = root / 'docs'
if '--screenshots' in sys.argv:
    out.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(dir=root) as d:
    w = Window(d)
    w.show()
    app.processEvents()
    assert 'participant' not in w.labels.fields
    assert w.labels.fields['caffeine'].currentData() is None
    legacy = Labels({'phase': 'before', 'fatigue_before': '3', 'study_minutes': '95'})
    assert legacy.fields['day_period'].currentData() is None
    duration = legacy.fields['study_minutes']
    assert duration.hours.value() == 1 and duration.minutes.value() == 35
    assert legacy.values()['study_minutes'] == 95
    unknown = StudyDuration()
    assert unknown.value() is None
    unknown.minutes.setValue(20)
    assert unknown.hours.value() == 0 and unknown.value() == 20
    unknown.hours.setValue(24)
    assert unknown.minutes.value() == 0 and unknown.value() == 1440
    unknown.hours.setValue(-1)
    assert unknown.value() is None and unknown.minutes.value() == -1
    try:
        w.labels.values()
        raise AssertionError('Missing fatigue label was accepted')
    except ValueError:
        assert not w.labels.error.isHidden()
    w.labels.fields['fatigue_before'].setCurrentIndex(3)
    period = w.labels.fields['day_period']
    period.setCurrentIndex(period.findData('noon'))
    w.labels.fields['study_minutes'].hours.setValue(2)
    w.labels.fields['study_minutes'].minutes.setValue(30)
    w.labels.fields['sleep_hours'].setValue(7.5)
    w.labels.fields['note'].setPlainText('Demo ölçümü · Türkçe not')
    w.start_demo()
    for _ in range(30):
        QTest.qWait(100)
        if w.buffer:
            break
    assert w.buffer and w.worker
    w.live_traces.pause.click()
    before = w.total
    QTest.qWait(250)
    assert w.total > before, 'Freezing the graph stopped acquisition'
    w.live_traces.pause.click()
    w.duration.setCurrentIndex(0)
    w.start_record()
    assert w.recording
    seq = w.sequence + 1
    ms = w.last_ms + 10
    stream = [(a + seq, b + ms, r, ir) for a, b, r, ir in synthetic(31)]
    w.receive(stream)
    assert not w.recording and w.pending
    w.save_record()
    assert not w.pending and len(w.records) == 1
    path = w.records[0]['path']
    raw_before, meta = w.store.read(path)
    assert meta['source'] == 'DEMO' and meta['mental_fatigue'] == '3'
    assert meta['day_period'] == 'noon' and meta['study_minutes'] == '150'
    w.disconnect()
    QTest.qWait(150)
    w.tabs.setCurrentIndex(1)
    w.table.selectRow(0)
    w.update_selection()
    w.open_selected()
    assert w.archive.currentIndex() == 1
    def edit():
        dialog = app.activeModalWidget()
        form = dialog.findChild(Labels)
        form.fields['fatigue_before'].setCurrentIndex(5)
        form.fields['note'].setPlainText('Düzenlenen not')
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()
    QTimer.singleShot(50, edit)
    w.edit_record()
    raw_after, meta = w.store.read(path)
    assert raw_after == raw_before and meta['fatigue_before'] == '5'
    assert meta['day_period'] == 'noon' and meta['study_minutes'] == '150'
    w.archive.setCurrentIndex(0)
    w.search.setText('no-such-note')
    assert w.table.isRowHidden(0)
    w.search.clear()
    w.table.selectRow(0)
    w.update_selection()
    w.open_selected()
    w.resize(1366, 768)
    app.processEvents()
    if '--screenshots' in sys.argv:
        w.grab().save(str(out / 'kayit-inceleme.png'))
    w.archive.setCurrentIndex(0)
    app.processEvents()
    if '--screenshots' in sys.argv:
        w.grab().save(str(out / 'kayitlar.png'))
    w.tabs.setCurrentIndex(0)
    w.timer.stop()
    samples = synthetic(65)
    w.buffer.clear()
    w.buffer.extend(samples)
    w.last_seen = __import__('time').monotonic()
    w.counter = 9
    w.tick()
    assert w.metric_values['bpm'].text() != '—'
    assert w.metric_values['prv_rmssd_ms'].text() != '—'
    w.connection_state.setText('DEMO · Son alınan sentetik sinyal')
    w.status.setText('Önizleme · Sentetik veri. Kalite sezgiseldir; PRV, EKG tabanlı HRV ile eşdeğer değildir.')
    for width, height in [(1100, 700), (1366, 768), (1600, 900)]:
        w.resize(width, height)
        app.processEvents()
        assert w.size().width() == width and w.size().height() == height
        assert not w.findChildren(QScrollArea)
        for name in ('caffeine', 'exercise', 'physical_fatigue', 'note'):
            widget = w.labels.fields[name]
            point = widget.mapTo(w, widget.rect().bottomRight())
            assert point.x() < width and point.y() < height, f'{name} does not fit'
        for mode in (0, 1):
            w.live_traces.mode.setCurrentIndex(mode)
            app.processEvents()
            assert w.live_traces.ir_plot.geometry().bottom() < w.live_traces.red_plot.geometry().top()
            assert w.live_traces.ibi_plot.geometry().bottom() < w.live_traces.caption.geometry().top()
    w.resize(1366, 768)
    w.live_traces.mode.setCurrentIndex(0)
    app.processEvents()
    if '--screenshots' in sys.argv:
        w.grab().save(str(out / 'canli.png'))
    w.live_traces.mode.setCurrentIndex(1)
    app.processEvents()
    if '--screenshots' in sys.argv:
        w.grab().save(str(out / 'ham-sinyal.png'))
    w.close()
print('GUI OK: demo, kayıt, CSV, etiket düzenleme, arşiv, grafik ve yerleşim')
