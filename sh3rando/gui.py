"""Shadow Hearts: From the New World Randomizer window (PySide6)."""
import json
import os
import random
import subprocess
import sys
import traceback

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QGridLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QTabWidget, QVBoxLayout,
                               QWidget)

from .randomize import REGIONS, Randomizer, Settings, identify, out_name, spoiler_name

# settings.json sits beside the exe when frozen by PyInstaller, else at the repo root
HERE = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
        else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG = os.path.join(HERE, "settings.json")
DEFAULT_PCSX2 = r"C:\Program Files\PCSX2\pcsx2-qt.exe"
DEFAULT_OUT = os.path.join(os.path.expanduser("~"), "Documents")
DEFAULT_ISO = os.path.join(os.path.expanduser("~"), "Downloads", "Shadow Hearts - From the New World (USA).iso")


def load_config():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


class Worker(QObject):
    progress = Signal(float, str)
    finished = Signal(str, str)        # out iso, spoiler path
    failed = Signal(str)

    def __init__(self, iso, out, spoiler, settings):
        super().__init__()
        self.iso, self.out, self.spoiler, self.settings = iso, out, spoiler, settings

    def run(self):
        try:
            log = Randomizer(self.settings, self.progress.emit).run(self.iso, self.out)
            with open(self.spoiler, "w", encoding="utf-8") as f:
                f.write(log)
            self.finished.emit(self.out, self.spoiler)
        except Exception:
            self.failed.emit(traceback.format_exc())


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Shadow Hearts: From the New World Randomizer")
        self.cfg = load_config()
        self.thread = None
        self.last_iso = None
        self._updating = False

        root = QWidget()
        lay = QVBoxLayout(root)
        lay.addWidget(self._files_box())
        lay.addWidget(self._seed_box())
        tabs = QTabWidget()
        tabs.addTab(self._options_page(), "Options")
        self.spoiler = QPlainTextEdit(readOnly=True)
        self.spoiler.setFont(QFont("Consolas", 9))
        self.spoiler.setPlaceholderText("The spoiler log appears here after you generate.")
        tabs.addTab(self.spoiler, "Spoiler log")
        self.tabs = tabs
        lay.addWidget(tabs, 1)
        lay.addLayout(self._action_row())
        self.setCentralWidget(root)
        self.resize(800, 880)

        try:
            s = Settings.from_code(self.cfg.get("code", ""), allow_old=True)
        except ValueError:          # no saved code, or one this version cannot read
            s = Settings()
        if not s.seed:
            s.seed = self._new_seed()
        self._apply(s)

    # -- layout ------------------------------------------------------------------------------------
    def _files_box(self):
        box = QGroupBox("Files")
        g = QGridLayout(box)
        saved = self.cfg.get("iso")
        self.iso = QLineEdit(saved if saved is not None else (DEFAULT_ISO if os.path.isfile(DEFAULT_ISO) else ""))
        self.iso.setPlaceholderText("Clean Shadow Hearts: From the New World image (.iso): USA, Europe or Japan")
        self.region = QLabel()
        self.region.setStyleSheet("color: gray")
        self.iso.textChanged.connect(self._detect)
        self.outdir = QLineEdit(self.cfg.get("outdir") or DEFAULT_OUT)
        self.outdir.setPlaceholderText("Folder for the randomized image")
        self.pcsx2 = QLineEdit(self.cfg.get("pcsx2", DEFAULT_PCSX2 if os.path.exists(DEFAULT_PCSX2) else ""))
        self.pcsx2.setPlaceholderText("pcsx2-qt.exe (optional, for Launch)")
        rows = (("Disc image", self.iso, lambda: self._pick_file(self.iso, "Disc images (*.iso)")),
                ("", self.region, None),
                ("Output folder", self.outdir, lambda: self._pick_dir(self.outdir)),
                ("PCSX2", self.pcsx2, lambda: self._pick_file(self.pcsx2, "Programs (*.exe)")))
        for row, (label, edit, pick) in enumerate(rows):
            g.addWidget(QLabel(label), row, 0)
            g.addWidget(edit, row, 1)
            if pick:
                b = QPushButton("Browse")
                b.clicked.connect(pick)
                g.addWidget(b, row, 2)
        self._detect()
        return box

    def _detect(self, *_):
        """Show which release the chosen image is."""
        src = self.iso.text().strip()
        if not os.path.isfile(src):
            self.region.setText("")
            return
        try:
            region, _ = identify(src)
            self.region.setText("Detected: " + REGIONS[region])
        except (OSError, ValueError) as err:
            self.region.setText(str(err))

    def _seed_box(self):
        box = QGroupBox("Seed")
        g = QGridLayout(box)
        self.seed = QLineEdit()
        self.seed.textChanged.connect(self._changed)
        dice = QPushButton("Random seed")
        dice.clicked.connect(lambda: self.seed.setText(self._new_seed()))
        self.code = QLineEdit()
        self.code.setFont(QFont("Consolas", 10))
        self.code.setToolTip("Share this code: it holds the seed and every option.")
        self.code.editingFinished.connect(self._code_edited)
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.code.text()))
        g.addWidget(QLabel("Seed"), 0, 0)
        g.addWidget(self.seed, 0, 1)
        g.addWidget(dice, 0, 2)
        g.addWidget(QLabel("Code"), 1, 0)
        g.addWidget(self.code, 1, 1)
        g.addWidget(copy, 1, 2)
        return box

    def _check(self, text, tip=""):
        c = QCheckBox(text)
        c.setToolTip(tip)
        c.toggled.connect(self._changed)
        return c

    def _combo(self, items):
        c = QComboBox()
        for label, value in items:
            c.addItem(label, value)
        c.currentIndexChanged.connect(self._changed)
        return c

    def _spin(self, lo, hi, suffix, step=1):
        s = QSpinBox()
        s.setRange(lo, hi)
        s.setSingleStep(step)
        s.setSuffix(suffix)
        s.valueChanged.connect(self._changed)
        return s

    def _group(self, title, master, rows, note=None):
        box = QGroupBox(title)
        v = QVBoxLayout(box)
        v.addWidget(master)
        form = QFormLayout()
        widgets = []
        for label, w in rows:
            if label:
                form.addRow(label, w)
            else:
                form.addRow(w)
            widgets.append(w)
        v.addLayout(form)
        if note:
            n = QLabel(note)
            n.setWordWrap(True)
            n.setStyleSheet("color: gray")
            v.addWidget(n)
        master.toggled.connect(lambda on: [w.setEnabled(on) for w in widgets])
        return box

    def _options_page(self):
        page = QWidget()
        g = QGridLayout(page)

        self.o_chests = self._check("Randomize treasure chests")
        self.o_chest_mode = self._combo([("Shuffle the existing contents", "shuffle"), ("Any item", "random")])
        self.o_chest_mix = self._check("Allow any kind (a weapon chest may hold an accessory)")
        self.o_gifts = self._check("Include items people and events give you")
        chests = self._group("Chests", self.o_chests, [("Contents", self.o_chest_mode), ("", self.o_chest_mix),
                                                      ("", self.o_gifts)],
                             "The \"You got\" message follows the new item. Key items, totem statues, "
                             "inspirations, Lottery Tickets, the numbered keys and items quests check stay where "
                             "they are. Magic and Stellar Charts only trade places with each other.")

        self.o_shops = self._check("Randomize shop stock")
        self.o_shop_mode = self._combo([("Same kind in every slot", "kind"), ("Any kind", "any")])
        self.o_shop_heal = self._check("Shops that sold Thera Leaf still do")
        shops = self._group("Shops", self.o_shops, [("Stock", self.o_shop_mode), ("", self.o_shop_heal)],
                            "Key items for sale stay.")

        self.o_drops = self._check("Randomize monster drops")
        self.o_drop_mix = self._check("Allow any kind")
        self.o_drop_fill = self._check("Monsters that dropped nothing drop something (10%)")
        self.o_drop_rate = self._spin(10, 500, " % of normal", 10)
        drops = self._group("Drops", self.o_drops, [("", self.o_drop_mix), ("", self.o_drop_fill),
                                                    ("Drop chance", self.o_drop_rate)],
                            "Totem statues that monsters drop stay.")

        self.o_stats = self._check("Vary monster stats")
        self.o_variance = self._spin(0, 100, " %", 5)
        self.o_cash = self._spin(0, 500, " % of normal", 10)
        self.o_exp = self._spin(0, 500, " % of normal", 10)
        stats = self._group("Monsters", self.o_stats, [("HP, MP, stats \u00b1", self.o_variance),
                                                       ("Cash", self.o_cash), ("EXP", self.o_exp)])

        self.o_enc = self._check("Randomize encounter groups (experimental)")
        self.o_spread = self._spin(1, 60, " ranks")
        enc = self._group("Encounters", self.o_enc, [("Strength range", self.o_spread)],
                          "Each monster in a random encounter becomes one of similar strength (ranked by HP). "
                          "Bosses and story fights are kept. Not yet play-tested.")

        g.addWidget(chests, 0, 0)
        g.addWidget(shops, 0, 1)
        g.addWidget(drops, 1, 0)
        g.addWidget(stats, 1, 1)
        g.addWidget(enc, 2, 0, 1, 2)
        g.setRowStretch(3, 1)
        return page

    def _action_row(self):
        h = QHBoxLayout()
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(True)
        self.bar.setFormat("")
        self.go = QPushButton("Generate")
        self.go.setDefault(True)
        self.go.setMinimumWidth(120)
        self.go.clicked.connect(self._generate)
        self.launch = QPushButton("Launch in PCSX2")
        self.launch.setEnabled(False)
        self.launch.clicked.connect(self._launch)
        self.open = QPushButton("Open folder")
        self.open.setEnabled(False)
        self.open.clicked.connect(lambda: os.startfile(os.path.dirname(self.last_iso)))
        h.addWidget(self.bar, 1)
        h.addWidget(self.open)
        h.addWidget(self.launch)
        h.addWidget(self.go)
        return h

    # -- settings <-> widgets ----------------------------------------------------------------------
    def _new_seed(self):
        return "%08d" % random.randrange(10 ** 8)

    def _settings(self):
        return Settings(
            seed=self.seed.text().strip(),
            chests=self.o_chests.isChecked(), chest_mode=self.o_chest_mode.currentData(),
            chest_mix_kinds=self.o_chest_mix.isChecked(), gifts=self.o_gifts.isChecked(),
            shops=self.o_shops.isChecked(), shop_mode=self.o_shop_mode.currentData(),
            shop_keep_healing=self.o_shop_heal.isChecked(),
            drops=self.o_drops.isChecked(), drop_mix_kinds=self.o_drop_mix.isChecked(),
            drop_fill_empty=self.o_drop_fill.isChecked(), drop_rate=self.o_drop_rate.value(),
            stats=self.o_stats.isChecked(), stat_variance=self.o_variance.value(), cash_rate=self.o_cash.value(),
            exp_rate=self.o_exp.value(),
            encounters=self.o_enc.isChecked(), encounter_spread=self.o_spread.value())

    def _apply(self, s):
        self._updating = True
        self.seed.setText(s.seed)
        for box, on in ((self.o_chests, s.chests), (self.o_chest_mix, s.chest_mix_kinds), (self.o_gifts, s.gifts),
                        (self.o_shops, s.shops), (self.o_shop_heal, s.shop_keep_healing), (self.o_drops, s.drops),
                        (self.o_drop_mix, s.drop_mix_kinds), (self.o_drop_fill, s.drop_fill_empty),
                        (self.o_stats, s.stats), (self.o_enc, s.encounters)):
            box.setChecked(not on)    # force a toggled signal so dependent widgets follow
            box.setChecked(on)
        self.o_chest_mode.setCurrentIndex(self.o_chest_mode.findData(s.chest_mode))
        self.o_shop_mode.setCurrentIndex(self.o_shop_mode.findData(s.shop_mode))
        self.o_drop_rate.setValue(s.drop_rate)
        self.o_variance.setValue(s.stat_variance)
        self.o_cash.setValue(s.cash_rate)
        self.o_exp.setValue(s.exp_rate)
        self.o_spread.setValue(s.encounter_spread)
        self._updating = False
        self._changed()

    def _changed(self, *_):
        if self._updating:
            return
        self.code.setText(self._settings().to_code())

    def _code_edited(self):
        try:
            s = Settings.from_code(self.code.text())
        except ValueError as e:
            QMessageBox.warning(self, "Code", str(e))
            self._changed()
            return
        self._apply(s)

    # -- actions -----------------------------------------------------------------------------------
    def _pick_file(self, edit, filt):
        p, _ = QFileDialog.getOpenFileName(self, "Choose a file", os.path.dirname(edit.text()), filt)
        if p:
            edit.setText(os.path.normpath(p))

    def _pick_dir(self, edit):
        p = QFileDialog.getExistingDirectory(self, "Choose a folder", edit.text())
        if p:
            edit.setText(os.path.normpath(p))

    def _remember(self):
        self.cfg.update(iso=self.iso.text(), outdir=self.outdir.text(),
                        pcsx2=self.pcsx2.text(), code=self.code.text())
        save_config(self.cfg)

    def _generate(self):
        src = self.iso.text().strip()
        if not src:
            QMessageBox.warning(self, "Disc", "Choose a clean Shadow Hearts: From the New World image first.")
            return
        try:
            region, _ = identify(src)
        except (OSError, ValueError) as err:
            QMessageBox.warning(self, "Disc", str(err))
            return
        s = self._settings()
        if not s.seed:
            s.seed = self._new_seed()
            self.seed.setText(s.seed)
        outdir = self.outdir.text().strip() or DEFAULT_OUT
        out = os.path.join(outdir, out_name(region, 0, s.seed))
        if os.path.normcase(os.path.abspath(out)) == os.path.normcase(os.path.abspath(src)):
            QMessageBox.warning(self, "Output", "The output would overwrite the clean image.")
            return
        self._remember()
        self.go.setEnabled(False)
        self.launch.setEnabled(False)
        self.open.setEnabled(False)
        self.thread = QThread()
        self.worker = Worker(src, out, os.path.join(outdir, spoiler_name(region, s.seed)), s)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._progress)
        self.worker.finished.connect(self._done)
        self.worker.failed.connect(self._failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.start()

    def _progress(self, frac, msg):
        self.bar.setValue(int(frac * 1000))
        self.bar.setFormat(msg)

    def _done(self, out, spoiler):
        self.last_iso = out
        with open(spoiler, encoding="utf-8") as f:
            self.spoiler.setPlainText(f.read())
        self.bar.setFormat("Done: " + os.path.basename(out))
        self.go.setEnabled(True)
        self.open.setEnabled(True)
        self.launch.setEnabled(os.path.isfile(self.pcsx2.text()))
        self.tabs.setCurrentIndex(1)

    def _failed(self, tb):
        self.bar.setFormat("Failed")
        self.go.setEnabled(True)
        QMessageBox.critical(self, "Generation failed", tb)

    def _launch(self):
        exe = self.pcsx2.text().strip()
        if not os.path.isfile(exe):
            QMessageBox.warning(self, "PCSX2", "Set the path to pcsx2-qt.exe first.")
            return
        subprocess.Popen([exe, "-fastboot", "--", self.last_iso], cwd=os.path.dirname(exe))

    def closeEvent(self, e):
        if self.thread and self.thread.isRunning():
            e.ignore()
            return
        self._remember()
        super().closeEvent(e)


def main():
    app = QApplication(sys.argv)
    w = Window()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
