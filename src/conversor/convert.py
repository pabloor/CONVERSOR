"""Punto de entrada común para la línea de comandos y la aplicación."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .audio import prepare
from .formats import aaf, ableton, midi
from .model import Project, TempoPoint, TrackKind

SOURCE_EXTENSIONS = (".aaf", ".als", ".mid", ".midi")

TARGETS = ("aaf", "midi", "ableton")
TARGET_LABELS = {
    "aaf": "AAF (Pro Tools, Logic, Cubase)",
    "midi": "MIDI (todos los DAW)",
    "ableton": "Ableton Live (.als)",
}

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
Ableton:     abre el .als de la carpeta "<sesión> Project". El audio está en
             Samples/Imported dentro de esa carpeta.
"""

HOW_TO_EXPORT_PRO_TOOLS = """Desde Pro Tools: Archivo > Exportar > Pistas seleccionadas como nuevo AAF/OMF
(formato WAV, «Incrustar audio» o «Copiar audio») y, si hay MIDI o cambios de
tempo, también Archivo > Exportar > MIDI. Abre los dos archivos a la vez.
"""


@dataclass
class Result:
    project: Project
    outputs: list[Path] = field(default_factory=list)
    rendered_clips: int = 0


def supported_extensions() -> list[str]:
    return list(SOURCE_EXTENSIONS)


def default_targets(sources) -> tuple[str, ...]:
    if any(Path(s).suffix.lower() == ".als" for s in sources):
        return ("aaf", "midi")
    return ("ableton",)


def load(sources, tempo: float | None = None) -> Project:
    """Lee una sesión de Ableton, o un AAF y/o un MIDI de la misma sesión."""
    paths = [Path(sources)] if isinstance(sources, (str, Path)) else [Path(s) for s in sources]
    if not paths:
        raise ValueError("No se ha indicado ninguna sesión")
    for path in paths:
        if path.suffix.lower() not in SOURCE_EXTENSIONS:
            raise ValueError(
                f"Formato no compatible: '{path.suffix}'. Formatos admitidos: "
                + ", ".join(SOURCE_EXTENSIONS)
            )
    kinds = [p.suffix.lower() for p in paths]
    if ".als" in kinds:
        if len(paths) > 1:
            raise ValueError("Una sesión de Ableton se convierte sola, sin otros archivos.")
        return ableton.read(paths[0])
    if kinds.count(".aaf") > 1:
        raise ValueError("Abre un solo AAF cada vez (puede acompañarse de un MIDI).")

    midis = [p for p in paths if p.suffix.lower() in (".mid", ".midi")]
    aafs = [p for p in paths if p.suffix.lower() == ".aaf"]
    project = Project(name=(aafs or midis)[0].stem)
    for path in midis:
        midi.read(path, project)
    if tempo:
        if midis:
            project.warn("Se usa el tempo del archivo MIDI; el tempo indicado se ignora.")
        else:
            project.tempo_map = [TempoPoint(0.0, float(tempo))]
    elif aafs and not midis:
        project.warn(
            "El AAF no incluye el tempo de la sesión: se usa 120 BPM. Exporta también "
            "un MIDI desde Pro Tools o indica el tempo para que la rejilla coincida."
        )
    for path in aafs:
        aaf.read(path, project)
    return project


def convert(
    sources,
    output_dir: str | Path,
    targets=None,
    template: str | Path | None = None,
    tempo: float | None = None,
) -> Result:
    paths = [Path(sources)] if isinstance(sources, (str, Path)) else list(sources)
    targets = targets or default_targets(paths)
    project = load(paths, tempo)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    result = Result(project)
    name = project.name

    has_audio = any(t.kind == TrackKind.AUDIO and t.audio_clips for t in project.tracks)
    has_midi = any(t.kind == TrackKind.MIDI for t in project.tracks)

    if "aaf" in targets and has_audio:
        path = output_dir / f"{name}.aaf"
        with tempfile.TemporaryDirectory(prefix="conversor-") as workdir:
            prepared = prepare(project, Path(workdir))
            aaf.write(project, path, prepared)
        result.rendered_clips = prepared.rendered
        result.outputs.append(path)
    if "aaf" in targets and has_midi and _from_ableton(paths):
        project.warn(
            "Las pistas MIDI se exportan como notas, sin el sonido de sus instrumentos. "
            "Para llevar también su audio, en Ableton usa «Congelar y aplanar» "
            "(Freeze and Flatten) en esas pistas antes de convertir."
        )
    if "midi" in targets and has_midi:
        path = output_dir / f"{name}.mid"
        midi.write(project, path)
        result.outputs.append(path)
    if "ableton" in targets and (has_audio or has_midi):
        path = output_dir / f"{name} Project" / f"{name}.als"
        ableton.write(project, path, template)
        result.outputs.append(path)
    if not result.outputs:
        project.warn("La sesión no tiene pistas que exportar a los formatos elegidos.")

    report = output_dir / f"{name} - informe.txt"
    report.write_text(build_report(result), encoding="utf-8")
    result.outputs.append(report)
    return result


def _from_ableton(paths) -> bool:
    return any(Path(p).suffix.lower() == ".als" for p in paths)


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
    if result.rendered_clips:
        parts.append(
            f"Se han procesado {_count(result.rendered_clips, 'clip', 'clips')} de audio con warp, "
            "transposición o Re-Pitch para que suenen como en Ableton."
        )
        parts.append("")
    if result.outputs:
        parts.append("Archivos generados:")
        parts.extend(f"  - {p.parent.name + '/' if p.suffix == '.als' else ''}{p.name}" for p in result.outputs)
        parts.append("")
    if result.project.warnings:
        parts.append("Avisos:")
        parts.extend(f"  - {w}" for w in result.project.warnings)
        parts.append("")
    parts.append(IMPORT_HELP)
    return "\n".join(parts)
