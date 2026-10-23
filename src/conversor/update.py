"""Actualizaciones automáticas desde GitHub Releases (aplicación para Mac).

1. `check()` consulta la última versión publicada en GitHub.
2. `download()` descarga el .dmg y comprueba su SHA-256 con el que publica GitHub.
3. `stage()` monta el .dmg, comprueba que contiene Conversor con la versión
   esperada y lo copia junto a la aplicación instalada.
4. `replace_after_exit()` lanza un pequeño script que espera a que la
   aplicación se cierre, cambia la versión instalada por la nueva (restaurando
   la anterior si algo falla) y la vuelve a abrir.

Solo funciona en la aplicación empaquetada para macOS; desde el código fuente,
`installed_app()` devuelve None y la interfaz no ofrece actualizar.
"""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import __version__

REPOSITORY = "pabloor/CONVERSOR"
LATEST_RELEASE = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPOSITORY}/releases/latest"
ASSET_NAME = "Conversor.dmg"
BUNDLE_ID = "com.pabloor.conversor"
_TIMEOUT = 10
_CHUNK = 1 << 20


class UpdateError(Exception):
    pass


def _ssl_context() -> ssl.SSLContext:
    """Certificados con los que comprobar la conexión con GitHub.

    El Python incluido en la aplicación no ve los certificados de macOS, así
    que se usan los del sistema mediante truststore (como Safari, también
    detrás de un proxy de empresa) y, si no está disponible, los de certifi.
    """
    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:  # noqa: BLE001 - se recurre a certifi
        import certifi

        return ssl.create_default_context(cafile=certifi.where())


def urlopen(request, timeout):
    return urllib.request.urlopen(request, timeout=timeout, context=_ssl_context())


def check_connection() -> None:
    """Comprueba que se puede abrir una conexión segura con GitHub (para la autoprueba)."""
    request = urllib.request.Request("https://api.github.com/", headers={"User-Agent": f"Conversor/{__version__}"})
    try:
        with urlopen(request, timeout=_TIMEOUT):
            pass
    except urllib.error.HTTPError:
        pass  # GitHub ha respondido (p. ej. límite de consultas): la conexión segura funciona.


@dataclass
class Update:
    version: str
    notes: str
    url: str
    sha256: str
    size: int
    page: str


