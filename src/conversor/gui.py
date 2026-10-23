"""Aplicación de escritorio (PySide6)."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QSettings, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QDesktopServices, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
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
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import __version__, update
from .convert import (
    HOW_TO_EXPORT_PRO_TOOLS,
    TARGET_LABELS,
    TARGETS,
    build_report,
    convert,
    convert_many,
    default_output,
    default_targets,
    load,
    summary,
    supported_extensions,
)

SAMPLE_RATES = [("Automática (la de los archivos)", None), ("44,1 kHz", 44100), ("48 kHz", 48000),
                ("88,2 kHz", 88200), ("96 kHz", 96000)]


class _Worker(QObject):
    progress = Signal(float, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, job):
        super().__init__()
        self.job = job

    def run(self) -> None:
        try:
            self.finished.emit(self.job(lambda fraction, text: self.progress.emit(fraction, text)))
        except Exception as error:  # noqa: BLE001 - se muestra al usuario
            self.failed.emit(str(error))


class DropArea(QFrame):
    dropped = Signal(list)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.StyledPanel)
        self.setMinimumHeight(80)
        layout = QVBoxLayout(self)
        label = QLabel(
            "Arrastra aquí una o varias sesiones de Ableton (.als)\n"
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
        self.resize(780, 760)
        self.settings = QSettings("pabloor", "Conversor")
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
        self.output_edit.setPlaceholderText("Junto a cada sesión, en «<sesión> (convertido)»")
        output_button = QPushButton("Cambiar…")
        output_button.clicked.connect(self.choose_output)

        self.target_checks = {name: QCheckBox(TARGET_LABELS[name]) for name in TARGETS}

        # Opciones para exportar desde Ableton.
        self.rate_combo = QComboBox()
        for label, rate in SAMPLE_RATES:
            self.rate_combo.addItem(label, rate)
        self.rate_combo.setCurrentIndex(int(self.settings.value("rate_index", 0)))
        self.skip_muted_check = QCheckBox("No exportar las pistas silenciadas")
        self.skip_muted_check.setChecked(self.settings.value("skip_muted", "false") == "true")
        self.group_check = QCheckBox("Poner el nombre del grupo delante («Batería - Bombo»)")
        self.group_check.setChecked(self.settings.value("group_prefix", "true") == "true")
        self.export_box = QGroupBox("Opciones de exportación")
        export_layout = QFormLayout(self.export_box)
        export_layout.addRow("Frecuencia de muestreo:", self.rate_combo)
        export_layout.addRow(self.skip_muted_check)
        export_layout.addRow(self.group_check)

        # Opciones para crear un set de Ableton desde Pro Tools.
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
        self.reverse_box = QGroupBox("Opciones para crear un set de Ableton")
        reverse_layout = QFormLayout(self.reverse_box)
        reverse_layout.addRow("Tempo si no hay MIDI:", self.tempo_spin)
        template_row = QHBoxLayout()
        template_row.addWidget(self.template_edit)
        template_row.addWidget(template_button)
        reverse_layout.addRow("Plantilla (.als vacío):", template_row)

        self.convert_button = QPushButton("Convertir")
        self.convert_button.setDefault(True)
        self.convert_button.setEnabled(False)
        self.convert_button.clicked.connect(self.start_conversion)
        self.show_button = QPushButton("Abrir carpeta")
        self.show_button.setEnabled(False)
        self.show_button.clicked.connect(self.open_output)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setTextVisible(False)
        self.progress_label = QLabel("")
        self.progress_bar.hide()

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
        buttons.addWidget(self.progress_label, 1)
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
        options.addWidget(self.export_box, 1)
        layout.addLayout(options)
        layout.addWidget(self.reverse_box)
        layout.addWidget(self.progress_bar)
        layout.addLayout(buttons)
        layout.addWidget(self.log, 1)
        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)
        self._update_targets()

        self._update_thread: QThread | None = None
        self._pending_update: update.Update | None = None
        self._create_menu()
        if self.auto_update_action.isChecked() and update.installed_app() is not None:
            # Tras abrir la ventana, para no retrasar el arranque.
            QTimer.singleShot(1500, lambda: self.check_for_updates(manual=False))

    # --- Actualizaciones ---

    def _create_menu(self) -> None:
        menu = self.menuBar().addMenu("Ayuda")
        check_action = QAction("Buscar actualizaciones…", self)
        check_action.setMenuRole(QAction.ApplicationSpecificRole)
        check_action.triggered.connect(lambda: self.check_for_updates(manual=True))
        menu.addAction(check_action)
        self.auto_update_action = QAction("Buscar actualizaciones al abrir", self)
        self.auto_update_action.setCheckable(True)
        self.auto_update_action.setChecked(self.settings.value("auto_update", "true") == "true")
        self.auto_update_action.toggled.connect(
            lambda on: self.settings.setValue("auto_update", "true" if on else "false")
        )
        menu.addAction(self.auto_update_action)
        releases = QAction("Página de versiones", self)
        releases.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(update.RELEASES_PAGE)))
        menu.addAction(releases)

    def _run_update_job(self, job, done, failed) -> None:
        self._update_thread = QThread(self)
        worker = _Worker(job)
        worker.moveToThread(self._update_thread)
        self._update_thread.started.connect(worker.run)
        worker.progress.connect(self.conversion_progress)
        worker.finished.connect(done)
        worker.failed.connect(failed)
        for signal in (worker.finished, worker.failed):
            signal.connect(self._update_thread.quit)
            signal.connect(worker.deleteLater)
        self._update_worker = worker
        self._update_thread.start()

    def check_for_updates(self, manual: bool) -> None:
        if self._update_thread is not None and self._update_thread.isRunning():
            return
        self._manual_check = manual
        self._run_update_job(lambda progress: update.check(), self.update_checked, self.update_check_failed)

    @Slot(object)
    def update_checked(self, available) -> None:
        if available is None:
            if self._manual_check:
                QMessageBox.information(self, "Conversor", f"Tienes la última versión ({__version__}).")
            return
        if not self._manual_check and self.settings.value("skipped_version", "") == available.version:
            return
        if self._thread is not None and self._thread.isRunning():
            # No se interrumpe una conversión: se pregunta al terminar.
            self._pending_update = available
            return
        self.offer_update(available)

    @Slot(str)
    def update_check_failed(self, message: str) -> None:
        if self._manual_check:
            QMessageBox.warning(self, "Conversor", message)

    def offer_update(self, available: update.Update) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Actualización disponible")
        box.setText(f"Hay una versión nueva de Conversor: {available.version} (tienes la {__version__}).")
        if available.notes:
            box.setInformativeText("Novedades:")
            box.setDetailedText(available.notes)
        install = box.addButton("Actualizar", QMessageBox.AcceptRole)
        later = box.addButton("Más tarde", QMessageBox.RejectRole)
        skip = box.addButton("Saltar esta versión", QMessageBox.DestructiveRole)
        box.setDefaultButton(install)
        box.exec()
        clicked = box.clickedButton()
        if clicked is skip:
            self.settings.setValue("skipped_version", available.version)
        elif clicked is install:
            self.install_update(available)
        del later

    def install_update(self, available: update.Update) -> None:
        app = update.installed_app()
        if not update.can_install(app):
            # Desde el código fuente, o sin permiso para escribir en la carpeta de la aplicación.
            QMessageBox.information(
                self, "Conversor",
                "No se puede actualizar automáticamente desde aquí. Se abrirá la página de "
                "la versión nueva para descargarla.",
            )
            QDesktopServices.openUrl(QUrl(available.page))
            return
        self.convert_button.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.progress_label.setText(f"Descargando la versión {available.version}…")
        workdir = Path(tempfile.mkdtemp(prefix="conversor-descarga-"))

        def job(progress):
            dmg = update.download(available, workdir, lambda f: progress(0.9 * f, "Descargando la actualización…"))
            progress(0.95, "Preparando la actualización…")
            return update.stage(dmg, app, available.version)

        self._run_update_job(job, self.update_staged, self.update_failed)

    @Slot(object)
    def update_staged(self, staged) -> None:
        self.progress_label.setText("Reiniciando con la versión nueva…")
        update.replace_after_exit(update.installed_app(), Path(staged))
        QApplication.quit()

    @Slot(str)
    def update_failed(self, message: str) -> None:
        self.progress_bar.hide()
        self.progress_label.setText("")
        self.convert_button.setEnabled(bool(self.sources))
        QMessageBox.warning(self, "Conversor", f"No se ha podido actualizar:\n{message}")

    # --- Origen y destino ---

    def _last_folder(self) -> str:
        return str(self.settings.value("last_folder", str(Path.home())))

    def choose_sources(self) -> None:
        patterns = " ".join(f"*{ext}" for ext in supported_extensions())
        filenames, _ = QFileDialog.getOpenFileNames(
            self, "Abrir sesiones (varias .als, o un AAF y un MIDI)", self._last_folder(), f"Sesiones ({patterns})"
        )
        if filenames:
            self.set_sources([Path(f) for f in filenames])

    def choose_output(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Carpeta de destino", self.output_edit.text() or self._last_folder())
        if directory:
            self.output_edit.setText(directory)

    def choose_template(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Set vacío de Ableton", self._last_folder(), "Sets de Ableton (*.als)")
        self.template_edit.setText(filename)

    def is_batch(self) -> bool:
        return len(self.sources) > 1 and all(p.suffix.lower() == ".als" for p in self.sources)

    def set_sources(self, paths: list[Path]) -> None:
        self.sources = paths
        self.settings.setValue("last_folder", str(paths[0].parent))
        if self.is_batch():
            self.source_edit.setText(f"{len(paths)} sesiones: " + ", ".join(p.stem for p in paths))
            self.output_edit.clear()
        else:
            self.source_edit.setText("  +  ".join(p.name for p in paths))
            self.output_edit.setText(str(default_output(paths[0])))
        self.show_button.setEnabled(False)
        self._update_targets()
        self.refresh_preview()

    def _update_targets(self) -> None:
        from_ableton = any(p.suffix.lower() == ".als" for p in self.sources)
        defaults = default_targets(self.sources) if self.sources else ()
        for name, check in self.target_checks.items():
            check.setEnabled(bool(self.sources) and (name != "ableton") == from_ableton)
            check.setChecked(name in defaults)
        self.export_box.setEnabled(bool(self.sources) and from_ableton)
        self.reverse_box.setVisible(bool(self.sources) and not from_ableton)

    def _tempo(self) -> float | None:
        return self.tempo_spin.value() or None

    def refresh_preview(self) -> None:
        if not self.sources:
            return
        try:
            if self.is_batch():
                parts = []
                for path in self.sources:
                    project = load(path)
                    parts.append(f"{path.name}: {len(project.tracks)} pistas, {project.tempo_map[0].bpm:g} BPM")
                text = "Se convertirá cada sesión en su propia carpeta:\n" + "\n".join(parts)
            else:
                project = load(self.sources, self._tempo())
                text = summary(project)
                if project.warnings:
                    text += "\n\nAvisos:\n" + "\n".join(f"  - {w}" for w in project.warnings)
        except Exception as error:  # noqa: BLE001
            self.convert_button.setEnabled(False)
            self.log.setPlainText(f"No se puede leer la sesión:\n{error}")
            return
        self.convert_button.setEnabled(True)
        self.log.setPlainText(text)

    # --- Conversión ---

    def _options(self, targets) -> dict:
        self.settings.setValue("rate_index", self.rate_combo.currentIndex())
        self.settings.setValue("skip_muted", "true" if self.skip_muted_check.isChecked() else "false")
        self.settings.setValue("group_prefix", "true" if self.group_check.isChecked() else "false")
        return dict(
            targets=targets,
            template=self.template_edit.text() or None,
            tempo=self._tempo(),
            sample_rate=self.rate_combo.currentData(),
            skip_muted=self.skip_muted_check.isChecked(),
            group_prefix=self.group_check.isChecked(),
        )

    def start_conversion(self) -> None:
        targets = tuple(
            name for name, check in self.target_checks.items() if check.isEnabled() and check.isChecked()
        )
        if not targets:
            QMessageBox.warning(self, "Conversor", "Elige al menos un formato de salida.")
            return
        options = self._options(targets)
        output_text = self.output_edit.text().strip()
        sources = list(self.sources)
        if self.is_batch():
            root = Path(output_text) if output_text else None
            self._output_dir = root or sources[0].parent
            job = lambda progress: convert_many(sources, root, progress=progress, **options)  # noqa: E731
        else:
            output = Path(output_text) if output_text else default_output(sources[0])
            self._output_dir = output
            job = lambda progress: convert(sources, output, progress=progress, **options)  # noqa: E731

        self.convert_button.setEnabled(False)
        self.show_button.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.progress_label.setText("Empezando…")
        self.log.setPlainText("Convirtiendo…")

        self._thread = QThread(self)
        worker = _Worker(job)
        worker.moveToThread(self._thread)
        self._thread.started.connect(worker.run)
        # Métodos de la ventana (no lambdas) para que Qt los ejecute en el hilo principal.
        worker.progress.connect(self.conversion_progress)
        worker.finished.connect(self.conversion_done)
        worker.failed.connect(self.conversion_failed)
        for signal in (worker.finished, worker.failed):
            signal.connect(self._thread.quit)
            signal.connect(worker.deleteLater)
        self._worker = worker
        self._thread.start()

    @Slot(float, str)
    def conversion_progress(self, fraction: float, text: str) -> None:
        self.progress_bar.setValue(int(fraction * 1000))
        self.progress_label.setText(text)

    @Slot(object)
    def conversion_done(self, result) -> None:
        if isinstance(result, list):
            lines = []
            for session, outcome in result:
                if isinstance(outcome, Exception):
                    lines.append(f"✗ {session.name}: {outcome}")
                else:
                    warnings = len(outcome.project.warnings)
                    lines.append(
                        f"✓ {session.name}: {len(outcome.project.tracks)} pistas"
                        + (f", {warnings} avisos (ver el informe)" if warnings else "")
                    )
            self.log.setPlainText("Conversión terminada:\n" + "\n".join(lines))
        else:
            self.log.setPlainText(build_report(result))
        self.progress_bar.hide()
        self.progress_label.setText("Terminado")
        self.convert_button.setEnabled(True)
        self.show_button.setEnabled(True)
        self._offer_pending_update()

    def _offer_pending_update(self) -> None:
        if self._pending_update is not None:
            available, self._pending_update = self._pending_update, None
            QTimer.singleShot(0, lambda: self.offer_update(available))

    @Slot(str)
    def conversion_failed(self, message: str) -> None:
        self.progress_bar.hide()
        self.progress_label.setText("")
        self.log.setPlainText(f"Error durante la conversión:\n{message}")
        self.convert_button.setEnabled(True)
        self._offer_pending_update()

    def open_output(self) -> None:
        if self._output_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._output_dir)))


class Application(QApplication):
    """Recibe los archivos que se sueltan sobre el icono del Dock o se abren con «Abrir con»."""

    def __init__(self, argv):
        super().__init__(argv)
        self.window: MainWindow | None = None
        self._pending: list[Path] = []
        # macOS envía un evento por archivo: se agrupan los que llegan juntos.
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._open_pending)

    def event(self, event) -> bool:
        if event.type() == QEvent.FileOpen:
            path = Path(event.file())
            if _is_supported(path):
                self._pending.append(path)
                self._timer.start()
            return True
        return super().event(event)

    def _open_pending(self) -> None:
        if self.window and self._pending:
            self.window.set_sources(self._pending)
            self.window.raise_()
            self.window.activateWindow()
        self._pending = []


def main() -> int:
    app = Application(sys.argv)
    app.setApplicationName("Conversor")
    window = MainWindow()
    app.window = window
    paths = [Path(a) for a in sys.argv[1:] if _is_supported(Path(a))]
    if paths:
        window.set_sources(paths)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
