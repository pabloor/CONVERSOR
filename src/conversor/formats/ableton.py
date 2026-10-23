"""Lector de sesiones de Ableton Live (.als, XML comprimido con gzip).

Probado con la estructura de Live 10, 11 y 12. Solo se leen los clips del
arreglo (Arrangement View); los clips de la vista Session se ignoran.
"""

from __future__ import annotations

import gzip
import math
import xml.etree.ElementTree as ET
from pathlib import Path

from ..model import (
    AudioClip,
    Automation,
    AutomationPoint,
    MidiClip,
    Note,
    Project,
    TempoPoint,
    TimeSignature,
    Track,
    TrackKind,
    gain_to_db,
)

# Ableton usa este instante (en tiempos) para el valor inicial de cada envolvente.
_PRE_START = -63072000.0

_TRACK_KINDS = {
    "AudioTrack": TrackKind.AUDIO,
    "MidiTrack": TrackKind.MIDI,
    "GroupTrack": TrackKind.GROUP,
    "ReturnTrack": TrackKind.RETURN,
}

_DENOMINATORS = [1, 2, 4, 8, 16]


def read(path: str | Path) -> Project:
    path = Path(path)
    data = path.read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    root = ET.fromstring(data)
    liveset = root.find("LiveSet")
    if liveset is None:
        raise ValueError(f"{path.name} no parece una sesión de Ableton Live")

    project = Project(name=path.stem)
    reader = _Reader(project, path.parent)
    master = liveset.find("MainTrack")
    if master is None:
        master = liveset.find("MasterTrack")
    reader.read_master(master)
    tracks = liveset.find("Tracks")
    if tracks is not None:
        reader.read_tracks(tracks)
    return project


def _value(element: ET.Element | None, path: str, default: str | None = None) -> str | None:
    if element is None:
        return default
    found = element.find(path)
    if found is None:
        return default
    return found.get("Value", default)


def _float(element: ET.Element | None, path: str, default: float = 0.0) -> float:
    value = _value(element, path)
    return float(value) if value is not None else default


def _bool(element: ET.Element | None, path: str, default: bool = False) -> bool:
    value = _value(element, path)
    return value == "true" if value is not None else default


def _decode_time_signature(value: int) -> tuple[int, int]:
    numerator = value % 99 + 1
    index = value // 99
    denominator = _DENOMINATORS[index] if index < len(_DENOMINATORS) else 4
    return numerator, denominator


