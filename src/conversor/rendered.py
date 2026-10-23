"""Pistas renderizadas en Ableton, con todos sus efectos.

El conversor no puede ejecutar los dispositivos de Ableton ni reproducir la
mezcla, así que el audio procesado lo genera el propio Ableton con *Archivo >
Exportar audio/vídeo > Pistas renderizadas: todas las pistas individuales*,
empezando en el compás 1. Aquí:

1. Se relaciona cada archivo exportado con su pista por el nombre.
2. Cada pista de audio o MIDI se sustituye por su versión procesada, cortada en
   los mismos clips que en Ableton. Cada clip se alarga hasta el siguiente (o
   hasta que se apaga el sonido) para no cortar colas de reverb o delay.
3. Los retornos entran como pistas propias (son una señal aparte de sus pistas).
4. El master y los grupos solo se copian como referencia: ya contienen el
   audio de sus pistas y sonarían dos veces.
5. Opcionalmente se mantiene la versión limpia de cada pista de audio, con el
   volumen al mínimo, por si se quiere rehacer la mezcla en el otro DAW.

Se supone que la exportación individual de Ableton incluye el volumen y el
panorama de cada pista, así que las pistas procesadas van a 0 dB y al centro.
"""

from __future__ import annotations

import math
import re
import shutil
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import soundfile as sf

from .model import AudioClip, Project, Track, TrackKind

AUDIO_EXTENSIONS = {".wav", ".aif", ".aiff", ".flac"}
# Por debajo de este nivel (dBFS) se considera que el sonido se ha apagado.
_SILENCE_DB = -70.0
_WINDOW = 0.01  # segundos
CLEAN_SUFFIX = " (limpio)"
AUDIO_SUFFIX = " (audio)"


@dataclass
class RenderedSet:
    folder: Path
    matched: dict[str, Path] = field(default_factory=dict)  # nombre de pista -> archivo
    master: Path | None = None
    unmatched: list[Path] = field(default_factory=list)
    references: list[Path] = field(default_factory=list)  # copiados a la carpeta de referencia


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return " ".join(re.findall(r"[a-z0-9]+", text))


def match(folder: Path, project: Project) -> RenderedSet:
    """Relaciona los archivos de la carpeta con las pistas del proyecto por su nombre.

    Ableton antepone al nombre de la pista el nombre que se escribe al exportar
    (normalmente el del set). Un archivo encaja con una pista si su nombre,
    sin acentos, mayúsculas ni signos, es el de la pista o termina en él; si
    encaja con varias, gana la de nombre más largo («Bajo 2» frente a «Bajo»).
    """
    result = RenderedSet(Path(folder))
    files = sorted(p for p in Path(folder).iterdir() if p.suffix.lower() in AUDIO_EXTENSIONS)
    names = {t.name: normalize(t.name) for t in project.tracks}
    for file in files:
        stem = normalize(file.stem)
        best = None
        for name, key in names.items():
            if key and name not in result.matched and (stem == key or stem.endswith(" " + key)):
                if best is None or len(key) > len(names[best]):
                    best = name
        if best is not None:
            result.matched[best] = file
        elif stem.split(" ")[-1] in ("master", "main") and result.master is None:
            result.master = file
        else:
            result.unmatched.append(file)
    return result


def apply(project: Project, rendered: RenderedSet, keep_clean: bool = True, start_beat: float = 0.0) -> None:
    """Sustituye las pistas por sus versiones procesadas (ver la descripción del módulo)."""
    start_seconds = project.beats_to_seconds(start_beat)
    processed: list[Track] = []
    clean: list[Track] = []
    for track in project.tracks:
        file = rendered.matched.get(track.name)
        if track.kind == TrackKind.GROUP:
            # Se mantiene para poner su nombre delante del de sus pistas; no se exporta.
            processed.append(track)
            if file is not None:
                rendered.references.append(file)
                if track.devices:
                    project.warn(
                        f"El grupo '{track.name}' tiene efectos ({', '.join(track.devices)}), que no "
                        "están en las pistas procesadas de sus miembros. Su versión procesada va en la "
                        "carpeta de referencias."
                    )
            continue
        if file is None:
            if track.kind in (TrackKind.AUDIO, TrackKind.MIDI, TrackKind.RETURN) and _has_content(track):
                project.warn(f"No hay versión procesada de la pista '{track.name}': se exporta en limpio.")
            processed.append(track)
            continue
        audio_track = _processed_track(project, track, file, start_seconds)
        if audio_track is not None:
            processed.append(audio_track)
        if track.kind == TrackKind.MIDI:
            # Las notas se siguen exportando en el MIDI.
            processed.append(track)
        elif track.kind == TrackKind.AUDIO and keep_clean:
            track.name += CLEAN_SUFFIX
            track.volume_db = -math.inf
            track.automation = [a for a in track.automation if a.parameter != "volume"]
            clean.append(track)
    if rendered.master is not None:
        rendered.references.append(rendered.master)
    if clean:
        project.warn(
            f"Las pistas «{CLEAN_SUFFIX.strip()}» van al final con el volumen al mínimo, para rehacer la "
            "mezcla si hace falta. Si en el otro DAW suenan junto a las procesadas, silencíalas o bórralas."
        )
    project.tracks = processed + clean


