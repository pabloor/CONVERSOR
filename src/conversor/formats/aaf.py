"""Lectura y escritura de AAF (pistas de audio).

Pro Tools, Logic Pro y Cubase/Nuendo importan AAF con las pistas, los clips en
su posición y el audio incrustado. El audio se incrusta en el propio .aaf, así
que el archivo resultante se puede mover a otro equipo sin perder medios.

Escritura: solo se incrustan archivos WAV PCM; la mezcla (volumen, panorama)
y la automatización no se escriben en el AAF.

Lectura: pensada para los AAF que exporta Pro Tools, con el audio incrustado o
enlazado a archivos externos. El AAF no lleva el tempo de la sesión, así que
las posiciones se pasan a tiempos con el mapa de tempo del proyecto que se
recibe (por ejemplo, el del MIDI exportado desde la misma sesión).
"""

from __future__ import annotations

import re
import struct
import sys
import urllib.parse
import wave
from fractions import Fraction
from functools import partial
from pathlib import Path

import aaf2
from aaf2.components import (
    EssenceGroup,
    Filler,
    OperationGroup,
    Selector,
    Sequence,
    SourceClip,
    Transition,
)
from aaf2.essence import AIFCDescriptor, FileDescriptor, WAVEDescriptor
from aaf2.misc import ConstantValue, VaryingValue
from aaf2.mobid import MobID
from aaf2.mobs import MasterMob, SourceMob

from ..model import (
    AudioClip,
    Automation,
    AutomationPoint,
    Project,
    Track,
    TrackKind,
    gain_to_db,
)

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


# --- Lectura -----------------------------------------------------------------

_CHUNK = 1 << 20


def read(path: str | Path, project: Project | None = None) -> Project:
    """Añade al proyecto las pistas de audio de un AAF y lo devuelve."""
    path = Path(path).resolve()
    if project is None:
        project = Project(name=path.stem)
        project.warn(
            "El AAF no incluye el tempo de la sesión: se usa 120 BPM. Exporta también "
            "un MIDI desde Pro Tools o indica el tempo para que la rejilla coincida."
        )
    with aaf2.open(str(path), "r") as f:
        composition = _main_composition(f)
        if composition is None:
            raise ValueError(f"{path.name} no contiene ninguna composición")
        _AAFReader(project, path).read(composition)
    return project


def _main_composition(f):
    compositions = list(f.content.toplevel()) or list(f.content.compositionmobs())
    if not compositions:
        return None
    # Si hay varias, la que tiene más pistas de audio.
    return max(compositions, key=lambda m: sum(1 for s in m.slots if _is_sound(s.segment)))


def _is_sound(segment) -> bool:
    return (segment.media_kind or "").lower() == "sound"


