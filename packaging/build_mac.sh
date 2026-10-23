#!/bin/bash
# Construye Conversor.app y Conversor.dmg en dist/.
# Se ejecuta en un Mac (lo usa también GitHub Actions) desde un entorno de
# Python con el proyecto instalado:
#   python3 -m venv .venv && source .venv/bin/activate
#   pip install -e . "pyinstaller>=6.10"
#   packaging/build_mac.sh
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=$(python -c "import conversor; print(conversor.__version__)")

extra=()
# Rubber Band no existe para Mac con Intel; si está instalado, se incluye entero.
if python -c "import pylibrb" 2>/dev/null; then
    extra+=(--collect-all pylibrb)
fi

pyinstaller --noconfirm --clean --windowed \
    --name Conversor \
    --osx-bundle-identifier com.pabloor.conversor \
    --collect-data conversor \
    --collect-all aaf2 \
    --collect-all soundfile \
    --collect-all soxr \
    "${extra[@]}" \
    packaging/conversor_app.py

if [[ "$(uname)" == "Darwin" ]]; then
    plist=dist/Conversor.app/Contents/Info.plist
    # -replace crea la clave si no existe.
    plutil -replace CFBundleShortVersionString -string "$VERSION" "$plist"
    plutil -replace CFBundleVersion -string "$VERSION" "$plist"
    # Tipos de archivo que abre: permite soltar sesiones sobre el icono del Dock
    # y usar «Abrir con > Conversor» en el Finder.
    plutil -replace CFBundleDocumentTypes -json '[
        {"CFBundleTypeName": "Sesión de Ableton Live", "CFBundleTypeRole": "Viewer",
         "LSHandlerRank": "Alternate", "CFBundleTypeExtensions": ["als"]},
        {"CFBundleTypeName": "AAF", "CFBundleTypeRole": "Viewer",
         "LSHandlerRank": "Alternate", "CFBundleTypeExtensions": ["aaf"]},
        {"CFBundleTypeName": "Archivo MIDI", "CFBundleTypeRole": "Viewer",
         "LSHandlerRank": "Alternate", "CFBundleTypeExtensions": ["mid", "midi"]}
    ]' "$plist"
    # Firma ad hoc (sin cuenta de desarrollador de Apple) tras editar el Info.plist.
    codesign --force --deep --sign - dist/Conversor.app

    rm -rf dist/dmg dist/Conversor.dmg
    mkdir dist/dmg
    cp -R dist/Conversor.app dist/dmg/
    ln -s /Applications dist/dmg/Aplicaciones
    hdiutil create -volname "Conversor $VERSION" -srcfolder dist/dmg -ov -format UDZO dist/Conversor.dmg
    rm -rf dist/dmg
    echo "Listo: dist/Conversor.dmg"
fi
