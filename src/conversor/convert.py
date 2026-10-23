"""Punto de entrada común para la línea de comandos y la aplicación."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .formats import aaf, ableton, midi
from .model import Project, TrackKind

READERS = {
    ".als": ableton.read,
}

TARGETS = ("aaf", "midi")

_KIND_LABELS = {
    TrackKind.AUDIO: "audio",
    TrackKind.MIDI: "MIDI",
    TrackKind.GROUP: "grupo",
    TrackKind.RETURN: "retorno",
}
_PARAMETER_LABELS = {"volume": "volumen", "pan": "panorama", "mute": "silencio"}

IMPORT_HELP = """\
Cómo abrir el resultado en cada DAW
-----------------------------------
Pro Tools:   Archivo > Abrir sesión (o Importar > Datos de sesión) con el .aaf;
             Archivo > Importar > MIDI con el .mid.
Logic Pro:   Archivo > Importar > AAF con el .aaf;
             Archivo > Importar > Archivo MIDI con el .mid.
Cubase:      Archivo > Importar > Archivo AAF con el .aaf;
             Archivo > Importar > Archivo MIDI con el .mid.
FL Studio:   no admite AAF. Importa el .mid (Archivo > Importar > Archivo MIDI)
             y arrastra los audios a la lista de reproducción.
"""


@dataclass
class Result:
    project: Project
    outputs: list[Path] = field(default_factory=list)


def supported_extensions() -> list[str]:
    return sorted(READERS)


def load(path: str | Path) -> Project:
    path = Path(path)
    reader = READERS.get(path.suffix.lower())
    if reader is None:
        raise ValueError(
            f"Formato no compatible: '{path.suffix}'. Formatos admitidos: "
            + ", ".join(supported_extensions())
        )
    return reader(path)


def convert(source: str | Path, output_dir: str | Path, targets=TARGETS) -> Result:
    project = load(source)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    result = Result(project)
    base = output_dir / project.name

    has_audio = any(t.kind == TrackKind.AUDIO and t.audio_clips for t in project.tracks)
    has_midi = any(t.kind == TrackKind.MIDI for t in project.tracks)

    if "aaf" in targets and has_audio:
        path = base.with_suffix(".aaf")
        aaf.write(project, path)
        result.outputs.append(path)
    if "midi" in targets and has_midi:
        path = base.with_suffix(".mid")
        midi.write(project, path)
        result.outputs.append(path)

    report = output_dir / f"{project.name} - informe.txt"
    report.write_text(build_report(result), encoding="utf-8")
    result.outputs.append(report)
    return result


def summary(project: Project) -> str:
    tempo = project.tempo_map[0].bpm
    signature = project.time_signatures[0]
    lines = [
        f"Sesión: {project.name}",
        f"Tempo inicial: {tempo:g} BPM"
        + (f" + {_count(len(project.tempo_map) - 1, 'cambio', 'cambios')} de tempo" if len(project.tempo_map) > 1 else ""),
        f"Compás inicial: {signature.numerator}/{signature.denominator}",
        f"Pistas: {len(project.tracks)}",
    ]
    for track in project.tracks:
        details = [_KIND_LABELS[track.kind]]
        if track.audio_clips:
            details.append(_count(len(track.audio_clips), "clip de audio", "clips de audio"))
        if track.midi_clips:
            notes = sum(len(c.notes) for c in track.midi_clips)
            details.append(_count(len(track.midi_clips), "clip MIDI", "clips MIDI"))
            details.append(_count(notes, "nota", "notas"))
        if track.automation:
            details.append(
                "automatización de "
                + ", ".join(_PARAMETER_LABELS[a.parameter] for a in track.automation)
            )
        lines.append(f"  - {track.name} ({'; '.join(details)})")
    return "\n".join(lines)


def _count(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def build_report(result: Result) -> str:
    parts = [summary(result.project), ""]
    if result.outputs:
        parts.append("Archivos generados:")
        parts.extend(f"  - {p.name}" for p in result.outputs)
        parts.append("")
    if result.project.warnings:
        parts.append("Avisos:")
        parts.extend(f"  - {w}" for w in result.project.warnings)
        parts.append("")
    parts.append(IMPORT_HELP)
    return "\n".join(parts)