class _AAFReader:
    def __init__(self, project: Project, path: Path):
        self.project = project
        self.path = path
        self.media: dict[object, Path | None] = {}
        self.media_dir = path.parent / f"{_safe_name(path.stem)} audio"

    def read(self, composition) -> None:
        existing = {t.name for t in self.project.tracks}
        for slot in composition.slots:
            segment = slot.segment
            if not _is_sound(segment):
                continue
            name = slot.name or f"Audio {slot.slot_id}"
            unique, n = name, 2
            while unique in existing:
                unique, n = f"{name} {n}", n + 1
            existing.add(unique)
            track = Track(name=unique, kind=TrackKind.AUDIO)
            rate = float(slot.edit_rate)
            if isinstance(segment, OperationGroup) and _gain_parameter(segment) is not None:
                self._track_gain(track, segment, rate)
            self._walk(segment, 0, rate, track, 0.0)
            self.project.tracks.append(track)

    def _track_gain(self, track: Track, group, rate: float) -> None:
        parameter = _gain_parameter(group)
        if isinstance(parameter, ConstantValue):
            track.volume_db = gain_to_db(float(parameter.value))
        elif isinstance(parameter, VaryingValue):
            points = []
            for point in parameter.pointlist:
                seconds = float(point.time) * group.length / rate
                points.append(
                    AutomationPoint(self.project.seconds_to_beats(seconds), gain_to_db(float(point.value)))
                )
            if points:
                track.volume_db = points[0].value
                track.automation.append(Automation("volume", points))

    def _walk(self, segment, position: int, rate: float, track: Track, gain_db: float) -> int:
        """Recorre un segmento a partir de `position` (en unidades de `rate`)."""
        if isinstance(segment, Sequence):
            for component in segment.components:
                if isinstance(component, Transition):
                    position -= component.length
                else:
                    position = self._walk(component, position, rate, track, gain_db)
            return position
        if isinstance(segment, SourceClip):
            self._add_clip([segment], position, rate, track, gain_db)
            return position + segment.length
        if isinstance(segment, OperationGroup):
            inputs = list(segment.segments)
            parameter = _gain_parameter(segment)
            if isinstance(parameter, ConstantValue):
                gain_db += gain_to_db(float(parameter.value))
            channels = [_single_clip(i) for i in inputs]
            if len(inputs) > 1 and all(channels):
                # Pistas estéreo o multicanal: un clip mono por canal.
                self._add_clip(channels, position, rate, track, gain_db, segment.length)
            elif inputs:
                self._walk(inputs[0], position, rate, track, gain_db)
            return position + segment.length
        if isinstance(segment, Selector):
            self._walk(segment["Selected"].value, position, rate, track, gain_db)
            return position + segment.length
        if not isinstance(segment, Filler):
            self.project.warn(
                f"Elemento AAF no compatible ({type(segment).__name__}) en la pista '{track.name}'; se omite."
            )
        return position + (segment.length or 0)

    def _add_clip(self, clips, position, rate, track, gain_db, length=None) -> None:
        length = clips[0].length if length is None else length
        resolved = [self._resolve(clip, rate) for clip in clips]
        if any(r is None for r in resolved):
            self.project.warn(f"Un clip de la pista '{track.name}' no tiene audio asociado; se omite.")
            return
        sources = [r[0] for r in resolved]
        offset, name = resolved[0][1], resolved[0][2]
        if len(sources) == 1:
            file = self._media(sources[0], name)
        else:
            name = _strip_channel(name) if name else name
            file = self._combined_media(sources, name)
        start = self.project.seconds_to_beats(position / rate)
        end = self.project.seconds_to_beats((position + length) / rate)
        track.audio_clips.append(
            AudioClip(
                name=name or (file.stem if file else track.name),
                start=start,
                length=end - start,
                file=file,
                source_offset=max(offset, 0.0),
                gain_db=gain_db,
            )
        )

    @staticmethod
    def _resolve(clip, rate: float):
        """Sigue la cadena de referencias hasta el SourceMob con el audio.

        Devuelve (source_mob, desplazamiento en segundos, nombre del clip).
        """
        offset = clip.start / rate
        name = None
        mob, slot = clip.mob, clip.slot
        for _ in range(16):
            if mob is None or slot is None:
                return None
            if isinstance(mob, MasterMob) and name is None:
                name = mob.name
            if isinstance(mob, SourceMob) and isinstance(mob.descriptor, FileDescriptor):
                return mob, offset, name
            slot_rate = float(slot.edit_rate)
            segment = slot.segment
            if isinstance(segment, Sequence):
                units = offset * slot_rate
                chosen = None
                for _, pos, component in segment.positions():
                    if not isinstance(component, Transition) and pos <= units < pos + component.length:
                        chosen = (pos, component)
                        break
                if chosen is None:
                    return None
                offset -= chosen[0] / slot_rate
                segment = chosen[1]
            if isinstance(segment, EssenceGroup):
                choices = list(segment["Choices"].value)
                segment = choices[0] if choices else None
            if not isinstance(segment, SourceClip):
                return None
            offset += segment.start / slot_rate
            mob, slot = segment.mob, segment.slot
        return None

    def _media(self, source_mob, name: str | None) -> Path | None:
        key = source_mob.mob_id
        if key in self.media:
            return self.media[key]
        file = None
        if source_mob.essence is not None:
            file = self._virtual_path(name or source_mob.name or "audio")
            self.project.embedded_media[file] = partial(
                _extract_embedded, self.path, str(source_mob.mob_id)
            )
        else:
            file = self._linked_file(source_mob)
        self.media[key] = file
        return file

    def _combined_media(self, sources, name: str | None) -> Path | None:
        key = tuple(s.mob_id for s in sources)
        if key in self.media:
            return self.media[key]
        channels = [self._media(s, None) for s in sources]
        if any(c is None for c in channels):
            self.media[key] = None
            return None
        file = self._virtual_path(name or "audio")
        self.project.embedded_media[file] = partial(
            _combine_channels, channels, dict(self.project.embedded_media)
        )
        self.media[key] = file
        return file

    def _virtual_path(self, name: str) -> Path:
        base = _safe_name(name)
        candidate, n = self.media_dir / f"{base}.wav", 2
        while candidate in self.project.embedded_media:
            candidate, n = self.media_dir / f"{base} {n}.wav", n + 1
        return candidate

    def _linked_file(self, source_mob) -> Path | None:
        descriptor = source_mob.descriptor
        locators = descriptor.get("Locator", allkeys=False)
        urls = [loc["URLString"].value for loc in (locators.value if locators else [])]
        names = []
        for url in urls:
            candidate = _url_to_path(url)
            if candidate.exists():
                return self._check_format(candidate)
            names.append(candidate.name)
        # Pro Tools suele exportar los medios en una carpeta junto al AAF.
        for filename in names:
            for folder in (self.path.parent, self.path.parent / "Audio Files"):
                if (folder / filename).exists():
                    return self._check_format(folder / filename)
            for found in self.path.parent.rglob(filename):
                return self._check_format(found)
        shown = names[0] if names else source_mob.name
        self.project.warn(f"No se encuentra el archivo de audio '{shown}' enlazado en el AAF.")
        return None

    def _check_format(self, file: Path) -> Path:
        if file.suffix.lower() == ".mxf":
            self.project.warn(
                f"'{file.name}' es MXF y Ableton no lo abre. Exporta el AAF desde Pro Tools "
                "con el audio incrustado o en formato WAV."
            )
        return file


