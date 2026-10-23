"""Aplicación de escritorio (PySide6)."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .convert import build_report, convert, load, summary, supported_extensions


class _Worker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, source: Path, output: Path, targets: tuple[str, ...]):
        super().__init__()
        self.source, self.output, self.targets = source, output, targets

    def run(self) -> None:
        try:
            self.finished.emit(convert(self.source, self.output, self.targets))
        except Exception as error:  # noqa: BLE001 - se muestra al usuario
            self.failed.emit(str(error))


class DropArea(QFrame):
    dropped = Signal(Path)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.StyledPanel)
        self.setMinimumHeight(90)
        layout = QVBoxLayout(self)
        self.label = QLabel("Arrastra aquí una sesión\n(" + ", ".join(supported_extensions()) + ")")
        self.label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.label)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if any(_is_supported(Path(u.toLocalFile())) for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if _is_supported(path):
                self.dropped.emit(path)
                return


def _is_supported(path: Path) -> bool:
    return path.suffix.lower() in supported_extensions()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Conversor de sesiones {__version__}")
        self.resize(720, 560)
        self._thread: QThread | None = None
        self._output_dir: Path | None = None

        self.drop = DropArea()
        self.drop.dropped.connect(self.set_source)

        self.source_edit = QLineEdit()
        self.source_edit.setReadOnly(True)
        self.source_edit.setPlaceholderText("Ninguna sesión seleccionada")
        open_button = QPushButton("Abrir sesión…")
        open_button.clicked.connect(self.choose_source)

        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("Carpeta de destino")
        output_button = QPushButton("Cambiar…")
        output_button.clicked.connect(self.choose_output)

        self.aaf_check = QCheckBox("AAF: pistas y clips de audio (Pro Tools, Logic, Cubase)")
        self.midi_check = QCheckBox("MIDI: notas, tempo y compás (todos los DAW)")
        self.aaf_check.setChecked(True)
        self.midi_check.setChecked(True)

        self.convert_button = QPushButton("Convertir")
        self.convert_button.setEnabled(False)
        self.convert_button.clicked.connect(self.start_conversion)
        self.show_button = QPushButton("Abrir carpeta")
        self.show_button.setEnabled(False)
        self.show_button.clicked.connect(self.open_output)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)

        source_row = QHBoxLayout()
        source_row.addWidget(self.source_edit)
        source_row.addWidget(open_button)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_edit)
        output_row.addWidget(output_button)

        targets_box = QGroupBox("Formatos de salida")
        targets_layout = QVBoxLayout(targets_box)
        targets_layout.addWidget(self.aaf_check)
        targets_layout.addWidget(self.midi_check)

        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.show_button)
        buttons.addWidget(self.convert_button)

        layout = QVBoxLayout()
        layout.addWidget(self.drop)
        layout.addWidget(QLabel("Sesión de origen"))
        layout.addLayout(source_row)
        layout.addWidget(QLabel("Destino"))
        layout.addLayout(output_row)
        layout.addWidget(targets_box)
        layout.addLayout(buttons)
        layout.addWidget(self.log, 1)
        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

    def choose_source(self) -> None:
        patterns = " ".join(f"*{ext}" for ext in supported_extensions())
        filename, _ = QFileDialog.getOpenFileName(
            self, "Abrir sesión", "", f"Sesiones ({patterns})"
        )
        if filename:
            self.set_source(Path(filename))

    def choose_output(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Carpeta de destino", self.output_edit.text())
        if directory:
            self.output_edit.setText(directory)

    def set_source(self, path: Path) -> None:
        self.source_edit.setText(str(path))
        self.output_edit.setText(str(path.parent / f"{path.stem} (convertido)"))
        self.show_button.setEnabled(False)
        try:
            project = load(path)
        except Exception as error:  # noqa: BLE001
            self.convert_button.setEnabled(False)
            self.log.setPlainText(f"No se puede leer la sesión:\n{error}")
            return
        self.convert_button.setEnabled(True)
        text = summary(project)
        if project.warnings:
            text += "\n\nAvisos:\n" + "\n".join(f"  - {w}" for w in project.warnings)
        self.log.setPlainText(text)

    def start_conversion(self) -> None:
        targets = tuple(
            name
            for name, check in (("aaf", self.aaf_check), ("midi", self.midi_check))
            if check.isChecked()
        )
        if not targets:
            QMessageBox.warning(self, "Conversor", "Elige al menos un formato de salida.")
            return
        source = Path(self.source_edit.text())
        output = Path(self.output_edit.text())
        self.convert_button.setEnabled(False)
        self.log.setPlainText("Convirtiendo…")

        self._thread = QThread(self)
        worker = _Worker(source, output, targets)
        worker.moveToThread(self._thread)
        self._thread.started.connect(worker.run)
        worker.finished.connect(lambda result: self.conversion_done(result, output))
        worker.failed.connect(self.conversion_failed)
        for signal in (worker.finished, worker.failed):
            signal.connect(self._thread.quit)
            signal.connect(worker.deleteLater)
        self._worker = worker
        self._thread.start()

    def conversion_done(self, result, output: Path) -> None:
        self._output_dir = output
        self.log.setPlainText(build_report(result))
        self.convert_button.setEnabled(True)
        self.show_button.setEnabled(True)

    def conversion_failed(self, message: str) -> None:
        self.log.setPlainText(f"Error durante la conversión:\n{message}")
        self.convert_button.setEnabled(True)

    def open_output(self) -> None:
        if self._output_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._output_dir)))


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    if len(sys.argv) > 1:
        window.set_source(Path(sys.argv[1]))
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
