"""La ventana, sin pantalla (plataforma offscreen de Qt)."""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtGui import QFileOpenEvent  # noqa: E402

from conversor.gui import Application, MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = Application.instance() or Application([])
    yield instance


@pytest.fixture
def window(app, tmp_path):
    # Ajustes en un archivo temporal: no tocan los del usuario ni pasan de una prueba a otra.
    window = MainWindow(QSettings(str(tmp_path / "ajustes.ini"), QSettings.IniFormat))
    app.window = window
    yield window
    window.close()


def wait(app, condition, timeout=60):
    end = time.time() + timeout
    while not condition():
        app.processEvents()
        time.sleep(0.01)
        assert time.time() < end, "la conversión no ha terminado a tiempo"


def test_converts_with_progress(app, window, als_session):
    window.set_sources([als_session])
    assert window.convert_button.isEnabled()
    seen = []
    window.progress_bar.valueChanged.connect(seen.append)
    window.start_conversion()
    wait(app, lambda: window.show_button.isEnabled())
    output = als_session.parent / "Demo (convertido)"
    assert (output / "Demo.aaf").exists() and (output / "Demo - pistas").is_dir()
    assert seen and max(seen) == 1000
    assert "Cómo abrirlo en Logic Pro" in window.log.toPlainText()


def test_batch_conversion(app, window, als_session):
    other = als_session.with_name("Otra.als")
    other.write_bytes(als_session.read_bytes())
    window.set_sources([als_session, other])
    assert window.is_batch() and window.output_edit.text() == ""
    window.target_checks["aaf"].setChecked(False)
    window.target_checks["stems"].setChecked(False)
    window.start_conversion()
    wait(app, lambda: window.show_button.isEnabled())
    assert (als_session.parent / "Demo (convertido)" / "Demo.mid").exists()
    assert (als_session.parent / "Otra (convertido)" / "Otra.mid").exists()
    assert "✓ Otra.als" in window.log.toPlainText()


def test_files_dropped_on_dock_icon(app, window, als_session):
    app.event(QFileOpenEvent(str(als_session)))
    app.event(QFileOpenEvent(str(als_session.with_suffix(".txt"))))  # se ignora
    wait(app, lambda: window.sources == [als_session], timeout=5)
