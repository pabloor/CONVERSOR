"""Autoprueba: convierte una sesión de Ableton generada al momento.

Sirve para comprobar que una instalación o la aplicación empaquetada tienen
todo lo necesario (plantilla, bibliotecas de audio, Rubber Band, interfaz).
Uso: `python -m conversor.selftest` o `Conversor.app/Contents/MacOS/Conversor --autoprueba`.
"""

from __future__ import annotations

import gzip
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

_SET = """<?xml version="1.0" encoding="UTF-8"?>
<Ableton MajorVersion="5" MinorVersion="11.0_433">
 <LiveSet>
  <Tracks>
   <AudioTrack Id="1">
    <Name><EffectiveName Value="Loop"/><UserName Value=""/></Name>
    <DeviceChain>
     <Mixer><Volume><Manual Value="0.5"/><AutomationTarget Id="2"/></Volume><Pan><Manual Value="0"/></Pan></Mixer>
     <MainSequencer><Sample><ArrangerAutomation><Events>
      <AudioClip Id="0" Time="0">
       <CurrentStart Value="0"/><CurrentEnd Value="4"/>
       <Loop><LoopStart Value="0"/><LoopEnd Value="8"/><StartRelative Value="0"/><LoopOn Value="false"/></Loop>
       <Name Value="loop"/>
       <SampleRef><FileRef><RelativePath Value="loop.flac"/></FileRef></SampleRef>
       <WarpMarkers><WarpMarker Id="0" BeatTime="0" SecTime="0"/><WarpMarker Id="1" BeatTime="1" SecTime="0.6"/></WarpMarkers>
       <IsWarped Value="true"/><WarpMode Value="4"/>
      </AudioClip>
     </Events></ArrangerAutomation></Sample></MainSequencer>
    </DeviceChain>
   </AudioTrack>
   <MidiTrack Id="3">
    <Name><EffectiveName Value="Piano"/><UserName Value=""/></Name>
    <DeviceChain>
     <Mixer><Volume><Manual Value="1"/></Volume><Pan><Manual Value="0"/></Pan></Mixer>
     <MainSequencer><ClipTimeable><ArrangerAutomation><Events>
      <MidiClip Id="0" Time="0">
       <CurrentStart Value="0"/><CurrentEnd Value="4"/>
       <Loop><LoopStart Value="0"/><LoopEnd Value="4"/><StartRelative Value="0"/><LoopOn Value="false"/></Loop>
       <Name Value="acordes"/>
       <Notes><KeyTracks><KeyTrack Id="0">
        <Notes><MidiNoteEvent Time="0" Duration="2" Velocity="100" IsEnabled="true"/></Notes>
        <MidiKey Value="60"/>
       </KeyTrack></KeyTracks></Notes>
      </MidiClip>
     </Events></ArrangerAutomation></ClipTimeable></MainSequencer>
    </DeviceChain>
   </MidiTrack>
  </Tracks>
  <MasterTrack><DeviceChain><Mixer>
   <Tempo><Manual Value="120"/><AutomationTarget Id="8"/></Tempo>
   <TimeSignature><Manual Value="201"/><AutomationTarget Id="9"/></TimeSignature>
  </Mixer></DeviceChain></MasterTrack>
 </LiveSet>
</Ableton>
"""


