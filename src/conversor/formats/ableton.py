"""Lectura y escritura de sesiones de Ableton Live (.als, XML comprimido con gzip).

Lectura: estructura de Live 10, 11 y 12. Solo se leen los clips del arreglo
(Arrangement View); los clips de la vista Session se ignoran.

Escritura: se parte de una plantilla (la incluida, con formato de Live 11, o un
set vacío guardado con la versión de Ableton del usuario) y se duplican su
primera pista de audio y su primera pista MIDI para cada pista del proyecto.
"""

from __future__ import annotations

import copy
import gzip
import math
import shutil
import wave
import xml.etree.ElementTree as ET
from importlib import resources
from pathlib import Path

from ..model import (
    AudioClip,
    AudioSegment,
    Automation,
    AutomationPoint,
    ControllerEvent,
    Marker,
    MidiClip,
    Note,
    Project,
    TempoPoint,
    TimeSignature,
    Track,
    TrackKind,
    db_to_gain,
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

# Modos de warp de Ableton (WarpMode): 0 Beats, 1 Tones, 2 Texture, 3 Re-Pitch,
# 4 Complex, 6 Complex Pro.
_WARP_REPITCH = 3


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
    project.master_devices = _devices(master)
    tracks = liveset.find("Tracks")
    if tracks is not None:
        reader.read_tracks(tracks)
    for locator in liveset.iterfind("Locators/Locators/Locator"):
        project.markers.append(Marker(_float(locator, "Time"), _value(locator, "Name") or ""))
    project.markers.sort(key=lambda m: m.beat)
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
            devices=_devices(element),
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
            controllers = {
                target.get("Id"): int(target.tag.split(".")[1])
                for target in sequencer.findall("MidiControllers/*")
                if target.tag.startswith("ControllerTargets.") and target.get("Id")
            }
            for clip in sequencer.iterfind("ClipTimeable/ArrangerAutomation/Events/MidiClip"):
                midi = self._midi_clip(clip, controllers, track.name)
                if midi is not None:
                    track.midi_clips.append(midi)
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
        label = f"'{name or track_name}' (pista '{track_name}')"
        if _bool(clip, "Disabled"):
            self.project.warn(f"El clip {label} está desactivado en Ableton; no se exporta.")
            return None
        start = _float(clip, "CurrentStart", float(clip.get("Time", "0")))
        end = _float(clip, "CurrentEnd", start)
        if end <= start:
            return None
        loop_start = _float(clip, "Loop/LoopStart")
        loop_end = _float(clip, "Loop/LoopEnd")
        content_start = loop_start + _float(clip, "Loop/StartRelative")
        looped = _bool(clip, "Loop/LoopOn")
        warped = _bool(clip, "IsWarped", True)
        warp_mode = int(_float(clip, "WarpMode", 0))
        transpose = _float(clip, "PitchCoarse") + _float(clip, "PitchFine") / 100.0

        if warped:
            # Contenido en tiempos del clip; los warp markers lo llevan a segundos del archivo.
            markers = sorted(
                (float(m.get("BeatTime", "0")), float(m.get("SecTime", "0")))
                for m in clip.iterfind("WarpMarkers/WarpMarker")
            )
            marker_beats = [b for b, _ in markers]
            segments = []
            for t0, t1, c0, _ in _loop_pieces(end - start, content_start, loop_start, loop_end, looped):
                cuts = [c0] + [b for b in marker_beats if c0 < b < c0 + (t1 - t0)] + [c0 + (t1 - t0)]
                for a, b in zip(cuts, cuts[1:]):
                    segments.append(
                        AudioSegment(
                            start + t0 + (a - c0),
                            start + t0 + (b - c0),
                            _warp_beat_to_seconds(markers, a),
                            _warp_beat_to_seconds(markers, b),
                        )
                    )
            repitch = warp_mode == _WARP_REPITCH
        else:
            # Sin warp, el audio avanza en segundos reales; la transposición
            # cambia la velocidad, como en un sampler.
            speed = 2.0 ** (transpose / 12.0)
            first = self.project.beats_to_seconds(start)
            total = self.project.beats_to_seconds(end) - first
            segments = []
            for t0, t1, c0, c1 in _loop_pieces(total, content_start, loop_start, loop_end, looped, speed):
                b0 = self.project.seconds_to_beats(first + t0)
                b1 = self.project.seconds_to_beats(first + t1)
                cuts = [b0] + _tempo_breaks(self.project, b0, b1) + [b1]
                for a, b in zip(cuts, cuts[1:]):
                    sa = self.project.beats_to_seconds(a) - first - t0
                    sb = self.project.beats_to_seconds(b) - first - t0
                    segments.append(AudioSegment(a, b, c0 + sa * speed, c0 + sb * speed))
            if segments:
                segments[-1].end = end
            repitch = True
        if not segments:
            return None

        file = self._resolve_file(clip.find("SampleRef/FileRef"))
        if file is None or not file.exists():
            shown = file.name if file else "desconocido"
            self.project.warn(f"No se encuentra el archivo de audio '{shown}' del clip {label}.")

        # Fundidos: en tiempos del clip si tiene warp y en segundos si no (como
        # el resto de posiciones del contenido).
        fade_in = max(_float(clip, "Fades/FadeInLength"), 0.0)
        fade_out = max(_float(clip, "Fades/FadeOutLength"), 0.0)
        if not warped:
            first = self.project.beats_to_seconds(start)
            last = self.project.beats_to_seconds(end)
            fade_in = self.project.seconds_to_beats(first + fade_in) - start
            fade_out = end - self.project.seconds_to_beats(max(last - fade_out, 0.0))
        fade_in = min(fade_in, end - start)
        fade_out = min(fade_out, end - start - fade_in)

        return AudioClip(
            name=name or (file.stem if file else track_name),
            start=start,
            length=end - start,
            file=file,
            source_offset=max(segments[0].source_start, 0.0) if segments else 0.0,
            gain_db=gain_to_db(_float(clip, "SampleVolume", 1.0)),
            segments=segments,
            transpose=0.0 if repitch else transpose,
            repitch=repitch,
            warp_mode=warp_mode if warped else None,
            fade_in=fade_in,
            fade_out=fade_out,
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

    def _midi_clip(self, clip: ET.Element, controllers: dict[str, int], track_name: str) -> MidiClip | None:
        name = _value(clip, "Name") or ""
        if _bool(clip, "Disabled"):
            self.project.warn(f"El clip MIDI '{name}' está desactivado en Ableton; no se exporta.")
            return None
        start = _float(clip, "CurrentStart", float(clip.get("Time", "0")))
        length = _float(clip, "CurrentEnd", start) - start
        loop_start = _float(clip, "Loop/LoopStart")
        loop_end = _float(clip, "Loop/LoopEnd")
        content_start = loop_start + _float(clip, "Loop/StartRelative")
        looped = _bool(clip, "Loop/LoopOn")

        source: list[Note] = []
        random_notes = 0
        for key_track in clip.iterfind("Notes/KeyTracks/KeyTrack"):
            pitch = int(_float(key_track, "MidiKey", 60))
            for event in key_track.iterfind("Notes/MidiNoteEvent"):
                if event.get("IsEnabled", "true") != "true":
                    continue
                if float(event.get("Probability", "1")) < 1 or float(event.get("VelocityDeviation", "0")) != 0:
                    random_notes += 1
                source.append(
                    Note(
                        pitch=pitch,
                        start=float(event.get("Time", "0")),
                        duration=float(event.get("Duration", "0.25")),
                        velocity=int(round(float(event.get("Velocity", "100")))),
                    )
                )

        segments = [
            (c0, c1, t0) for t0, _, c0, c1 in _loop_pieces(length, content_start, loop_start, loop_end, looped)
        ]

        notes: list[Note] = []
        for seg_start, seg_end, timeline_pos in segments:
            for note in source:
                if seg_start - 1e-9 <= note.start < seg_end - 1e-9:
                    duration = min(note.duration, seg_end - note.start)
                    notes.append(Note(note.pitch, timeline_pos + note.start - seg_start, duration, note.velocity))
        notes.sort(key=lambda n: (n.start, n.pitch))
        if random_notes:
            self.project.warn(
                f"El clip MIDI '{name or track_name}' (pista '{track_name}') tiene "
                f"{random_notes} notas con probabilidad o variación de velocidad: se exportan "
                "todas, con su velocidad base, y suenan siempre."
            )
        events = self._clip_controllers(clip, controllers, segments, length)
        return MidiClip(name=name, start=start, length=length, notes=notes, controllers=events)

    def _clip_controllers(self, clip, controllers, segments, length) -> list[ControllerEvent]:
        """Envolventes de controladores MIDI del clip (pitch bend, presión, CC).

        Los destinos de las envolventes son los ControllerTargets.N de la pista:
        0 es pitch bend, 1 es presión de canal (aftertouch) y 2 + n es el CC n.
        Las envolventes se repiten con el bucle del clip, como las notas.
        """
        result: list[ControllerEvent] = []
        for envelope in clip.iterfind("Envelopes/Envelopes/ClipEnvelope"):
            index = controllers.get(_value(envelope, "EnvelopeTarget/PointeeId"))
            if index is None:
                continue
            if index == 0:
                kind, number, low, high = "pitchbend", 0, -8192, 8191
            elif index == 1:
                kind, number, low, high = "aftertouch", 0, 0, 127
            else:
                kind, number, low, high = "cc", index - 2, 0, 127
            points = sorted(
                (float(e.get("Time", "0")), float(e.get("Value", "0")))
                for e in envelope.iterfind("Automation/Events/*")
                if e.get("Value") is not None
            )
            if not points:
                continue
            for seg_start, seg_end, timeline_pos in segments:
                for beat, value in _sample_envelope(points, seg_start, seg_end):
                    event_value = int(round(max(low, min(high, value))))
                    if result and result[-1].kind == kind and result[-1].number == number and result[-1].value == event_value:
                        continue
                    result.append(ControllerEvent(timeline_pos + beat - seg_start, kind, event_value, number))
        result.sort(key=lambda e: e.beat)
        return result


def _sample_envelope(points, start, end, step=1 / 32):
    """Valores de una envolvente (lineal entre puntos) en [start, end), cada `step` tiempos."""
    def value_at(beat):
        if beat <= points[0][0]:
            return points[0][1]
        for (b0, v0), (b1, v1) in zip(points, points[1:]):
            if b0 <= beat < b1:
                return v0 if b1 == b0 else v0 + (v1 - v0) * (beat - b0) / (b1 - b0)
        return points[-1][1]

    beats = {start} | {b for b, _ in points if start < b < end}
    for (b0, v0), (b1, v1) in zip(points, points[1:]):
        if v0 != v1 and b1 > start and b0 < end:
            beat = max(b0, start)
            while beat < min(b1, end):
                beats.add(beat)
                beat += step
    return [(beat, value_at(beat)) for beat in sorted(beats) if beat < end]


# Nombres que muestra Ableton para los dispositivos cuyo nombre interno es distinto.
_DEVICE_NAMES = {
    "Eq8": "EQ Eight", "FilterEQ3": "EQ Three", "Compressor2": "Compressor",
    "GlueCompressor": "Glue Compressor", "MultibandDynamics": "Multiband Dynamics",
    "StereoGain": "Utility", "AutoFilter": "Auto Filter", "AutoPan": "Auto Pan",
    "Chorus2": "Chorus-Ensemble", "PhaserNew": "Phaser-Flanger", "Redux2": "Redux",
    "Hybrid": "Hybrid Reverb", "PingPongDelay": "Ping Pong Delay", "FilterDelay": "Filter Delay",
    "GrainDelay": "Grain Delay", "BeatRepeat": "Beat Repeat", "DrumBuss": "Drum Buss",
    "FrequencyShifter": "Frequency Shifter", "OriginalSimpler": "Simpler",
    "MultiSampler": "Sampler", "InstrumentVector": "Wavetable", "UltraAnalog": "Analog",
    "InstrumentImpulse": "Impulse", "LoungeLizard": "Electric", "StringStudio": "Tension",
    "DrumGroupDevice": "Drum Rack", "InstrumentGroupDevice": "Instrument Rack",
    "AudioEffectGroupDevice": "Audio Effect Rack", "MidiEffectGroupDevice": "MIDI Effect Rack",
    "MidiArpeggiator": "Arpeggiator", "MidiChord": "Chord", "MidiScale": "Scale",
    "MidiNoteLength": "Note Length", "MidiPitcher": "Pitch", "MidiRandom": "Random",
    "MidiVelocity": "Velocity", "MxDeviceAudioEffect": "Max for Live (efecto)",
    "MxDeviceInstrument": "Max for Live (instrumento)", "MxDeviceMidiEffect": "Max for Live (MIDI)",
}


def _devices(track: ET.Element | None) -> list[str]:
    """Nombres de los dispositivos de una pista, con el fabricante para los plugins."""
    if track is None:
        return []
    names = []
    for device in track.findall("DeviceChain/DeviceChain/Devices/*"):
        user_name = _value(device, "UserName") or ""
        if device.tag in ("PluginDevice", "AuPluginDevice"):
            info = device.find("PluginDesc/*")
            plugin = _value(info, "PlugName") or _value(info, "Name") or "plugin"
            kind = {"VstPluginInfo": "VST", "Vst3PluginInfo": "VST3", "AuPluginInfo": "AU"}.get(
                info.tag if info is not None else "", "plugin"
            )
            name = f"{plugin} ({kind})"
        else:
            name = _DEVICE_NAMES.get(device.tag, device.tag)
        if user_name and user_name != name:
            name = f"{user_name} [{name}]"
        if not _bool(device, "On/Manual", True):
            name += " (desactivado)"
        names.append(name)
    return names


def _loop_pieces(total, content_start, loop_start, loop_end, looped, speed=1.0):
    """Divide la reproducción de un clip en tramos sin saltos.

    Devuelve (inicio, fin) en la línea de tiempo, relativos al clip, y
    (inicio, fin) en el contenido. Sin bucle hay un solo tramo; con bucle, el
    primero va de content_start al final del bucle y los demás repiten
    [loop_start, loop_end). `speed` es cuánto avanza el contenido por unidad de tiempo.
    """
    pieces = []
    position = 0.0
    piece_start = content_start
    piece_end = loop_end if looped else math.inf
    while position < total - 1e-9:
        span = min((piece_end - piece_start) / speed, total - position)
        if span <= 1e-9:
            break
        pieces.append((position, position + span, piece_start, piece_start + span * speed))
        position += span
        if not looped:
            break
        piece_start, piece_end = loop_start, loop_end
    return pieces


def _tempo_breaks(project: Project, start: float, end: float) -> list[float]:
    """Instantes entre start y end donde cambia el tempo (cada tiempo dentro de una rampa)."""
    breaks = []
    points = sorted(project.tempo_map, key=lambda p: p.beat)
    for point, nxt in zip(points, points[1:] + [None]):
        if start < point.beat < end:
            breaks.append(point.beat)
        if point.ramp and nxt is not None:
            beat = math.floor(max(point.beat, start)) + 1
            while beat < min(nxt.beat, end):
                breaks.append(float(beat))
                beat += 1
    return sorted(set(breaks))


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


# --- Escritura ---------------------------------------------------------------

_MIN_GAIN = 0.0003162277571
_MAX_GAIN = 1.99526238
_TEMPO_RANGE = (20.0, 999.0)


def write(project: Project, path: str | Path, template: str | Path | None = None) -> Path:
    """Escribe el proyecto como set de Ableton y copia el audio a Samples/Imported.

    `path` es la ruta del .als; el audio se guarda en la misma carpeta.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    root = _load_template(template)
    liveset = root.find("LiveSet")
    writer = _Writer(project, root, path.parent)
    writer.write_tracks(liveset)
    writer.write_master(liveset)
    liveset.find("NextPointeeId").set("Value", str(writer.next_pointee))
    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    path.write_bytes(gzip.compress(data))
    return path


def _load_template(template: str | Path | None) -> ET.Element:
    if template is None:
        data = resources.files("conversor").joinpath("templates/ableton11.xml").read_bytes()
    else:
        data = Path(template).read_bytes()
        if data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
    root = ET.fromstring(data)
    if root.find("LiveSet") is None:
        raise ValueError("La plantilla no es un set de Ableton Live")
    return root


def _is_pointee(element: ET.Element) -> bool:
    tag = element.tag
    return "Id" in element.attrib and (
        tag.endswith("Target") or tag == "Pointee" or tag.startswith("ControllerTargets")
    )


def _sub(parent: ET.Element, tag: str, value=None, **attrs) -> ET.Element:
    element = ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})
    if value is not None:
        element.set("Value", _fmt(value))
    return element


def _fmt(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.10f}".rstrip("0").rstrip(".")
    return str(value)


def _set(element: ET.Element | None, path: str, value) -> None:
    if element is None:
        return
    found = element.find(path)
    if found is not None:
        found.set("Value", _fmt(value))


def _encode_time_signature(numerator: int, denominator: int) -> int:
    index = _DENOMINATORS.index(denominator) if denominator in _DENOMINATORS else 2
    return index * 99 + max(1, min(99, numerator)) - 1


class _Writer:
    def __init__(self, project: Project, root: ET.Element, folder: Path):
        self.project = project
        self.folder = folder
        self.samples = folder / "Samples" / "Imported"
        ids = [int(e.get("Id")) for e in root.iter() if _is_pointee(e)]
        declared = root.find("LiveSet/NextPointeeId")
        self.next_pointee = max(ids + [int(declared.get("Value", "0")) - 1 if declared is not None else 0]) + 1
        self.copied: dict[Path, Path] = {}

    def _pointee(self) -> int:
        value = self.next_pointee
        self.next_pointee += 1
        return value

    # --- Pistas ---

    def write_tracks(self, liveset: ET.Element) -> None:
        tracks = liveset.find("Tracks")
        prototypes = {
            TrackKind.AUDIO: tracks.find("AudioTrack"),
            TrackKind.MIDI: tracks.find("MidiTrack"),
        }
        track_id = max((int(t.get("Id", "0")) for t in tracks), default=0) + 1
        for element in list(tracks):
            if element.tag != "ReturnTrack":
                tracks.remove(element)
        position = 0
        for track in self.project.tracks:
            prototype = prototypes.get(track.kind)
            if track.kind not in prototypes:
                continue
            if prototype is None:
                raise ValueError(
                    "La plantilla necesita al menos una pista de audio y una pista MIDI."
                )
            element = copy.deepcopy(prototype)
            element.set("Id", str(track_id))
            track_id += 1
            for child in element.iter():
                if _is_pointee(child):
                    child.set("Id", str(self._pointee()))
            self._fill_track(element, track)
            tracks.insert(position, element)
            position += 1

    def _fill_track(self, element: ET.Element, track: Track) -> None:
        _set(element, "Name/EffectiveName", track.name)
        _set(element, "Name/UserName", track.name)
        _set(element, "TrackGroupId", -1)
        mixer = element.find("DeviceChain/Mixer")
        _set(mixer, "Volume/Manual", self._gain(track.volume_db))
        _set(mixer, "Pan/Manual", max(-1.0, min(1.0, track.pan)))
        _set(mixer, "Speaker/Manual", not track.mute)
        _set(mixer, "SoloSink", track.solo)

        envelopes = element.find("AutomationEnvelopes/Envelopes")
        for child in list(envelopes):
            envelopes.remove(child)
        targets = {"volume": "Volume", "pan": "Pan", "mute": "Speaker"}
        for automation in track.automation:
            target = mixer.find(f"{targets[automation.parameter]}/AutomationTarget")
            if target is None or not automation.points:
                continue
            if automation.parameter == "volume":
                values = [(p.beat, self._gain(p.value)) for p in automation.points]
            elif automation.parameter == "pan":
                values = [(p.beat, max(-1.0, min(1.0, p.value))) for p in automation.points]
            else:
                values = [(p.beat, p.value < 0.5) for p in automation.points]
            self._envelope(envelopes, target.get("Id"), values)

        sequencer = element.find("DeviceChain/MainSequencer")
        if track.kind == TrackKind.AUDIO:
            events = sequencer.find("Sample/ArrangerAutomation/Events")
        else:
            events = sequencer.find("ClipTimeable/ArrangerAutomation/Events")
        for child in list(events):
            events.remove(child)
        if track.kind == TrackKind.AUDIO:
            for index, clip in enumerate(sorted(track.audio_clips, key=lambda c: c.start)):
                self._audio_clip(events, index, clip)
        else:
            for index, clip in enumerate(sorted(track.midi_clips, key=lambda c: c.start)):
                self._midi_clip(events, index, clip)

    @staticmethod
    def _gain(db: float) -> float:
        return max(_MIN_GAIN, min(_MAX_GAIN, db_to_gain(db)))

    def _envelope(self, envelopes: ET.Element, pointee: str, values) -> None:
        envelope = _sub(envelopes, "AutomationEnvelope", Id=len(envelopes))
        _sub(_sub(envelope, "EnvelopeTarget"), "PointeeId", pointee)
        automation = _sub(envelope, "Automation")
        events = _sub(automation, "Events")
        kind = "BoolEvent" if isinstance(values[0][1], bool) else "FloatEvent"
        first = values[0][1]
        _sub(events, kind, Id=0, Time=_fmt(_PRE_START), Value=_fmt(first))
        for index, (beat, value) in enumerate(values, start=1):
            _sub(events, kind, Id=index, Time=_fmt(float(beat)), Value=_fmt(value))
        transform = _sub(automation, "AutomationTransformViewState")
        _sub(transform, "IsTransformPending", False)
        _sub(transform, "TimeAndValueTransforms")

    # --- Clips ---

    def _clip_common(self, clip: ET.Element, start: float, length: float, loop_start: float,
                     loop_end: float, name: str, color: int) -> None:
        _sub(clip, "LomId", 0)
        _sub(clip, "LomIdView", 0)
        _sub(clip, "CurrentStart", start)
        _sub(clip, "CurrentEnd", start + length)
        loop = _sub(clip, "Loop")
        _sub(loop, "LoopStart", loop_start)
        _sub(loop, "LoopEnd", loop_end)
        _sub(loop, "StartRelative", 0)
        _sub(loop, "LoopOn", False)
        _sub(loop, "OutMarker", loop_end)
        _sub(loop, "HiddenLoopStart", loop_start)
        _sub(loop, "HiddenLoopEnd", loop_end)
        _sub(clip, "Name", name)
        _sub(clip, "Annotation", "")
        _sub(clip, "Color", color)
        _sub(clip, "LaunchMode", 0)
        _sub(clip, "LaunchQuantisation", 0)
        signature = self.project.time_signatures[0]
        remote = _sub(_sub(_sub(clip, "TimeSignature"), "TimeSignatures"), "RemoteableTimeSignature", Id=0)
        _sub(remote, "Numerator", signature.numerator)
        _sub(remote, "Denominator", signature.denominator)
        _sub(remote, "Time", 0)
        _sub(_sub(clip, "Envelopes"), "Envelopes")
        scroller = _sub(clip, "ScrollerTimePreserver")
        _sub(scroller, "LeftTime", 0)
        _sub(scroller, "RightTime", 0)
        selection = _sub(clip, "TimeSelection")
        _sub(selection, "AnchorTime", 0)
        _sub(selection, "OtherTime", 0)
        _sub(clip, "Legato", False)
        _sub(clip, "Ram", False)
        _sub(_sub(clip, "GrooveSettings"), "GrooveId", -1)
        _sub(clip, "Disabled", False)
        _sub(clip, "VelocityAmount", 0)
        follow = _sub(clip, "FollowAction")
        for tag, value in (
            ("FollowTime", 4), ("IsLinked", True), ("LoopIterations", 1), ("FollowActionA", 4),
            ("FollowActionB", 0), ("FollowChanceA", 100), ("FollowChanceB", 0),
            ("JumpIndexA", 1), ("JumpIndexB", 1), ("FollowActionEnabled", False),
        ):
            _sub(follow, tag, value)
        grid = _sub(clip, "Grid")
        for tag, value in (
            ("FixedNumerator", 1), ("FixedDenominator", 16), ("GridIntervalPixel", 20),
            ("Ntoles", 2), ("SnapToGrid", True), ("Fixed", False),
        ):
            _sub(grid, tag, value)
        _sub(clip, "FreezeStart", 0)
        _sub(clip, "FreezeEnd", 0)

    def _audio_clip(self, events: ET.Element, index: int, audio: AudioClip) -> None:
        if audio.file is None:
            return
        file, frames, rate = self._sample(audio.file)
        seconds = self.project.beats_to_seconds(audio.start + audio.length) - self.project.beats_to_seconds(audio.start)
        clip = _sub(events, "AudioClip", Id=index, Time=_fmt(float(audio.start)))
        loop_start = audio.source_offset
        self._clip_common(clip, audio.start, audio.length, loop_start, loop_start + seconds, audio.name, 16)
        _sub(clip, "IsWarped", False)
        _sub(clip, "TakeId", 1)
        sample_ref = _sub(clip, "SampleRef")
        file_ref = _sub(sample_ref, "FileRef")
        try:
            relative = file.relative_to(self.folder).as_posix()
            relative_type = 3
        except ValueError:
            relative, relative_type = "", 0
        _sub(file_ref, "RelativePathType", relative_type)
        _sub(file_ref, "RelativePath", relative)
        _sub(file_ref, "Path", str(file.resolve()))
        _sub(file_ref, "Type", 1)
        _sub(file_ref, "LivePackName", "")
        _sub(file_ref, "LivePackId", "")
        _sub(file_ref, "OriginalFileSize", file.stat().st_size if file.exists() else 0)
        _sub(file_ref, "OriginalCrc", 0)
        _sub(sample_ref, "LastModDate", 0)
        _sub(sample_ref, "SourceContext")
        _sub(sample_ref, "SampleUsageHint", 0)
        _sub(sample_ref, "DefaultDuration", frames)
        _sub(sample_ref, "DefaultSampleRate", rate)
        onsets = _sub(clip, "Onsets")
        _sub(onsets, "UserOnsets")
        _sub(onsets, "HasUserOnsets", False)
        for tag, value in (
            ("WarpMode", 0), ("GranularityTones", 30), ("GranularityTexture", 65),
            ("FluctuationTexture", 25), ("TransientResolution", 6), ("TransientLoopMode", 2),
            ("TransientEnvelope", 100), ("ComplexProFormants", 100), ("ComplexProEnvelope", 128),
            ("Sync", True), ("HiQ", True), ("Fade", True),
        ):
            _sub(clip, tag, value)
        fades = _sub(clip, "Fades")
        for tag, value in (
            ("FadeInLength", 0), ("FadeOutLength", 0), ("ClipFadesAreInitialized", True),
            ("CrossfadeInState", 0), ("FadeInCurveSkew", 0), ("FadeInCurveSlope", 0),
            ("FadeOutCurveSkew", 0), ("FadeOutCurveSlope", 0), ("IsDefaultFadeIn", True),
            ("IsDefaultFadeOut", True),
        ):
            _sub(fades, tag, value)
        _sub(clip, "PitchCoarse", 0)
        _sub(clip, "PitchFine", 0)
        _sub(clip, "SampleVolume", db_to_gain(audio.gain_db))
        bpm = self.project.tempo_at(audio.start)
        markers = _sub(clip, "WarpMarkers")
        _sub(markers, "WarpMarker", Id=0, SecTime="0", BeatTime="0")
        _sub(markers, "WarpMarker", Id=1, SecTime="1", BeatTime=_fmt(bpm / 60.0))
        _sub(clip, "SavedWarpMarkersForStretched")
        _sub(clip, "MarkersGenerated", False)
        _sub(clip, "IsSongTempoLeader", False)

    def _sample(self, source: Path) -> tuple[Path, int, int]:
        """Lleva el audio a Samples/Imported y devuelve (ruta, muestras, frecuencia)."""
        if source not in self.copied:
            target = self.samples / source.name
            n = 2
            while target in self.copied.values():
                target = self.samples / f"{source.stem} {n}{source.suffix}"
                n += 1
            if source in self.project.embedded_media:
                self.samples.mkdir(parents=True, exist_ok=True)
                self.project.embedded_media[source](target)
            elif source.exists():
                self.samples.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            else:
                target = source
            self.copied[source] = target
        target = self.copied[source]
        try:
            with wave.open(str(target), "rb") as wav:
                return target, wav.getnframes(), wav.getframerate()
        except (OSError, wave.Error, EOFError):
            return target, 0, 44100

    def _midi_clip(self, events: ET.Element, index: int, midi: MidiClip) -> None:
        clip = _sub(events, "MidiClip", Id=index, Time=_fmt(float(midi.start)))
        self._clip_common(clip, midi.start, midi.length, 0, midi.length, midi.name, 9)
        _sub(clip, "IsWarped", True)
        _sub(clip, "TakeId", 1)
        notes = _sub(clip, "Notes")
        key_tracks = _sub(notes, "KeyTracks")
        by_pitch: dict[int, list[Note]] = {}
        for note in midi.notes:
            by_pitch.setdefault(note.pitch, []).append(note)
        note_id = 1
        for key_index, pitch in enumerate(sorted(by_pitch)):
            key_track = _sub(key_tracks, "KeyTrack", Id=key_index)
            events_el = _sub(key_track, "Notes")
            for note in sorted(by_pitch[pitch], key=lambda n: n.start):
                _sub(
                    events_el, "MidiNoteEvent",
                    Time=_fmt(float(note.start)), Duration=_fmt(float(max(note.duration, 1e-3))),
                    Velocity=_fmt(float(max(1, min(127, note.velocity)))), VelocityDeviation="0",
                    OffVelocity="64", Probability="1", IsEnabled="true", NoteId=note_id,
                )
                note_id += 1
            _sub(key_track, "MidiKey", pitch)
        _sub(_sub(notes, "PerNoteEventStore"), "EventLists")
        _sub(notes, "NoteProbabilityGroups")
        _sub(_sub(notes, "ProbabilityGroupIdGenerator"), "NextId", 1)
        _sub(_sub(notes, "NoteIdGenerator"), "NextId", note_id)
        _sub(clip, "BankSelectCoarse", -1)
        _sub(clip, "BankSelectFine", -1)
        _sub(clip, "ProgramChange", -1)

    # --- Tempo y compás ---

    def write_master(self, liveset: ET.Element) -> None:
        master = liveset.find("MainTrack")
        if master is None:
            master = liveset.find("MasterTrack")
        mixer = master.find("DeviceChain/Mixer")
        points = sorted(self.project.tempo_map, key=lambda p: p.beat)
        low, high = _TEMPO_RANGE
        bpm = lambda value: max(low, min(high, value))  # noqa: E731
        _set(mixer, "Tempo/Manual", bpm(points[0].bpm))
        signatures = self.project.time_signatures
        first = signatures[0]
        _set(mixer, "TimeSignature/Manual", _encode_time_signature(first.numerator, first.denominator))

        envelopes = master.find("AutomationEnvelopes/Envelopes")
        tempo_id = mixer.find("Tempo/AutomationTarget").get("Id")
        signature_id = mixer.find("TimeSignature/AutomationTarget").get("Id")
        for envelope in list(envelopes):
            if _value(envelope, "EnvelopeTarget/PointeeId") in (tempo_id, signature_id):
                envelopes.remove(envelope)

        if len(points) > 1:
            values = []
            for previous, point in zip(points, points[1:]):
                if not previous.ramp:
                    values.append((point.beat, bpm(previous.bpm)))
                values.append((point.beat, bpm(point.bpm)))
            values.insert(0, (0.0, bpm(points[0].bpm)))
            self._envelope(envelopes, tempo_id, values)
        if len(signatures) > 1:
            envelope = _sub(envelopes, "AutomationEnvelope", Id=len(envelopes))
            _sub(_sub(envelope, "EnvelopeTarget"), "PointeeId", signature_id)
            automation = _sub(envelope, "Automation")
            events = _sub(automation, "Events")
            _sub(events, "EnumEvent", Id=0, Time=_fmt(_PRE_START),
                 Value=_encode_time_signature(first.numerator, first.denominator))
            for index, signature in enumerate(signatures[1:], start=1):
                _sub(events, "EnumEvent", Id=index, Time=_fmt(float(signature.beat)),
                     Value=_encode_time_signature(signature.numerator, signature.denominator))
            transform = _sub(automation, "AutomationTransformViewState")
            _sub(transform, "IsTransformPending", False)
            _sub(transform, "TimeAndValueTransforms")
