"""New recording dialog: two documents, three IDs, then Start.

Start is enabled only with a consent form, a questionnaire, all three IDs and a
connected Polar H10. Attaching a document fills any empty ID field it can find
(marked "From <file>" until the user edits it); IDs that differ between files
are reported in the hint but do not block.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..config import DOC_FILTERS
from ..documents import extract_ids, load_document
from . import theme
from .widgets import Button, ElidedLabel, set_tone, text_label

SETTINGS = ("Shobotics", "Sonorium")
FACILITATOR_KEY = "facilitator"
DOC_TITLES = {"consent": "Consent form", "questionnaire": "Questionnaire"}
FIELDS = (  # key, label, placeholder
    ("p", "Participant ID", "PT-025"),
    ("s", "Session ID", "S-2026-019"),
    ("f", "Facilitator ID", "FC-03"),
)


def size_text(n: int) -> str:
    return f"{max(1, round(n / 1024))} KB" if n < 1048576 else f"{n / 1048576:.1f} MB"


def join_and(items: list[str]) -> str:
    """ "a, b and c"."""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


class DocZone(QFrame):
    """Drop zone and file list for one document kind."""

    files_added = Signal(str, list)  # kind, paths
    removed = Signal(str, int)  # kind, index

    def __init__(self, kind: str):
        super().__init__()
        self.kind = kind
        self.setObjectName("doc")
        self.setAcceptDrops(True)
        col = QVBoxLayout(self)
        col.setContentsMargins(10, 10, 10, 10)
        col.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(text_label(DOC_TITLES[kind], 12.5, QFont.Bold), 1)
        self.req = QLabel("Required")
        self.req.setObjectName("req")
        row.addWidget(self.req)
        attach = Button("Attach")
        attach.clicked.connect(self._browse)
        row.addWidget(attach)
        col.addLayout(row)
        self.list = QVBoxLayout()
        self.list.setSpacing(2)
        col.addLayout(self.list)
        col.addStretch(1)

    def _browse(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Attach " + DOC_TITLES[self.kind].lower(), "", DOC_FILTERS[self.kind]
        )
        if paths:
            self.files_added.emit(self.kind, paths)

    def show_files(self, docs) -> None:
        while self.list.count():
            item = self.list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not docs:
            self.list.addWidget(text_label("Drop a file or attach", 11.5, tone="3"))
        for i, d in enumerate(docs):
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(6)
            name = ElidedLabel(d.name, 11.5, mono=True)
            name.setToolTip(d.name)
            h.addWidget(name, 1)
            h.addWidget(text_label(size_text(d.size), 11.5, tone="3", mono=True))
            rm = QPushButton("Remove")
            rm.setObjectName("remove")
            rm.setCursor(Qt.PointingHandCursor)
            rm.clicked.connect(lambda _=False, i=i: self.removed.emit(self.kind, i))
            h.addWidget(rm)
            self.list.addWidget(row)
        self.req.setText("Attached" if docs else "Required")
        self.req.setProperty("ok", bool(docs))
        self.setProperty("has", bool(docs))
        self._repolish(self.req, self)

    def _set_drag(self, on: bool):
        self.setProperty("drag", on)
        self._repolish(self)

    @staticmethod
    def _repolish(*widgets):
        for w in widgets:
            w.style().unpolish(w)
            w.style().polish(w)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._set_drag(True)

    def dragLeaveEvent(self, e):
        self._set_drag(False)

    def dropEvent(self, e):
        self._set_drag(False)
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        paths = [p for p in paths if Path(p).is_file()]
        if paths:
            e.acceptProposedAction()
            self.files_added.emit(self.kind, paths)


class NewRecordingDialog(QDialog):
    start_requested = Signal(dict, dict)  # ids, documents
    connect_requested = Signal()

    def __init__(self, parent, is_connected, has_unsaved):
        """is_connected / has_unsaved: callables read when the hint refreshes."""
        super().__init__(parent)
        self.is_connected = is_connected
        self.has_unsaved = has_unsaved
        self.setWindowTitle("New recording")
        self.setModal(True)
        self.setFixedWidth(600)
        self.draft = {"consent": [], "questionnaire": []}
        self.found: dict[str, dict] = {}  # k -> {"value", "from", "conflict"}
        self.read_errors: list[str] = []

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        head = QFrame()
        head.setObjectName("boxHead")
        h = QHBoxLayout(head)
        h.setContentsMargins(16, 12, 16, 12)
        h.addWidget(text_label("New recording", 15, QFont.Bold))
        h.addStretch(1)
        cancel = Button("Cancel", "ghost", key="Esc")
        cancel.clicked.connect(self.reject)
        h.addWidget(cancel)
        col.addWidget(head)

        body = QVBoxLayout()
        body.setContentsMargins(16, 14, 16, 14)
        body.setSpacing(12)
        docs = QGridLayout()
        docs.setSpacing(8)
        self.zones = {}
        for i, kind in enumerate(("consent", "questionnaire")):
            z = DocZone(kind)
            z.files_added.connect(self.add_docs)
            z.removed.connect(self.remove_doc)
            docs.addWidget(z, 0, i)
            docs.setColumnStretch(i, 1)
            self.zones[kind] = z
        body.addLayout(docs)

        fields = QGridLayout()
        fields.setHorizontalSpacing(8)
        fields.setVerticalSpacing(4)
        self.inputs: dict[str, QLineEdit] = {}
        self.sources: dict[str, ElidedLabel] = {}
        for i, (k, label, placeholder) in enumerate(FIELDS):
            fields.addWidget(text_label(label, 11.5, QFont.DemiBold, tone="2"), 0, i)
            e = QLineEdit()
            e.setPlaceholderText(placeholder)
            e.setFont(theme.mono_font(13))
            e.textEdited.connect(lambda _t, k=k: self._edited(k))  # user edits only
            fields.addWidget(e, 1, i)
            src = ElidedLabel("", 10.5, tone="3")
            src.setFixedHeight(14)
            fields.addWidget(src, 2, i)
            fields.setColumnStretch(i, 1)
            self.inputs[k], self.sources[k] = e, src
        body.addLayout(fields)
        col.addLayout(body)

        foot = QFrame()
        foot.setObjectName("dialogFoot")
        f = QHBoxLayout(foot)
        f.setContentsMargins(16, 10, 16, 10)
        f.setSpacing(10)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setFont(theme.ui_font(12))
        f.addWidget(self.hint, 1)
        self.connect_btn = Button("Connect Polar H10")
        self.connect_btn.clicked.connect(self.connect_requested.emit)
        f.addWidget(self.connect_btn)
        self.start_btn = Button("Start recording", "primary", key="↵", large=True)
        self.start_btn.clicked.connect(self._start)
        f.addWidget(self.start_btn)
        col.addWidget(foot)

        self.reset()

    # -- documents ----------------------------------------------------------

    def add_docs(self, kind: str, paths: list) -> None:
        self.read_errors = []
        for path in paths:
            try:
                doc = load_document(path)
            except OSError as exc:
                self.read_errors.append(f"{Path(path).name} ({exc.strerror or exc})")
                continue
            self.draft[kind].append(doc)
            for k, value in extract_ids(doc.name, doc.data).items():
                prev = self.found.get(k)
                if prev and prev["value"] != value:
                    prev["conflict"] = (
                        f"{prev['value']} in {prev['from']}, {value} in {doc.name}"
                    )
                    continue
                if not prev:
                    self.found[k] = {"value": value, "from": doc.name}
                e = self.inputs[k]
                if not e.text().strip():
                    e.setText(value)
                    e.setProperty("auto", True)
                    self._repolish(e)
                    self.sources[k].setText("From " + doc.name)
                    self.sources[k].setToolTip(doc.name)
        self.zones[kind].show_files(self.draft[kind])
        self.refresh()

    def remove_doc(self, kind: str, index: int) -> None:
        self.read_errors = []
        del self.draft[kind][index]
        self.zones[kind].show_files(self.draft[kind])
        self.refresh()

    # -- fields -------------------------------------------------------------

    def _edited(self, k: str) -> None:
        e = self.inputs[k]
        if e.property("auto"):
            e.setProperty("auto", False)
            self._repolish(e)
        self.sources[k].setText("")
        self.refresh()

    @staticmethod
    def _repolish(w):
        w.style().unpolish(w)
        w.style().polish(w)

    def ids(self) -> dict:
        return {k: e.text().strip() for k, e in self.inputs.items()}

    # -- state --------------------------------------------------------------

    def reset(self) -> None:
        """Clear everything except the remembered facilitator."""
        self.draft = {"consent": [], "questionnaire": []}
        self.found = {}
        self.read_errors = []
        for k, e in self.inputs.items():
            e.clear()
            e.setProperty("auto", False)
            self._repolish(e)
            self.sources[k].setText("")
        self.inputs["f"].setText(
            str(QSettings(*SETTINGS).value(FACILITATOR_KEY, "") or "")
        )
        for kind, z in self.zones.items():
            z.show_files(self.draft[kind])
        self.refresh()

    def refresh(self) -> None:
        """Enable Start and write the one-line hint."""
        connected = self.is_connected()
        missing = []
        if not self.draft["consent"]:
            missing.append("consent form")
        if not self.draft["questionnaire"]:
            missing.append("questionnaire")
        if not all(self.ids().values()):
            missing.append("IDs")
        if not connected:
            missing.append("a connected Polar H10")
        conflicts = [f["conflict"] for f in self.found.values() if f.get("conflict")]
        unsaved = self.has_unsaved()

        self.start_btn.setEnabled(not missing)
        self.connect_btn.setVisible(not connected)
        if self.read_errors:
            text, warn = "Couldn't read " + "; ".join(self.read_errors) + ".", True
        elif missing:
            text, warn = "Needs " + join_and(missing) + ".", bool(conflicts)
        elif conflicts:
            text = "IDs differ between files: " + "; ".join(conflicts)
            text, warn = text + ". Check before starting.", True
        elif unsaved:
            text, warn = "The last session wasn't saved. Starting discards it.", True
        else:
            text, warn = "Ready to record.", False
        self.hint.setText(text)
        set_tone(self.hint, "warn" if warn else "3")

    def _start(self) -> None:
        if not self.start_btn.isEnabled():
            return
        ids = self.ids()
        QSettings(*SETTINGS).setValue(FACILITATOR_KEY, ids["f"])
        docs = self.draft
        self.reset()
        self.accept()
        self.start_requested.emit(ids, docs)

    def showEvent(self, e):
        self.refresh()
        super().showEvent(e)
        # Start typing in the first empty ID field.
        empty = [e for e in self.inputs.values() if not e.text().strip()]
        (empty[0] if empty else self.inputs["p"]).setFocus()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._start()
            return
        super().keyPressEvent(e)