def run(with_gui: bool = True) -> list[str]:
    """Devuelve la lista de problemas encontrados (vacía si todo va bien)."""
    from .audio import pylibrb
    from .convert import convert
    from .formats import aaf

    problems = []
    with tempfile.TemporaryDirectory(prefix="conversor-autoprueba-") as tmp:
        folder = Path(tmp)
        # Un loop a 100 BPM (0,6 s por tiempo) en FLAC a 44,1 kHz, en una sesión a 120 BPM.
        rate = 44100
        audio = np.zeros((3 * rate, 2), dtype=np.float32)
        for k in range(5):
            audio[int(k * 0.6 * rate) : int(k * 0.6 * rate) + 64] = 0.8
        sf.write(folder / "loop.flac", audio, rate)
        (folder / "Prueba.als").write_bytes(gzip.compress(_SET.encode()))

        result = convert(folder / "Prueba.als", folder / "salida")
        names = sorted(p.name for p in result.outputs)
        expected = ["Prueba - informe.txt", "Prueba - pistas", "Prueba.aaf", "Prueba.mid"]
        if names != expected:
            problems.append(f"archivos generados: {names}")
        if pylibrb is None:
            problems.append("Rubber Band (pylibrb) no está disponible: el warp no se procesará")
        elif result.rendered_clips != 1:
            problems.append(f"clips procesados con warp: {result.rendered_clips} (se esperaba 1)")
        tracks = aaf.read(folder / "salida" / "Prueba.aaf").tracks
        if [t.name for t in tracks] != ["Loop"] or abs(tracks[0].volume_db + 6.02) > 0.05:
            problems.append("el AAF no contiene la pista con su volumen")

        stems = list((folder / "salida" / "Prueba - pistas").glob("*.wav"))
        if len(stems) != 1 or abs(sf.info(str(stems[0])).duration - 2.0) > 0.001:
            problems.append("la pista en WAV no dura lo esperado (2 s)")

        # Conversión inversa: comprueba que la plantilla de Ableton está incluida.
        back = convert([folder / "salida" / "Prueba.aaf", folder / "salida" / "Prueba.mid"], folder / "vuelta")
        if not any(p.suffix == ".als" for p in back.outputs):
            problems.append("no se ha generado el set de Ableton")

    if with_gui:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from .gui import MainWindow

        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.show()
        app.processEvents()
        window.close()
    return problems


def install_test(dmg: Path) -> list[str]:
    """Instala el .dmg sobre una versión anterior falsa, como haría el actualizador (solo macOS)."""
    import plistlib
    import subprocess

    from . import __version__, update

    with tempfile.TemporaryDirectory(prefix="conversor-instalacion-") as tmp:
        app = Path(tmp) / "Aplicaciones" / "Conversor.app"
        (app / "Contents").mkdir(parents=True)
        with open(app / "Contents" / "Info.plist", "wb") as f:
            plistlib.dump({"CFBundleIdentifier": update.BUNDLE_ID, "CFBundleShortVersionString": "0.0.1"}, f)
        staged = update.stage(dmg, app, __version__)
        finished = subprocess.Popen(["true"])
        finished.wait()
        update.replace_after_exit(app, staged, pid=finished.pid, relaunch=False).wait(timeout=60)
        update.check_app(app, __version__)
        if not (app / "Contents" / "MacOS" / "Conversor").exists():
            return ["la aplicación instalada no tiene ejecutable"]
        leftovers = sorted(p.name for p in app.parent.iterdir())
        if leftovers != ["Conversor.app"]:
            return [f"quedan restos de la instalación: {leftovers}"]
    return []


def main() -> int:
    if "--instalacion" in sys.argv:
        dmg = Path(sys.argv[sys.argv.index("--instalacion") + 1])
        try:
            problems = install_test(dmg)
        except Exception as error:  # noqa: BLE001
            print(f"Prueba de instalación: ERROR {type(error).__name__}: {error}")
            return 1
        print("Prueba de instalación: " + ("todo correcto" if not problems else "; ".join(problems)))
        return 1 if problems else 0
    try:
        problems = run(with_gui="--sin-interfaz" not in sys.argv)
    except Exception as error:  # noqa: BLE001 - se informa del fallo
        print(f"Autoprueba: ERROR {type(error).__name__}: {error}")
        return 1
    if problems:
        print("Autoprueba: problemas encontrados")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("Autoprueba: todo correcto")
    return 0


if __name__ == "__main__":
    sys.exit(main())
