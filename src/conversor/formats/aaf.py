"""Escritor AAF para las pistas de audio.

Pro Tools, Logic Pro y Cubase/Nuendo importan AAF con las pistas, los clips en
su posición y el audio incrustado. El audio se incrusta en el propio .aaf, así
que el archivo resultante se puede mover a otro equipo sin perder medios.

Limitaciones de esta versión: solo se incrustan archivos WAV PCM; la mezcla
(volumen, panorama) y la automatización no se escriben en el AAF.
"""

from __future__ import annotations

import wave
from fractions import Fraction
from pathlib import Path

import aaf2

from ..model import Project, TrackKind

DEFAULT_SAMPLE_RATE = 48000


def write(project: Project, path: str | Path) -> int:
    """Escribe las pistas de audio del proyecto. Devuelve el número de clips escritos."""
    sources = _usable_sources(project)
    rate = _session_rate(sources.values())
    for file, file_rate in sources.items():
        if file_rate != rate:
            project.warn(
                f"'{file.name}' está a {file_rate} Hz y la sesión a {rate} Hz; "
                "conviértelo a la misma frecuencia o sonará a otra velocidad."
            )

    clip_count = 0
    with aaf2.open(str(path), "w") as f:
        composition = f.create.CompositionMob(project.name)
        composition.usage = "Usage_TopLevel"
        f.content.mobs.append(composition)
        master_mobs: dict[Path, tuple[object, int]] = {}
        audio_tracks = [t for t in project.tracks if t.kind == TrackKind.AUDIO]

        for number, track in enumerate(audio_tracks, start=1):
            slot = composition.create_sound_slot(edit_rate=rate)
            slot.name = track.name
            slot["PhysicalTrackNumber"].value = number
            sequence = slot.segment
            cursor = 0

            for clip in sorted(track.audio_clips, key=lambda c: c.start):
                if clip.file is None or clip.file not in sources:
                    continue
                if clip.file not in master_mobs:
                    master = f.create.MasterMob(clip.file.stem)
                    f.content.mobs.append(master)
                    source_slot = master.import_audio_essence(str(clip.file), edit_rate=rate)
                    master_mobs[clip.file] = (master, source_slot.segment.length)
                master, source_length = master_mobs[clip.file]

                start = _samples(project.beats_to_seconds(clip.start), rate)
                end = _samples(project.beats_to_seconds(clip.start + clip.length), rate)
                offset = _samples(clip.source_offset, rate)
                if start < cursor:
                    # Clips solapados: se recorta el principio del clip posterior.
                    offset += cursor - start
                    start = cursor
                length = min(end - start, source_length - offset)
                if length <= 0:
                    continue
                if start > cursor:
                    sequence.components.append(f.create.Filler("sound", start - cursor))
                source_clip = master.create_source_clip(slot_id=1, start=offset, length=length)
                sequence.components.append(source_clip)
                cursor = start + length
                clip_count += 1

    return clip_count


def _usable_sources(project: Project) -> dict[Path, int]:
    """Archivos que se pueden incrustar, con su frecuencia de muestreo."""
    sources: dict[Path, int] = {}
    for track in project.tracks:
        for clip in track.audio_clips:
            file = clip.file
            if file is None or file in sources or not file.exists():
                continue
            try:
                with wave.open(str(file), "rb") as wav:
                    sources[file] = wav.getframerate()
            except (wave.Error, EOFError):
                project.warn(
                    f"'{file.name}' no es un WAV PCM; no se incluye en el AAF. "
                    "Conviértelo a WAV (16 o 24 bits) antes de exportar."
                )
    return sources


def _session_rate(rates) -> int:
    rates = list(rates)
    if not rates:
        return DEFAULT_SAMPLE_RATE
    return max(set(rates), key=rates.count)


def _samples(seconds: float, rate: int) -> int:
    return int(round(Fraction(seconds).limit_denominator(10**9) * rate))
