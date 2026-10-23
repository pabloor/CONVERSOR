"""Lectura y escritura de archivos MIDI estándar (SMF).

Todos los DAW (Pro Tools, Logic, Cubase, FL Studio, Ableton) importan este
formato con tempo, compás, nombres de pista, notas y controladores.
"""

from __future__ import annotations

import math
from pathlib import Path

import mido

from ..model import MidiClip, Note, Project, TempoPoint, TimeSignature, Track, TrackKind

TICKS_PER_BEAT = 960
# Las rampas de tempo se aproximan con un cambio cada semicorchea.
_RAMP_STEP = 0.25


def write(project: Project, path: str | Path) -> int:
    """Escribe las pistas MIDI del proyecto. Devuelve el número de pistas escritas."""
    midi = mido.MidiFile(type=1, ticks_per_beat=TICKS_PER_BEAT)
    midi.tracks.append(_conductor(project))

    count = 0
    for track in project.tracks:
        if track.kind != TrackKind.MIDI:
            continue
        events: list[tuple[int, int, mido.Message]] = []
        # El segundo campo ordena eventos simultáneos: note_off antes que note_on.
        events.append((0, 0, mido.Message("control_change", control=7, value=_volume_cc(track.volume_db))))
        events.append((0, 0, mido.Message("control_change", control=10, value=_pan_cc(track.pan))))
        for automation in track.automation:
            for point in automation.points:
                if automation.parameter == "volume":
                    message = mido.Message("control_change", control=7, value=_volume_cc(point.value))
                elif automation.parameter == "pan":
                    message = mido.Message("control_change", control=10, value=_pan_cc(point.value))
                else:
                    continue
                events.append((_ticks(point.beat), 0, message))
        for clip in track.midi_clips:
            for note in clip.notes:
                start = _ticks(clip.start + note.start)
                end = max(_ticks(clip.start + note.start + note.duration), start + 1)
                velocity = max(1, min(127, note.velocity))
                pitch = max(0, min(127, note.pitch))
                events.append((start, 2, mido.Message("note_on", note=pitch, velocity=velocity)))
                events.append((end, 1, mido.Message("note_off", note=pitch, velocity=0)))

        midi_track = mido.MidiTrack()
        midi_track.append(mido.MetaMessage("track_name", name=track.name, time=0))
        _append_sorted(midi_track, events)
        midi.tracks.append(midi_track)
        count += 1

    midi.save(str(path))
    return count


def _conductor(project: Project) -> mido.MidiTrack:
    events: list[tuple[int, int, mido.MetaMessage]] = []
    for signature in project.time_signatures:
        events.append(
            (
                _ticks(signature.beat),
                0,
                mido.MetaMessage(
                    "time_signature",
                    numerator=signature.numerator,
                    denominator=signature.denominator,
                ),
            )
        )
    points = sorted(project.tempo_map, key=lambda p: p.beat)
    for i, point in enumerate(points):
        nxt = points[i + 1] if i + 1 < len(points) else None
        beat = point.beat
        if point.ramp and nxt is not None:
            while beat < nxt.beat - 1e-9:
                events.append((_ticks(beat), 1, _tempo(project.tempo_at(beat + _RAMP_STEP / 2))))
                beat += _RAMP_STEP
        else:
            events.append((_ticks(beat), 1, _tempo(point.bpm)))

    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name=project.name, time=0))
    _append_sorted(track, events)
    return track


def _tempo(bpm: float) -> mido.MetaMessage:
    return mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm))


def _append_sorted(track: mido.MidiTrack, events: list) -> None:
    now = 0
    for tick, _, message in sorted(events, key=lambda e: (e[0], e[1])):
        track.append(message.copy(time=tick - now))
        now = tick
    track.append(mido.MetaMessage("end_of_track", time=0))


def _ticks(beat: float) -> int:
    return max(0, int(round(beat * TICKS_PER_BEAT)))


def _volume_cc(db: float) -> int:
    # Curva General MIDI: dB = 40 * log10(cc / 127).
    if db == -math.inf:
        return 0
    return max(0, min(127, int(round(127 * 10 ** (db / 40.0)))))


def _pan_cc(pan: float) -> int:
    return max(0, min(127, int(round(64 + pan * 63))))


def read(path: str | Path, project: Project | None = None) -> Project:
    """Lee tempo, compás y pistas con notas. Si se pasa un proyecto, se añaden a él."""
    path = Path(path)
    midi = mido.MidiFile(str(path))
    project = project or Project(name=path.stem)
    ppq = midi.ticks_per_beat

    tempo_map: list[TempoPoint] = []
    signatures: list[TimeSignature] = []
    for track in midi.tracks:
        now = 0
        for message in track:
            now += message.time
            beat = now / ppq
            if message.type == "set_tempo":
                if tempo_map and tempo_map[-1].beat == beat:
                    tempo_map.pop()
                tempo_map.append(TempoPoint(beat, round(mido.tempo2bpm(message.tempo), 3)))
            elif message.type == "time_signature":
                if signatures and signatures[-1].beat == beat:
                    signatures.pop()
                signatures.append(TimeSignature(beat, message.numerator, message.denominator))
    tempo_map.sort(key=lambda p: p.beat)
    signatures.sort(key=lambda s: s.beat)
    if tempo_map:
        if tempo_map[0].beat > 0:
            tempo_map.insert(0, TempoPoint(0.0, 120.0))
        project.tempo_map = tempo_map
    if signatures:
        if signatures[0].beat > 0:
            signatures.insert(0, TimeSignature(0.0, 4, 4))
        project.time_signatures = signatures

    for index, track in enumerate(midi.tracks):
        name = track.name.strip() or f"MIDI {index + 1}"
        notes: list[Note] = []
        open_notes: dict[tuple[int, int], list[tuple[float, int]]] = {}
        now = 0
        for message in track:
            now += message.time
            beat = now / ppq
            if message.type == "note_on" and message.velocity > 0:
                open_notes.setdefault((message.channel, message.note), []).append((beat, message.velocity))
            elif message.type in ("note_off", "note_on"):
                started = open_notes.get((message.channel, message.note))
                if started:
                    begin, velocity = started.pop(0)
                    notes.append(Note(message.note, begin, max(beat - begin, 1 / ppq), velocity))
        if not notes:
            continue
        notes.sort(key=lambda n: (n.start, n.pitch))
        bar = 4.0 * project.time_signatures[0].numerator / project.time_signatures[0].denominator
        start = math.floor(notes[0].start / bar) * bar
        end = math.ceil(max(n.start + n.duration for n in notes) / bar) * bar
        for note in notes:
            note.start -= start
        clip = MidiClip(name=name, start=start, length=max(end - start, bar), notes=notes)
        project.tracks.append(Track(name=name, kind=TrackKind.MIDI, midi_clips=[clip]))
    return project
