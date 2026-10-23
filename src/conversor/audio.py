"""Preparación del audio de los clips para exportarlo.

Un clip de Ableton puede sonar distinto de su archivo: estirado por el warp,
repetido en bucle, transpuesto o a otra frecuencia de muestreo. Aquí cada clip
se convierte en regiones de archivos WAV que suenan exactamente como en la
sesión:

- Si el clip reproduce el archivo a su velocidad original, se referencia el
  archivo tal cual (con un tramo por vuelta de bucle). Así se conservan los
  márgenes para alargar el clip en el otro DAW.
- Si no, se procesa con Rubber Band (o se cambia la velocidad en Re-Pitch) y
  la región apunta al audio procesado.

Los archivos que no son WAV PCM de 16 o 24 bits, o que tienen otra frecuencia
de muestreo que la sesión, se convierten.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import soundfile as sf
import soxr

from .model import AudioClip, AudioSegment, Project, Track, TrackKind

try:
    import pylibrb
except ImportError:  # pragma: no cover - depende de la plataforma
    pylibrb = None

DEFAULT_SAMPLE_RATE = 48000
# Diferencia máxima entre la duración en el archivo y en la línea de tiempo
# para considerar que un tramo suena a velocidad original.
_TOLERANCE = 0.0005
_CHUNK = 1 << 16
# Margen de audio a cada lado de un tramo al estirarlo (segundos) y longitud
# del fundido cruzado entre tramos (muestras).
_CONTEXT = 0.25
_CROSSFADE = 256
_WAV_SUBTYPES = ("PCM_16", "PCM_24")
_WARP_BEATS, _WARP_COMPLEX_PRO = 0, 6


@dataclass
class Region:
    name: str
    start: float  # segundos en la línea de tiempo
    length: float  # segundos
    file: Path
    offset: float  # segundos dentro del archivo
    gain_db: float = 0.0
    fade_in: float = 0.0  # segundos
    fade_out: float = 0.0


@dataclass
class PreparedAudio:
    rate: int
    tracks: list[tuple[Track, list[Region]]] = field(default_factory=list)
    rendered: int = 0


def prepare(project: Project, workdir: Path, rate: int | None = None, progress=None) -> PreparedAudio:
    """Prepara el audio de todas las pistas. `progress(fracción, texto)` informa del avance."""
    return _Preparer(project, Path(workdir), rate, progress).run()


class _Preparer:
    def __init__(self, project: Project, workdir: Path, rate: int | None, progress=None):
        self.project = project
        self.workdir = workdir
        self.progress = progress
        self.done = 0
        self.total = sum(len(t.audio_clips) for t in project.tracks if t.kind == TrackKind.AUDIO)
        self.infos: dict[Path, sf._SoundFileInfo] = {}
        self.ready_files: dict[Path, Path] = {}
        self.used_names: set[str] = set()
        for track in project.tracks:
            for clip in track.audio_clips:
                if clip.file is not None and clip.file.exists() and clip.file not in self.infos:
                    try:
                        self.infos[clip.file] = sf.info(str(clip.file))
                    except (RuntimeError, sf.LibsndfileError):
                        project.warn(f"No se puede leer el archivo de audio '{clip.file.name}'.")
        rates = Counter(info.samplerate for info in self.infos.values())
        self.rate = rate or (rates.most_common(1)[0][0] if rates else DEFAULT_SAMPLE_RATE)

    def seconds(self, beat: float) -> float:
        return self.project.beats_to_seconds(beat)

    def run(self) -> PreparedAudio:
        self.workdir.mkdir(parents=True, exist_ok=True)
        result = PreparedAudio(self.rate)
        for track in self.project.tracks:
            if track.kind != TrackKind.AUDIO:
                continue
            regions: list[Region] = []
            for clip in sorted(track.audio_clips, key=lambda c: c.start):
                if clip.file not in self.infos:
                    continue
                if self._is_natural(clip):
                    clip_regions = self._natural_regions(clip)
                elif pylibrb is None and not clip.repitch:
                    self.project.warn(
                        f"No está instalado Rubber Band (pylibrb): el clip '{clip.name}' "
                        "se exporta sin estirar ni transponer."
                    )
                    clip_regions = self._natural_regions(clip)
                else:
                    clip_regions = [self._rendered_region(track, clip)]
                    result.rendered += 1
                self._apply_fades(clip, clip_regions)
                regions.extend(clip_regions)
                self.done += 1
                if self.progress:
                    self.progress(self.done / max(self.total, 1), f"Audio: {track.name} – {clip.name}")
            result.tracks.append((track, regions))
        if any(info.format == "MP3" for info in self.infos.values()):
            self.project.warn(
                "Hay archivos MP3: según el codificador puede haber un desfase de unos "
                "milisegundos. Si es posible, usa los originales en WAV o AIFF."
            )
        return result

    # --- Clips que suenan a velocidad original ---

    def _apply_fades(self, clip: AudioClip, regions: list[Region]) -> None:
        """El fundido de entrada va en la primera región del clip y el de salida en la última."""
        if not regions:
            return
        start, end = clip.start, clip.start + clip.length
        fade_in = self.seconds(start + clip.fade_in) - self.seconds(start)
        fade_out = self.seconds(end) - self.seconds(end - clip.fade_out)
        regions[0].fade_in = min(fade_in, regions[0].length)
        regions[-1].fade_out = min(fade_out, regions[-1].length - (regions[-1].fade_in if len(regions) == 1 else 0))

    def _is_natural(self, clip: AudioClip) -> bool:
        if clip.transpose:
            return False
        for segment in clip.playback(self.project):
            timeline = self.seconds(segment.end) - self.seconds(segment.start)
            if abs((segment.source_end - segment.source_start) - timeline) > _TOLERANCE:
                return False
        return True

    def _natural_regions(self, clip: AudioClip) -> list[Region]:
        file = self._ready(clip.file)
        duration = self.infos[clip.file].duration
        regions = []
        for run in _runs(clip.playback(self.project)):
            start = self.seconds(run[0].start)
            length = self.seconds(run[-1].end) - start
            offset = run[0].source_start
            if offset < 0:
                # El clip empieza antes del principio del archivo: ahí no suena nada.
                start, length, offset = start - offset, length + offset, 0.0
            length = min(length, duration - offset)
            if length > 0:
                regions.append(Region(clip.name, start, length, file, offset, clip.gain_db))
        return regions

    def _ready(self, source: Path) -> Path:
        """Devuelve un WAV PCM a la frecuencia de la sesión con el audio de `source`."""
        if source in self.ready_files:
            return self.ready_files[source]
        info = self.infos[source]
        if info.format == "WAV" and info.subtype in _WAV_SUBTYPES and info.samplerate == self.rate:
            target = source
        else:
            data, rate = sf.read(str(source), dtype="float32", always_2d=True)
            target = self._new_file(source.stem)
            sf.write(str(target), self._resample(data, rate), self.rate, subtype="PCM_24", format="WAV")
        self.ready_files[source] = target
        return target

    # --- Clips que hay que procesar ---

    def _rendered_region(self, track: Track, clip: AudioClip) -> Region:
        segments = clip.playback(self.project)
        clip_start = self.seconds(clip.start)
        parts = []
        position = 0
        for run in _runs(segments):
            run_end = round((self.seconds(run[-1].end) - clip_start) * self.rate)
            n_out = run_end - position
            if n_out <= 0:
                continue
            source = self._read(clip.file, run[0].source_start, run[-1].source_end)
            keyframes = self._keyframes(run, clip_start, position)
            if clip.repitch:
                parts.append(_repitch(source, n_out, keyframes))
            else:
                parts.append(_stretch(source, n_out, keyframes, 2.0 ** (clip.transpose / 12.0), clip.warp_mode, self.rate))
            position = run_end
        audio = np.concatenate(parts) if parts else np.zeros((0, 1), dtype=np.float32)
        target = self._new_file(f"{track.name} - {clip.name}")
        sf.write(str(target), np.clip(audio, -1.0, 1.0), self.rate, subtype="PCM_24", format="WAV")
        return Region(clip.name, clip_start, len(audio) / self.rate, target, 0.0, clip.gain_db)

    def _keyframes(self, run: list[AudioSegment], clip_start: float, out_origin: int) -> list[tuple[int, int]]:
        """Puntos (muestra de entrada, muestra de salida) donde cambia la velocidad."""
        source_origin = run[0].source_start
        points = []
        for segment in run:
            beats = [segment.start] + _ramp_points(self.project, segment.start, segment.end)
            span = segment.end - segment.start
            for beat in beats:
                fraction = (beat - segment.start) / span if span else 0.0
                source = segment.source_start + fraction * (segment.source_end - segment.source_start)
                points.append(
                    (
                        round((source - source_origin) * self.rate),
                        round((self.seconds(beat) - clip_start) * self.rate) - out_origin,
                    )
                )
        unique = []
        for point in sorted(points):
            if point[0] > 0 and point[1] > 0 and (not unique or (point[0] > unique[-1][0] and point[1] > unique[-1][1])):
                unique.append(point)
        return unique

    def _read(self, source: Path, start: float, end: float) -> np.ndarray:
        """Lee [start, end) segundos del archivo a la frecuencia de la sesión; fuera del archivo, silencio."""
        info = self.infos[source]
        first = math.floor(start * info.samplerate)
        last = math.ceil(end * info.samplerate)
        data, _ = sf.read(
            str(source), start=max(first, 0), stop=max(min(last, info.frames), 0), dtype="float32", always_2d=True
        )
        data = np.concatenate(
            [
                np.zeros((max(-first, 0), info.channels), dtype=np.float32),
                data,
                np.zeros((max(last - max(info.frames, first), 0), info.channels), dtype=np.float32),
            ]
        )
        return self._resample(data, info.samplerate)

    def _resample(self, data: np.ndarray, rate: int) -> np.ndarray:
        if rate == self.rate or len(data) == 0:
            return data
        return soxr.resample(data, rate, self.rate, quality="VHQ").astype(np.float32)

    def _new_file(self, name: str) -> Path:
        base = re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "audio"
        candidate, n = base, 2
        while candidate.lower() in self.used_names:
            candidate, n = f"{base} {n}", n + 1
        self.used_names.add(candidate.lower())
        return self.workdir / f"{candidate}.wav"


def _fade(audio: np.ndarray, fade_in: int, fade_out: int) -> None:
    """Aplica fundidos lineales (en amplitud) al principio y al final."""
    fade_in, fade_out = min(fade_in, len(audio)), min(fade_out, len(audio))
    if fade_in > 0:
        audio[:fade_in] *= np.linspace(0, 1, fade_in, endpoint=False, dtype=np.float32)[:, None]
    if fade_out > 0:
        audio[len(audio) - fade_out :] *= np.linspace(1, 0, fade_out, dtype=np.float32)[:, None]


def _runs(segments: list[AudioSegment]) -> list[list[AudioSegment]]:
    """Agrupa tramos consecutivos que leen el archivo sin saltos."""
    runs: list[list[AudioSegment]] = []
    for segment in segments:
        if runs and abs(runs[-1][-1].source_end - segment.source_start) < 1e-6 and abs(runs[-1][-1].end - segment.start) < 1e-9:
            runs[-1].append(segment)
        else:
            runs.append([segment])
    return runs


def _ramp_points(project: Project, start: float, end: float) -> list[float]:
    """Puntos intermedios para seguir una rampa de tempo (uno por tiempo)."""
    if not any(p.ramp for p in project.tempo_map):
        return []
    points = []
    beat = math.floor(start) + 1
    while beat < end:
        points.append(float(beat))
        beat += 1
    return points


def _fit(audio: np.ndarray, length: int) -> np.ndarray:
    if len(audio) >= length:
        return audio[:length]
    return np.concatenate([audio, np.zeros((length - len(audio), audio.shape[1]), dtype=audio.dtype)])


def _repitch(source: np.ndarray, n_out: int, keyframes: list[tuple[int, int]]) -> np.ndarray:
    """Cambia la velocidad (y con ella el tono), tramo a tramo."""
    points = [(0, 0)] + keyframes + [(len(source), n_out)]
    parts = []
    for (in0, out0), (in1, out1) in zip(points, points[1:]):
        if out1 <= out0:
            continue
        chunk = source[in0:in1]
        if len(chunk) == 0:
            parts.append(np.zeros((out1 - out0, source.shape[1]), dtype=np.float32))
            continue
        resampled = soxr.resample(chunk, in1 - in0, out1 - out0, quality="VHQ").astype(np.float32)
        parts.append(_fit(resampled, out1 - out0))
    return _fit(np.concatenate(parts), n_out) if parts else np.zeros((n_out, source.shape[1]), np.float32)


def _stretch(source: np.ndarray, n_out: int, keyframes, pitch: float, warp_mode: int | None, rate: int) -> np.ndarray:
    """Estira el audio a n_out muestras con Rubber Band, siguiendo los keyframes.

    Cada tramo entre keyframes tiene una proporción constante y se procesa por
    separado, con margen a ambos lados para que el estiramiento tenga contexto.
    Los tramos se unen con un fundido cruzado corto.
    """
    channels = source.shape[1]
    points = [(0, 0)] + list(keyframes) + [(len(source), n_out)]
    pieces = [(a, b, c, d) for (a, c), (b, d) in zip(points, points[1:]) if d > c]
    output = np.zeros((n_out, channels), dtype=np.float32)
    overlap = min(_CROSSFADE, *(d - c for _, _, c, d in pieces)) // 2 if len(pieces) > 1 else 0
    for index, (in0, in1, out0, out1) in enumerate(pieces):
        length = out1 - out0
        if in1 <= in0:
            continue
        ratio = length / (in1 - in0)
        pad = int(_CONTEXT * rate)
        lo, hi = max(in0 - pad, 0), min(in1 + pad, len(source))
        stretched = _rubberband(source[lo:hi], round((hi - lo) * ratio), ratio, pitch, warp_mode, rate)
        first = index > 0
        last = index == len(pieces) - 1
        begin = round((in0 - lo) * ratio) - (overlap if first else 0)
        piece = _fit(stretched[max(begin, 0):], length + (overlap if first else 0) + (0 if last else overlap))
        if first:
            piece[: 2 * overlap] *= np.linspace(0, 1, 2 * overlap, dtype=np.float32)[:, None]
        if not last:
            piece[len(piece) - 2 * overlap :] *= np.linspace(1, 0, 2 * overlap, dtype=np.float32)[:, None]
        start = out0 - (overlap if first else 0)
        end = min(start + len(piece), n_out)
        output[start:end] += piece[: end - start]
    return output


def _rubberband(source: np.ndarray, n_out: int, ratio: float, pitch: float, warp_mode: int | None, rate: int) -> np.ndarray:
    channels = source.shape[1]
    if len(source) == 0 or n_out <= 0:
        return np.zeros((max(n_out, 0), channels), dtype=np.float32)
    options = pylibrb.Option.PROCESS_OFFLINE | pylibrb.Option.ENGINE_FINER
    if warp_mode == _WARP_BEATS:
        options |= pylibrb.Option.DETECTOR_PERCUSSIVE
    if warp_mode == _WARP_COMPLEX_PRO:
        options |= pylibrb.Option.FORMANT_PRESERVED
    stretcher = pylibrb.RubberBandStretcher(
        sample_rate=rate,
        channels=channels,
        options=options,
        initial_time_ratio=ratio,
        initial_pitch_scale=pitch,
    )
    audio = np.ascontiguousarray(source.T)
    stretcher.set_expected_input_duration(len(source))
    stretcher.set_max_process_size(_CHUNK)
    for i in range(0, len(source), _CHUNK):
        stretcher.study(audio[:, i : i + _CHUNK], final=i + _CHUNK >= len(source))
    output = []
    for i in range(0, len(source), _CHUNK):
        stretcher.process(audio[:, i : i + _CHUNK], final=i + _CHUNK >= len(source))
        while (available := stretcher.available()) > 0:
            output.append(stretcher.retrieve(available))
    while (available := stretcher.available()) > 0:
        output.append(stretcher.retrieve(available))
    result = np.concatenate(output, axis=1).T if output else np.zeros((0, channels), np.float32)
    return _fit(result.astype(np.float32), n_out)


def write_stems(prepared: PreparedAudio, folder: Path, progress=None) -> list[Path]:
    """Escribe un WAV por pista de audio que empieza en el inicio de la sesión.

    Incluye la ganancia de cada clip, pero no el volumen ni el panorama de la
    pista, que se ajustan en el otro DAW. Arrastrados al compás 1, quedan
    alineados con el resto de la sesión.
    """
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    used: set[str] = set()
    for number, (track, regions) in enumerate(prepared.tracks, start=1):
        if progress:
            progress((number - 1) / max(len(prepared.tracks), 1), f"Pistas en WAV: {track.name}")
        if not regions:
            continue
        rate = prepared.rate
        channels = max(sf.info(str(r.file)).channels for r in regions)
        total = max(round((r.start + r.length) * rate) for r in regions)
        mix = np.zeros((total, channels), dtype=np.float32)
        for region in regions:
            start = round(region.start * rate)
            offset = round(region.offset * rate)
            audio, _ = sf.read(
                str(region.file), start=offset, frames=round(region.length * rate), dtype="float32", always_2d=True
            )
            if audio.shape[1] < channels:
                audio = np.repeat(audio[:, :1], channels, axis=1)
            audio = audio[:, :channels] * np.float32(10 ** (region.gain_db / 20))
            _fade(audio, round(region.fade_in * rate), round(region.fade_out * rate))
            end = min(start + len(audio), total)
            # Como en el AAF, un clip posterior tapa al anterior si se solapan.
            mix[start:end] = audio[: end - start]
        name = re.sub(r'[\\/:*?"<>|]+', "_", f"{number:02d} {track.name}").strip()
        while name.lower() in used:
            name += " bis"
        used.add(name.lower())
        path = folder / f"{name}.wav"
        sf.write(str(path), np.clip(mix, -1.0, 1.0), rate, subtype="PCM_24", format="WAV")
        written.append(path)
    return written
