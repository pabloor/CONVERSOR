"""Aplicación de escritorio (PySide6)."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
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
from .convert import (
    HOW_TO_EXPORT_PRO_TOOLS,
    TARGET_LABELS,
    TARGETS,
    build_report,
    convert,
    default_targets,
    load,
    summary,
    supported_extensions,
)


class _Worker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, sources, output, targets, template, tempo):
        super().__init__()
        self.args = (sources, output, targets, template, tempo)

    def run(self) -> None:
        try:
            self.finished.emit(convert(*self.args))
        except Exception as error:  # noqa: BLE001 - se muestra al usuario
            self.failed.emit(str(error))


class DropArea(QFrame):
    dropped = Signal(list)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.StyledPanel)
        self.setMinimumHeight(90)
        layout = QVBoxLayout(self)
        label = QLabel(
            "Arrastra aquí una sesión de Ableton (.als)\n"
            "o el AAF y el MIDI exportados de Pro Tools"
        )
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if any(_is_supported(Path(u.toLocalFile())) for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls()]
        paths = [p for p in paths if _is_supported(p)]
        if paths:
            self.dropped.emit(paths)


def _is_supported(path: Path) -> bool:
    return path.suffix.lower() in supported_extensions()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Conversor de sesiones {__version__}")
        self.resize(760, 680)
        self._thread: QThread | None = None
        self._output_dir: Path | None = None
        self.sources: list[Path] = []

        self.drop = DropArea()
        self.drop.dropped.connect(self.set_sources)

        self.source_edit = QLineEdit()
        self.source_edit.setReadOnly(True)
        self.source_edit.setPlaceholderText("Ninguna sesión seleccionada")
        open_button = QPushButton("Abrir…")
        open_button.clicked.connect(self.choose_sources)

        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("Carpeta de destino")
        output_button = QPushButton("Cambiar…")
        output_button.clicked.connect(self.choose_output)

        self.target_checks = {name: QCheckBox(TARGET_LABELS[name]) for name in TARGETS}

        self.tempo_spin = QDoubleSpinBox()
        self.tempo_spin.setRange(0, 999)
        self.tempo_spin.setDecimals(2)
        self.tempo_spin.setSpecialValueText("del MIDI (o 120)")
        self.tempo_spin.setSuffix(" BPM")
        self.tempo_spin.valueChanged.connect(self.refresh_preview)
        self.template_edit = QLineEdit()
        self.template_edit.setPlaceholderText("Plantilla incluida (formato Live 11)")
        template_button = QPushButton("Elegir…")
        template_button.clicked.connect(self.choose_template)
        self.reverse_box = QGroupBox("Opciones para Ableton")
        reverse_layout = QFormLayout(self.reverse_box)
        reverse_layout.addRow("Tempo si no hay MIDI:", self.tempo_spin)
        template_row = QHBoxLayout()
        template_row.addWidget(self.template_edit)
        template_row.addWidget(template_button)
        reverse_layout.addRow("Plantilla (.als vacío):", template_row)

        self.convert_button = QPushButton("Convertir")
        self.convert_button.setEnabled(False)
        self.convert_button.clicked.connect(self.start_conversion)
        self.show_button = QPushButton("Abrir carpeta")
        self.show_button.setEnabled(False)
        self.show_button.clicked.connect(self.open_output)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlainText(HOW_TO_EXPORT_PRO_TOOLS)

        source_row = QHBoxLayout()
        source_row.addWidget(self.source_edit)
        source_row.addWidget(open_button)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_edit)
        output_row.addWidget(output_button)

        targets_box = QGroupBox("Formatos de salida")
        targets_layout = QVBoxLayout(targets_box)
        for check in self.target_checks.values():
            targets_layout.addWidget(check)

        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.show_button)
        buttons.addWidget(self.convert_button)

        layout = QVBoxLayout()
        layout.addWidget(self.drop)
        layout.addWidget(QLabel("Origen"))
        layout.addLayout(source_row)
        layout.addWidget(QLabel("Destino"))
        layout.addLayout(output_row)
        options = QHBoxLayout()
        options.addWidget(targets_box)
        options.addWidget(self.reverse_box, 1)
        layout.addLayout(options)
        layout.addLayout(buttons)
        layout.addWidget(self.log, 1)
        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)
        self._update_targets()

    def choose_sources(self) -> None:
        patterns = " ".join(f"*{ext}" for ext in supported_extensions())
        filenames, _ = QFileDialog.getOpenFileNames(
            self, "Abrir sesión (puedes elegir un AAF y un MIDI a la vez)", "", f"Sesiones ({patterns})"
        )
        if filenames:
            self.set_sources([Path(f) for f in filenames])

    def choose_output(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Carpeta de destino", self.output_edit.text())
        if directory:
            self.output_edit.setText(directory)

    def choose_template(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Set vacío de Ableton", "", "Sets de Ableton (*.als)")
        self.template_edit.setText(filename)

    def set_sources(self, paths: list[Path]) -> None:
        self.sources = paths
        self.source_edit.setText("  +  ".join(p.name for p in paths))
        first = paths[0]
        self.output_edit.setText(str(first.parent / f"{first.stem} (convertido)"))
        self.show_button.setEnabled(False)
        self._update_targets()
        self.refresh_preview()

    def _update_targets(self) -> None:
        from_ableton = any(p.suffix.lower() == ".als" for p in self.sources)
        defaults = default_targets(self.sources) if self.sources else ()
        for name, check in self.target_checks.items():
            check.setEnabled(bool(self.sources) and (name != "ableton") == from_ableton)
            check.setChecked(name in defaults)
        self.reverse_box.setEnabled(bool(self.sources) and not from_ableton)

    def _tempo(self) -> float | None:
        return self.tempo_spin.value() or None

    def refresh_preview(self) -> None:
        if not self.sources:
            return
        try:
            project = load(self.sources, self._tempo())
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
            name for name, check in self.target_checks.items() if check.isEnabled() and check.isChecked()
        )
        if not targets:
            QMessageBox.warning(self, "Conversor", "Elige al menos un formato de salida.")
            return
        output = Path(self.output_edit.text())
        self._output_dir = output
        template = self.template_edit.text() or None
        self.convert_button.setEnabled(False)
        self.log.setPlainText("Convirtiendo…")

        self._thread = QThread(self)
        worker = _Worker(self.sources, output, targets, template, self._tempo())
        worker.moveToThread(self._thread)
        self._thread.started.connect(worker.run)
        # Métodos de la ventana (no lambdas) para que Qt los ejecute en el hilo principal.
        worker.finished.connect(self.conversion_done)
        worker.failed.connect(self.conversion_failed)
        for signal in (worker.finished, worker.failed):
            signal.connect(self._thread.quit)
            signal.connect(worker.deleteLater)
        self._worker = worker
        self._thread.start()

    @Slot(object)
    def conversion_done(self, result) -> None:
        self.log.setPlainText(build_report(result))
        self.convert_button.setEnabled(True)
        self.show_button.setEnabled(True)

    @Slot(str)
    def conversion_failed(self, message: str) -> None:
        self.log.setPlainText(f"Error durante la conversión:\n{message}")
        self.convert_button.setEnabled(True)

    def open_output(self) -> None:
        if self._output_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._output_dir)))


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    paths = [Path(a) for a in sys.argv[1:] if _is_supported(Path(a))]
    if paths:
        window.set_sources(paths)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