def copy_references(rendered: RenderedSet, folder: Path) -> list[Path]:
    copied = []
    if rendered.references:
        folder.mkdir(parents=True, exist_ok=True)
        for file in rendered.references:
            target = folder / file.name
            shutil.copy2(file, target)
            copied.append(target)
    return copied


def _has_content(track: Track) -> bool:
    return bool(track.audio_clips or track.midi_clips or track.kind == TrackKind.RETURN)


def _processed_track(project: Project, track: Track, file: Path, start_seconds: float) -> Track | None:
    """Pista de audio con el archivo procesado, cortado en los clips de la pista original."""
    data, rate = sf.read(str(file), dtype="float32", always_2d=True)
    duration = len(data) / rate
    active = _active_windows(data, rate)
    if not active.any():
        project.warn(f"La versión procesada de '{track.name}' está en silencio; no se incluye.")
        return None

    def last_sound(t0: float, t1: float) -> float:
        """Último instante con sonido en [t0, t1) (segundos del archivo), o t0 si no hay."""
        a, b = int(t0 / _WINDOW), min(int(math.ceil(t1 / _WINDOW)), len(active))
        indices = np.flatnonzero(active[a:b])
        return t0 if not len(indices) else min((a + indices[-1] + 1) * _WINDOW, t1)

    clips = [(c.start, c.start + c.length, c.name) for c in track.audio_clips] + [
        (c.start, c.start + c.length, c.name) for c in track.midi_clips
    ]
    clips.sort()
    spans: list[tuple[float, float, str]] = []
    if clips:
        for index, (begin, end, name) in enumerate(clips):
            t0 = project.beats_to_seconds(begin) - start_seconds
            t1 = project.beats_to_seconds(end) - start_seconds
            following = (
                project.beats_to_seconds(clips[index + 1][0]) - start_seconds if index + 1 < len(clips) else duration
            )
            # La cola (reverb, delay) sigue hasta el siguiente clip mientras suene.
            t1 = max(t1, last_sound(t1, following)) if following > t1 else t1
            if spans and t0 < spans[-1][1]:
                t0 = spans[-1][1]
            if t1 > t0 and t0 < duration:
                spans.append((max(t0, 0.0), min(t1, duration), name))
    else:
        # Retornos: todo el tramo con sonido.
        indices = np.flatnonzero(active)
        spans.append((indices[0] * _WINDOW, min((indices[-1] + 1) * _WINDOW, duration), track.name))

    name = track.name + AUDIO_SUFFIX if track.kind == TrackKind.MIDI else track.name
    result = Track(name=name, kind=TrackKind.AUDIO, group=track.group, devices=track.devices, mute=track.mute)
    for t0, t1, name in spans:
        begin = project.seconds_to_beats(t0 + start_seconds)
        end = project.seconds_to_beats(t1 + start_seconds)
        result.audio_clips.append(AudioClip(name=name or track.name, start=begin, length=end - begin, file=file, source_offset=t0))
    return result


def _active_windows(data: np.ndarray, rate: int) -> np.ndarray:
    """Para cada ventana de 10 ms, si el pico supera el umbral de silencio."""
    window = max(int(rate * _WINDOW), 1)
    count = math.ceil(len(data) / window)
    padded = np.zeros((count * window, data.shape[1]), dtype=np.float32)
    padded[: len(data)] = data
    peaks = np.abs(padded).reshape(count, window * data.shape[1]).max(axis=1)
    return peaks > 10 ** (_SILENCE_DB / 20)