def _gain_parameter(group):
    for parameter in group.parameters:
        name = (parameter.name or "").lower()
        if "amplitude" in name or "gain" in name or "level" in name:
            return parameter
    return None


def _single_clip(segment):
    if isinstance(segment, SourceClip):
        return segment
    if isinstance(segment, Sequence):
        clips = [c for c in segment.components if not isinstance(c, Filler)]
        if len(clips) == 1 and isinstance(clips[0], SourceClip):
            return clips[0]
    return None


def _strip_channel(name: str) -> str:
    """'Voz.L' -> 'Voz': Pro Tools nombra cada canal de un clip estéreo con un sufijo."""
    return re.sub(r"[._ -](L|R|C|Ls|Rs|Lfe|LFE|\d{1,2})$", "", name) or name


def _url_to_path(url: str) -> Path:
    parsed = urllib.parse.urlparse(url)
    raw = urllib.parse.unquote(parsed.path if parsed.scheme else url)
    if sys.platform == "win32" and re.match(r"^/[A-Za-z]:", raw):
        raw = raw[1:]
    return Path(raw)


def _safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "audio"


def _extract_embedded(aaf_path: Path, mob_id: str, target: Path) -> None:
    """Escribe como WAV el audio incrustado en el SourceMob indicado."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with aaf2.open(str(aaf_path), "r") as f:
        mob = f.content.mobs.get(MobID(mob_id))
        descriptor = mob.descriptor
        stream = mob.essence.open("r")
        head = stream.read(12)
        if head[:4] == b"RIFF":
            with open(target, "wb") as out:
                out.write(head)
                _copy(stream, out)
            return
        channels, bits, rate, big_endian = _pcm_format(descriptor)
        width = bits // 8
        with wave.open(str(target), "wb") as out:
            out.setnchannels(channels)
            out.setsampwidth(width)
            out.setframerate(rate)
            pending = head
            while True:
                chunk = stream.read(_CHUNK)
                data = pending + chunk
                usable = len(data) - len(data) % (width * channels)
                frames, pending = data[:usable], data[usable:]
                if big_endian:
                    frames = _byteswap(frames, width)
                out.writeframesraw(frames)
                if not chunk:
                    break


def _pcm_format(descriptor) -> tuple[int, int, int, bool]:
    if isinstance(descriptor, WAVEDescriptor):
        summary = bytes(descriptor["Summary"].value)
        index = summary.find(b"fmt ")
        _, channels, rate, _, _, bits = struct.unpack("<HHIIHH", summary[index + 8 : index + 24])
        return channels, bits, rate, False
    if isinstance(descriptor, AIFCDescriptor):
        summary = bytes(descriptor["Summary"].value)
        index = summary.find(b"COMM")
        channels, _, bits = struct.unpack(">hIh", summary[index + 8 : index + 16])
        rate = _read_extended(summary[index + 16 : index + 26])
        return channels, bits, rate, True
    channels = descriptor["Channels"].value
    bits = descriptor["QuantizationBits"].value
    rate = int(float(descriptor["AudioSamplingRate"].value))
    return channels, bits, rate, False


def _read_extended(data: bytes) -> int:
    """Número en coma flotante de 80 bits (frecuencia de muestreo en AIFF)."""
    exponent, mantissa = struct.unpack(">HQ", data)
    return int(round(mantissa * 2.0 ** ((exponent & 0x7FFF) - 16383 - 63)))


def _byteswap(data: bytes, width: int) -> bytes:
    swapped = bytearray(len(data))
    for b in range(width):
        swapped[b::width] = data[width - 1 - b :: width]
    return bytes(swapped)


def _copy(stream, out) -> None:
    while chunk := stream.read(_CHUNK):
        out.write(chunk)


def _combine_channels(channels: list[Path], pending: dict, target: Path) -> None:
    """Une varios archivos mono en uno multicanal."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = []
    sources = []
    for index, channel in enumerate(channels):
        if channel in pending:
            path = target.with_name(f"{target.stem}.canal{index + 1}.wav")
            pending[channel](path)
            temporary.append(path)
            sources.append(path)
        else:
            sources.append(channel)
    try:
        readers = [wave.open(str(s), "rb") for s in sources]
        try:
            width = readers[0].getsampwidth()
            frames = min(r.getnframes() for r in readers)
            with wave.open(str(target), "wb") as out:
                out.setnchannels(len(readers))
                out.setsampwidth(width)
                out.setframerate(readers[0].getframerate())
                step = 65536
                for _ in range(0, frames, step):
                    blocks = [r.readframes(step) for r in readers]
                    count = min(len(b) for b in blocks) // width
                    data = bytearray(count * width * len(blocks))
                    stride = width * len(blocks)
                    for c, block in enumerate(blocks):
                        for b in range(width):
                            data[c * width + b :: stride] = block[b : count * width : width]
                    out.writeframesraw(bytes(data))
        finally:
            for reader in readers:
                reader.close()
    finally:
        for path in temporary:
            path.unlink(missing_ok=True)