def parse_version(text: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", text)
    return tuple(int(n) for n in numbers[:3]) if numbers else (0,)


def check(current: str = __version__, opener=urlopen) -> Update | None:
    """Devuelve la actualización disponible, o None si ya está la última versión."""
    request = urllib.request.Request(
        LATEST_RELEASE,
        headers={"Accept": "application/vnd.github+json", "User-Agent": f"Conversor/{current}"},
    )
    try:
        with opener(request, timeout=_TIMEOUT) as response:
            release = json.load(response)
    except (OSError, ValueError) as error:
        raise UpdateError(f"No se ha podido consultar GitHub: {error}") from error
    version = release.get("tag_name", "").lstrip("v")
    if parse_version(version) <= parse_version(current):
        return None
    asset = next((a for a in release.get("assets", []) if a.get("name") == ASSET_NAME), None)
    if asset is None:
        return None
    digest = asset.get("digest") or ""
    if not digest.startswith("sha256:"):
        # Sin huella no se puede comprobar la descarga: mejor no actualizar.
        return None
    return Update(
        version=version,
        notes=(release.get("body") or "").strip(),
        url=asset["browser_download_url"],
        sha256=digest.split(":", 1)[1].lower(),
        size=int(asset.get("size") or 0),
        page=release.get("html_url") or RELEASES_PAGE,
    )


def download(update: Update, folder: Path, progress=None, opener=urlopen) -> Path:
    """Descarga el .dmg y comprueba su SHA-256. `progress(fracción)` informa del avance."""
    target = Path(folder) / ASSET_NAME
    digest = hashlib.sha256()
    received = 0
    request = urllib.request.Request(update.url, headers={"User-Agent": f"Conversor/{__version__}"})
    try:
        with opener(request, timeout=_TIMEOUT) as response, open(target, "wb") as out:
            while chunk := response.read(_CHUNK):
                out.write(chunk)
                digest.update(chunk)
                received += len(chunk)
                if progress and update.size:
                    progress(min(received / update.size, 1.0))
    except OSError as error:
        raise UpdateError(f"No se ha podido descargar la actualización: {error}") from error
    if digest.hexdigest() != update.sha256:
        target.unlink(missing_ok=True)
        raise UpdateError("La descarga no coincide con la publicada en GitHub; no se instala.")
    return target


def installed_app() -> Path | None:
    """Ruta de Conversor.app si se está ejecutando la aplicación empaquetada de macOS."""
    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        return None
    app = Path(sys.executable).resolve().parents[2]
    return app if app.suffix == ".app" else None


def can_install(app: Path | None) -> bool:
    return app is not None and os.access(app.parent, os.W_OK)


def app_info(app: Path) -> dict:
    with open(app / "Contents" / "Info.plist", "rb") as f:
        return plistlib.load(f)


def stage(dmg: Path, app: Path, expected_version: str) -> Path:
    """Copia la aplicación del .dmg junto a la instalada y devuelve su ruta."""
    mountpoint = Path(tempfile.mkdtemp(prefix="conversor-dmg-"))
    staged = app.with_name(f".{app.stem}-{expected_version}.app")
    try:
        _run(["hdiutil", "attach", "-nobrowse", "-readonly", "-noautoopen", "-mountpoint", str(mountpoint), str(dmg)])
        try:
            new_app = mountpoint / "Conversor.app"
            check_app(new_app, expected_version)
            if staged.exists():
                shutil.rmtree(staged)
            # ditto conserva la firma, los enlaces y los atributos de la aplicación.
            _run(["ditto", str(new_app), str(staged)])
        finally:
            _run(["hdiutil", "detach", "-quiet", str(mountpoint)], check=False)
    finally:
        shutil.rmtree(mountpoint, ignore_errors=True)
    return staged


def check_app(app: Path, expected_version: str) -> None:
    if not (app / "Contents" / "Info.plist").exists():
        raise UpdateError("La descarga no contiene la aplicación Conversor.")
    info = app_info(app)
    if info.get("CFBundleIdentifier") != BUNDLE_ID:
        raise UpdateError("La descarga contiene otra aplicación; no se instala.")
    if info.get("CFBundleShortVersionString") != expected_version:
        raise UpdateError(
            f"La descarga es la versión {info.get('CFBundleShortVersionString')}, "
            f"no la {expected_version}; no se instala."
        )


REPLACE_SCRIPT = """#!/bin/bash
# Sustituye Conversor.app por la versión nueva cuando se cierra la actual.
app="$1"; staged="$2"; pid="$3"; relaunch="$4"
while kill -0 "$pid" 2>/dev/null; do sleep 0.2; done
backup="${app%.app}.anterior.app"
rm -rf "$backup"
if mv "$app" "$backup"; then
    if mv "$staged" "$app"; then
        rm -rf "$backup"
    else
        mv "$backup" "$app"
    fi
fi
rm -rf "$staged"
if [ "$relaunch" = "1" ]; then open "$app"; fi
"""


def replace_after_exit(app: Path, staged: Path, pid: int | None = None, relaunch: bool = True) -> subprocess.Popen:
    """Lanza el script que cambia la aplicación cuando termina el proceso `pid`."""
    script = Path(tempfile.mkdtemp(prefix="conversor-update-")) / "actualizar.sh"
    script.write_text(REPLACE_SCRIPT)
    return subprocess.Popen(
        ["/bin/bash", str(script), str(app), str(staged), str(pid or os.getpid()), "1" if relaunch else "0"],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _run(command: list[str], check: bool = True) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise UpdateError(f"Error al ejecutar {command[0]}: {result.stderr.strip() or result.returncode}")
