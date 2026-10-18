"""Run manually: python tests/gui_smoke.py (uses isolated temporary storage)."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialogButtonBox, QPushButton
import main as module
from test_core import synthetic

app = QApplication([])
with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as d:
    w = module.Window(d)
    w.show()
    w.start_demo()
    QTest.qWait(350)
    assert len(w.live) > 0, 'Demo did not emit samples'
    w.duration.setCurrentIndex(0)
    w.label_form.fields['fatigue'].setCurrentIndex(4)
    w.start_record()
    assert w.recording
    # Advance a known synthetic stream to exercise automatic duration stop.
    base = w.last_seq + 1
    stream = [(i + base, ms + base * 10, red, ir) for i, ms, red, ir in synthetic(31)]
    w.receive(stream)
    assert not w.recording and w.pending
    w.save_record()
    assert len(w.rows) == 1
    assert w.rows[0]['source'] == 'DEMO'
    w.disconnect()
    QTest.qWait(200)
    w.card_checks[w.rows[0]['id']].setChecked(True)
    assert len(w.selected()) == 1
    w.inspect_record(w.rows[0])
    def edit_labels():
        dialog = app.activeModalWidget()
        form = dialog.findChild(module.LabelForm)
        form.fields['fatigue'].setCurrentIndex(6)
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()
    QTimer.singleShot(100, edit_labels)
    w.edit_record(w.rows[0])
    assert '7' in w.rows[0]['labels']
    original_folder = w.store.root
    assert w.switch_folder(Path(d) / 'other', persist=False)
    assert not w.rows
    assert w.switch_folder(original_folder, persist=False)
    assert len(w.rows) == 1
    old_config = module.CONFIG
    module.CONFIG = Path(d) / 'settings.json'
    assert w.switch_folder(original_folder)
    reopened = module.Window()
    assert reopened.store.root == original_folder
    reopened.close()
    module.CONFIG = old_config
    # Optional fields remain reachable when expanded in a short window.
    toggle = next(b for b in w.label_form.findChildren(QPushButton) if b.text() == 'Ek bilgiler')
    toggle.click()
    app.processEvents()
    assert w.label_form.fields['notes'].isVisible()
    toggle.click()
    w.tabs.setCurrentIndex(0)
    w.live.clear()
    w.live.extend(synthetic(30))
    w.tick()
    w.folder_label.setText('data (demo)')
    app.processEvents()
    out = Path(__file__).resolve().parents[1] / 'docs' / 'arayuz.png'
    out.parent.mkdir(exist_ok=True)
    if '--screenshots' in sys.argv:
        w.grab().save(str(out))
    w.tabs.setCurrentIndex(1)
    app.processEvents()
    if '--screenshots' in sys.argv:
        w.grab().save(str(out.with_name('kayitlar.png')))
    w.close()
print('GUI OK: demo, kayıt, etiket düzenleme, klasör değiştirme ve tercih kalıcılığı')
