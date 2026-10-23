"""Modelo intermedio común a todos los formatos.

Las posiciones en la línea de tiempo se guardan en tiempos (negras) y se
convierten a segundos con el mapa de tempo del proyecto. Los desplazamientos
dentro de un archivo de audio se guardan en segundos.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class TrackKind(str, Enum):
    AUDIO = "audio"
    MIDI = "midi"
    GROUP = "group"
    RETURN = "return"


@dataclass
class TempoPoint:
    beat: float
    bpm: float
    # Si es True, el tempo cambia linealmente hasta el siguiente punto.
    ramp: bool = False


@dataclass
class TimeSignature:
    beat: float
    numerator: int
    denominator: int


@dataclass
class Note:
    pitch: int
    start: float  # en tiempos, relativo al inicio del clip
    duration: float
    velocity: int = 100


@dataclass
class AudioClip:
    name: str
    start: float  # tiempos
    length: float  # tiempos
    file: Path | None
    source_offset: float = 0.0  # segundos dentro del archivo
    gain_db: float = 0.0


@dataclass
class MidiClip:
    name: str
    start: float
    length: float
    notes: list[Note] = field(default_factory=list)


@dataclass
class AutomationPoint:
    beat: float
    value: float


@dataclass
class Automation:
    # "volume" (dB), "pan" (-1..1) o "mute" (0/1)
    parameter: str
    points: list[AutomationPoint] = field(default_factory=list)


@dataclass
class Track:
    name: str
    kind: TrackKind
    color: tuple[int, int, int] | None = None
    volume_db: float = 0.0
    pan: float = 0.0
    mute: bool = False
    solo: bool = False
    group: str | None = None
    audio_clips: list[AudioClip] = field(default_factory=list)
    midi_clips: list[MidiClip] = field(default_factory=list)
    automation: list[Automation] = field(default_factory=list)


@dataclass
class Project:
    name: str
    tempo_map: list[TempoPoint] = field(default_factory=lambda: [TempoPoint(0.0, 120.0)])
    time_signatures: list[TimeSignature] = field(
        default_factory=lambda: [TimeSignature(0.0, 4, 4)]
    )
    tracks: list[Track] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def beats_to_seconds(self, beat: float) -> float:
        points = sorted(self.tempo_map, key=lambda p: p.beat)
        seconds = 0.0
        position = 0.0
        bpm = points[0].bpm
        for i, point in enumerate(points):
            if point.beat >= beat:
                break
            # Tramo [position, point.beat) con el tempo anterior.
            seconds += (point.beat - position) * 60.0 / bpm
            position = point.beat
            bpm = point.bpm
            nxt = points[i + 1] if i + 1 < len(points) else None
            if point.ramp and nxt is not None and nxt.bpm != point.bpm:
                end = min(beat, nxt.beat)
                bpm_end = point.bpm + (nxt.bpm - point.bpm) * (end - point.beat) / (
                    nxt.beat - point.beat
                )
                seconds += _ramp_seconds(end - point.beat, point.bpm, bpm_end)
                position = end
                bpm = nxt.bpm
                if end >= beat:
                    return seconds
        return seconds + (beat - position) * 60.0 / bpm

    def tempo_at(self, beat: float) -> float:
        points = sorted(self.tempo_map, key=lambda p: p.beat)
        current = points[0].bpm
        for i, point in enumerate(points):
            if point.beat > beat:
                break
            current = point.bpm
            nxt = points[i + 1] if i + 1 < len(points) else None
            if point.ramp and nxt is not None and nxt.beat > beat:
                frac = (beat - point.beat) / (nxt.beat - point.beat)
                return point.bpm + (nxt.bpm - point.bpm) * frac
        return current


def _ramp_seconds(beats: float, bpm_start: float, bpm_end: float) -> float:
    """Duración de una rampa lineal de tempo (en función de los tiempos)."""
    if beats <= 0:
        return 0.0
    if math.isclose(bpm_start, bpm_end):
        return beats * 60.0 / bpm_start
    return beats * 60.0 * math.log(bpm_end / bpm_start) / (bpm_end - bpm_start)


def gain_to_db(gain: float) -> float:
    return 20.0 * math.log10(gain) if gain > 0 else -math.inf


def db_to_gain(db: float) -> float:
    return 0.0 if db == -math.inf else 10.0 ** (db / 20.0)