class _Reader:
    def __init__(self, project: Project, base_dir: Path):
        self.project = project
        self.base_dir = base_dir

    # --- Pista maestra: tempo y compás -----------------------------------

    def read_master(self, master: ET.Element | None) -> None:
        if master is None:
            return
        mixer = master.find("DeviceChain/Mixer")
        bpm = _float(mixer, "Tempo/Manual", 120.0)
        numerator, denominator = _decode_time_signature(
            int(_float(mixer, "TimeSignature/Manual", 201))
        )
        envelopes = self._envelopes(master)

        tempo_events = envelopes.get(self._target_id(mixer, "Tempo"), [])
        self.project.tempo_map = self._tempo_map(tempo_events) or [TempoPoint(0.0, bpm)]

        signature_events = envelopes.get(self._target_id(mixer, "TimeSignature"), [])
        signatures = []
        for beat, value in signature_events:
            num, den = _decode_time_signature(int(value))
            beat = max(beat, 0.0)
            if signatures and signatures[-1].beat == beat:
                signatures.pop()
            signatures.append(TimeSignature(beat, num, den))
        self.project.time_signatures = signatures or [TimeSignature(0.0, numerator, denominator)]

    @staticmethod
    def _tempo_map(events: list[tuple[float, float]]) -> list[TempoPoint]:
        points: list[TempoPoint] = []
        for beat, bpm in events:
            beat = max(beat, 0.0)
            if points and points[-1].beat == beat:
                # Dos eventos en el mismo instante: salto de tempo.
                points[-1].ramp = False
                if points[-1].bpm == bpm:
                    continue
                if beat == 0.0:
                    points[-1].bpm = bpm
                    continue
            points.append(TempoPoint(beat, bpm))
        # Ableton interpola linealmente entre puntos consecutivos.
        for current, nxt in zip(points, points[1:]):
            if current.beat != nxt.beat and current.bpm != nxt.bpm:
                current.ramp = True
        # Elimina el duplicado del salto (mismo instante, el primero no aporta).
        result: list[TempoPoint] = []
        for point in points:
            if result and result[-1].beat == point.beat:
                result[-1] = point
            else:
                result.append(point)
        return result

    # --- Pistas -----------------------------------------------------------

    def read_tracks(self, tracks: ET.Element) -> None:
        names_by_id: dict[str, str] = {}
        parents: list[tuple[Track, str]] = []
        for element in tracks:
            kind = _TRACK_KINDS.get(element.tag)
            if kind is None:
                continue
            track = self._read_track(element, kind)
            names_by_id[element.get("Id", "")] = track.name
            group_id = _value(element, "TrackGroupId", "-1")
            if group_id not in (None, "-1"):
                parents.append((track, group_id))
            self.project.tracks.append(track)
        for track, group_id in parents:
            track.group = names_by_id.get(group_id)

    def _read_track(self, element: ET.Element, kind: TrackKind) -> Track:
        name = _value(element, "Name/UserName") or _value(element, "Name/EffectiveName") or element.tag
        mixer = element.find("DeviceChain/Mixer")
        volume_gain = _float(mixer, "Volume/Manual", 1.0)
        track = Track(
            name=name,
            kind=kind,
            volume_db=gain_to_db(volume_gain),
            pan=_float(mixer, "Pan/Manual", 0.0),
            mute=not _bool(mixer, "Speaker/Manual", True),
            solo=_bool(mixer, "SoloSink", False),
        )

        envelopes = self._envelopes(element)
        for parameter, target, convert in (
            ("volume", "Volume", gain_to_db),
            ("pan", "Pan", float),
            ("mute", "Speaker", lambda v: 0.0 if v else 1.0),
        ):
            events = envelopes.get(self._target_id(mixer, target))
            if events:
                points = [AutomationPoint(max(b, 0.0), convert(v)) for b, v in events]
                track.automation.append(Automation(parameter, points))

        sequencer = element.find("DeviceChain/MainSequencer")
        if kind == TrackKind.AUDIO and sequencer is not None:
            for clip in sequencer.iterfind("Sample/ArrangerAutomation/Events/AudioClip"):
                audio = self._audio_clip(clip, track.name)
                if audio is not None:
                    track.audio_clips.append(audio)
        if kind == TrackKind.MIDI and sequencer is not None:
            for clip in sequencer.iterfind("ClipTimeable/ArrangerAutomation/Events/MidiClip"):
                track.midi_clips.append(self._midi_clip(clip))
        if kind in (TrackKind.GROUP, TrackKind.RETURN):
            self.project.warn(
                f"La pista '{name}' es un {'grupo' if kind == TrackKind.GROUP else 'retorno'}: "
                "los formatos de intercambio no admiten buses, así que no se exporta."
            )
        return track

    # --- Envolventes de automatización ------------------------------------

    @staticmethod
    def _target_id(mixer: ET.Element | None, parameter: str) -> str | None:
        if mixer is None:
            return None
        target = mixer.find(f"{parameter}/AutomationTarget")
        return target.get("Id") if target is not None else None

    @staticmethod
    def _envelopes(element: ET.Element) -> dict[str, list[tuple[float, float]]]:
        result: dict[str, list[tuple[float, float]]] = {}
        for envelope in element.iterfind("AutomationEnvelopes/Envelopes/AutomationEnvelope"):
            pointee = _value(envelope, "EnvelopeTarget/PointeeId")
            events_element = envelope.find("Automation/Events")
            if pointee is None or events_element is None:
                continue
            events = []
            for event in events_element:
                raw = event.get("Value")
                if raw is None:
                    continue
                if raw in ("true", "false"):
                    value = 1.0 if raw == "true" else 0.0
                else:
                    value = float(raw)
                time = float(event.get("Time", "0"))
                events.append((0.0 if time <= _PRE_START else time, value))
            events.sort(key=lambda e: e[0])
            result[pointee] = events
        return result

    # --- Clips -------------------------------------------------------------

    def _audio_clip(self, clip: ET.Element, track_name: str) -> AudioClip | None:
        name = _value(clip, "Name") or ""
        start = _float(clip, "CurrentStart", float(clip.get("Time", "0")))
        end = _float(clip, "CurrentEnd", start)
        loop_start = _float(clip, "Loop/LoopStart")
        loop_end = _float(clip, "Loop/LoopEnd")
        content_start = loop_start + _float(clip, "Loop/StartRelative")
        warped = _bool(clip, "IsWarped", True)
        label = f"'{name or track_name}' (pista '{track_name}')"

        if warped:
            markers = sorted(
                (float(m.get("BeatTime", "0")), float(m.get("SecTime", "0")))
                for m in clip.iterfind("WarpMarkers/WarpMarker")
            )
            offset = _warp_beat_to_seconds(markers, content_start)
            clip_seconds = _warp_beat_to_seconds(markers, content_start + (end - start)) - offset
            timeline_seconds = self.project.beats_to_seconds(end) - self.project.beats_to_seconds(start)
            if timeline_seconds > 0 and abs(clip_seconds / timeline_seconds - 1) > 0.005:
                self.project.warn(
                    f"El clip {label} está estirado (warp) respecto al tempo de la sesión; "
                    "se exporta sin estirar. Consolídalo en Ableton (Ctrl+J) para conservar el resultado."
                )
        else:
            offset = content_start

        looped = _bool(clip, "Loop/LoopOn")
        if looped and (end - start) > (loop_end - content_start) + 1e-6:
            self.project.warn(
                f"El clip {label} se repite en bucle; solo se exporta la primera vuelta. "
                "Consolídalo en Ableton para conservar las repeticiones."
            )

        file = self._resolve_file(clip.find("SampleRef/FileRef"))
        if file is None or not file.exists():
            shown = file.name if file else "desconocido"
            self.project.warn(f"No se encuentra el archivo de audio '{shown}' del clip {label}.")

        return AudioClip(
            name=name or (file.stem if file else track_name),
            start=start,
            length=end - start,
            file=file,
            source_offset=max(offset, 0.0),
            gain_db=gain_to_db(_float(clip, "SampleVolume", 1.0)),
        )

    def _resolve_file(self, file_ref: ET.Element | None) -> Path | None:
        if file_ref is None:
            return None
        candidates: list[Path] = []
        absolute = _value(file_ref, "Path")
        relative = _value(file_ref, "RelativePath")
        if relative:
            candidates.append(self.base_dir / relative)
        if absolute:
            candidates.append(Path(absolute))
        # Live 10: la ruta se guarda como una lista de carpetas más el nombre.
        dirs = [e.get("Dir", "") for e in file_ref.iterfind("RelativePath/RelativePathElement")]
        name = _value(file_ref, "Name")
        if name:
            candidates.append(self.base_dir.joinpath(*dirs, name))
        for candidate in candidates:
            if candidate.exists():
                return candidate
        # Último recurso: busca el archivo por nombre dentro de la carpeta del proyecto.
        filename = name or (Path(absolute).name if absolute else None) or (
            Path(relative).name if relative else None
        )
        if filename:
            for found in self.base_dir.rglob(filename):
                return found
        return candidates[0] if candidates else None

    def _midi_clip(self, clip: ET.Element) -> MidiClip:
        name = _value(clip, "Name") or ""
        start = _float(clip, "CurrentStart", float(clip.get("Time", "0")))
        length = _float(clip, "CurrentEnd", start) - start
        loop_start = _float(clip, "Loop/LoopStart")
        loop_end = _float(clip, "Loop/LoopEnd")
        content_start = loop_start + _float(clip, "Loop/StartRelative")
        looped = _bool(clip, "Loop/LoopOn")

        source: list[Note] = []
        for key_track in clip.iterfind("Notes/KeyTracks/KeyTrack"):
            pitch = int(_float(key_track, "MidiKey", 60))
            for event in key_track.iterfind("Notes/MidiNoteEvent"):
                if event.get("IsEnabled", "true") != "true":
                    continue
                source.append(
                    Note(
                        pitch=pitch,
                        start=float(event.get("Time", "0")),
                        duration=float(event.get("Duration", "0.25")),
                        velocity=int(round(float(event.get("Velocity", "100")))),
                    )
                )

        # Recorre el contenido como lo reproduce Ableton: desde content_start
        # hasta el final del bucle y, si hay bucle, repite [loop_start, loop_end).
        segments: list[tuple[float, float, float]] = []  # (inicio, fin, posición en el clip)
        position = 0.0
        seg_start = content_start
        seg_end = loop_end if looped else content_start + length
        while position < length - 1e-9 and seg_end > seg_start:
            span = min(seg_end - seg_start, length - position)
            segments.append((seg_start, seg_start + span, position))
            position += span
            if not looped:
                break
            seg_start, seg_end = loop_start, loop_end

        notes: list[Note] = []
        for seg_start, seg_end, timeline_pos in segments:
            for note in source:
                if seg_start - 1e-9 <= note.start < seg_end - 1e-9:
                    duration = min(note.duration, seg_end - note.start)
                    notes.append(Note(note.pitch, timeline_pos + note.start - seg_start, duration, note.velocity))
        notes.sort(key=lambda n: (n.start, n.pitch))
        return MidiClip(name=name, start=start, length=length, notes=notes)


def _warp_beat_to_seconds(markers: list[tuple[float, float]], beat: float) -> float:
    """Convierte una posición del clip (tiempos) a segundos del archivo usando los warp markers."""
    if len(markers) < 2:
        if markers:
            # Sin información suficiente: se asume 120 BPM a partir del único marcador.
            b0, s0 = markers[0]
            return s0 + (beat - b0) * 0.5
        return beat * 0.5
    for (b0, s0), (b1, s1) in zip(markers, markers[1:]):
        if beat <= b1 or (b1, s1) == markers[-1]:
            if math.isclose(b1, b0):
                return s0
            return s0 + (beat - b0) * (s1 - s0) / (b1 - b0)
    return markers[-1][1]
