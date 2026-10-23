"""Actualizaciones automáticas."""

import hashlib
import io
import json
import os
import plistlib
import subprocess

import pytest

from conversor import update

DMG = b"contenido del dmg de prueba"


def release(version="9.0.0", digest=True):
    asset = {
        "name": "Conversor.dmg",
        "browser_download_url": "https://example.invalid/Conversor.dmg",
        "size": len(DMG),
    }
    if digest:
        asset["digest"] = "sha256:" + hashlib.sha256(DMG).hexdigest()
    return {
        "tag_name": f"v{version}",
        "body": "- Novedad",
        "html_url": "https://github.com/pabloor/CONVERSOR/releases/tag/v" + version,
        "assets": [asset],
    }


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def opener_for(payload):
    def opener(request, timeout):
        if isinstance(payload, Exception):
            raise payload
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return Response(data)

    return opener


def test_parse_version():
    assert update.parse_version("v0.10.2") == (0, 10, 2)
    assert update.parse_version("0.2.0") < update.parse_version("0.10.0")


def test_check_finds_newer_version():
    found = update.check("0.2.0", opener=opener_for(release("0.3.0")))
    assert found.version == "0.3.0" and found.notes == "- Novedad"
    assert found.sha256 == hashlib.sha256(DMG).hexdigest()
    assert update.check("0.3.0", opener=opener_for(release("0.3.0"))) is None
    assert update.check("0.4.0", opener=opener_for(release("0.3.0"))) is None


def test_check_ignores_releases_without_checksum():
    assert update.check("0.2.0", opener=opener_for(release("0.3.0", digest=False))) is None


def test_check_reports_network_errors():
    with pytest.raises(update.UpdateError, match="No se ha podido consultar GitHub"):
        update.check("0.2.0", opener=opener_for(OSError("sin conexión")))


def test_download_verifies_checksum(tmp_path):
    available = update.check("0.2.0", opener=opener_for(release("0.3.0")))
    seen = []
    path = update.download(available, tmp_path, seen.append, opener=opener_for(DMG))
    assert path.read_bytes() == DMG and seen[-1] == 1.0

    with pytest.raises(update.UpdateError, match="no coincide"):
        update.download(available, tmp_path, opener=opener_for(b"otro contenido"))
    assert not (tmp_path / "Conversor.dmg").exists()


def fake_app(path, version, bundle_id=update.BUNDLE_ID):
    (path / "Contents").mkdir(parents=True)
    with open(path / "Contents" / "Info.plist", "wb") as f:
        plistlib.dump({"CFBundleIdentifier": bundle_id, "CFBundleShortVersionString": version}, f)
    (path / "version.txt").write_text(version)
    return path


def test_check_app(tmp_path):
    good = fake_app(tmp_path / "a" / "Conversor.app", "0.3.0")
    update.check_app(good, "0.3.0")
    with pytest.raises(update.UpdateError, match="versión 0.3.0, no la 0.4.0"):
        update.check_app(good, "0.4.0")
    other = fake_app(tmp_path / "b" / "Conversor.app", "0.3.0", bundle_id="com.otra.app")
    with pytest.raises(update.UpdateError, match="otra aplicación"):
        update.check_app(other, "0.3.0")


def test_replace_script_swaps_the_app(tmp_path):
    app = fake_app(tmp_path / "Conversor.app", "0.2.0")
    staged = fake_app(tmp_path / ".Conversor-0.3.0.app", "0.3.0")
    finished = subprocess.Popen(["true"])
    finished.wait()
    update.replace_after_exit(app, staged, pid=finished.pid, relaunch=False).wait(timeout=10)
    assert (app / "version.txt").read_text() == "0.3.0"
    assert not staged.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Conversor.app"]


def test_installed_app_only_in_packaged_mac_app():
    assert update.installed_app() is None


# --- Ventana ---

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def window(tmp_path):
    from PySide6.QtCore import QSettings

    from conversor.gui import Application, MainWindow

    app = Application.instance() or Application([])
    # Ajustes en un archivo temporal: no tocan los del usuario ni pasan de una prueba a otra.
    window = MainWindow(QSettings(str(tmp_path / "ajustes.ini"), QSettings.IniFormat))
    offered = []
    window.offer_update = offered.append
    window.offered = offered
    yield window
    window.close()
    del app


def test_window_respects_skipped_version(window):
    available = update.check("0.2.0", opener=opener_for(release("0.3.0")))
    window._manual_check = False
    window.settings.setValue("skipped_version", "0.3.0")
    window.update_checked(available)
    assert window.offered == []
    # Al buscar a mano sí se ofrece.
    window._manual_check = True
    window.update_checked(available)
    assert window.offered == [available]


def test_window_waits_for_conversion_to_finish(window):
    available = update.check("0.2.0", opener=opener_for(release("0.3.0")))

    class Running:
        def isRunning(self):
            return True

    window._manual_check = False
    window._thread = Running()
    window.update_checked(available)
    assert window.offered == [] and window._pending_update is available
