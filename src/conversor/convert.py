"""Punto de entrada común para la línea de comandos y la aplicación."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import rendered as rendered_tracks
from .audio import prepare, write_stems
from .formats import aaf, ableton, midi
from .model import Project, TempoPoint, TrackKind

SOURCE_EXTENSIONS = (".aaf", ".als", ".mid", ".midi")

TARGETS = ("aaf", "midi", "stems", "ableton")
TARGET_LABELS = {
    "aaf": "AAF: pistas de audio con sus clips (Logic, Pro Tools, Cubase)",
    "midi": "MIDI: notas, tempo y compás (todos los DAW)",
    "stems": "Un WAV por pista de audio, desde el compás 1",
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
Cómo abrirlo en Logic Pro
-------------------------
1. Archivo > Abrir y elige el .mid. Se crea un proyecto con el tempo, el
   compás y las pistas MIDI (con instrumentos por defecto).
2. Archivo > Importar > Archivo AAF y elige el .aaf. Se añaden las pistas de
   audio con sus clips, su volumen y su panorama.
3. Si el audio no queda en su sitio, usa la carpeta «pistas»: selecciona
   todos los WAV, arrástralos al compás 1 y elige «Crear pistas nuevas».
4. Ajusta los volúmenes de las pistas MIDI con la tabla de mezcla de este informe.

Otros DAW
---------
Pro Tools:   Archivo > Importar > Datos de sesión con el .aaf;
             Archivo > Importar > MIDI con el .mid.
Cubase:      Archivo > Importar > Archivo AAF con el .aaf;
             Archivo > Importar > Archivo MIDI con el .mid.
FL Studio:   no admite AAF. Importa el .mid y arrastra los WAV de la carpeta
             «pistas» al inicio de la lista de reproducción.
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
    rendered_set: rendered_tracks.RenderedSet | None = None


def supported_extensions() -> list[str]:
    return list(SOURCE_EXTENSIONS)


def default_targets(sources) -> tuple[str, ...]:
    if any(Path(s).suffix.lower() == ".als" for s in sources):
        return ("aaf", "midi", "stems")
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
    *,
    sample_rate: int | None = None,
    skip_muted: bool = False,
    group_prefix: bool = True,
    rendered: str | Path | None = None,
    keep_clean: bool = True,
    render_start_bar: float = 1,
    progress=None,
) -> Result:
    """Convierte una sesión.

    - sample_rate: frecuencia del audio exportado (por defecto, la más habitual en la sesión).
    - skip_muted: no exportar las pistas silenciadas.
    - group_prefix: anteponer el nombre del grupo al de cada pista ("Batería - Bombo").
    - rendered: carpeta con las pistas exportadas desde Ableton (todas las pistas
      individuales), para llevar el sonido con sus efectos. keep_clean mantiene
      además la versión limpia de cada pista de audio, con el volumen al mínimo;
      render_start_bar es el compás en el que empieza la exportación.
    - progress(fracción, texto): informa del avance.
    """
    report_progress = progress or (lambda fraction, text: None)
    paths = [Path(sources)] if isinstance(sources, (str, Path)) else list(sources)
    targets = targets or default_targets(paths)
    report_progress(0.0, "Leyendo la sesión")
    project = load(paths, tempo)
    rendered_set = None
    if rendered and _from_ableton(paths):
        # Antes de cambiar los nombres: los archivos llevan el nombre original de cada pista.
        rendered_set = rendered_tracks.match(Path(rendered), project)
        start_beat = _bar_to_beat(project, render_start_bar)
        rendered_tracks.apply(project, rendered_set, keep_clean, start_beat)
    if _from_ableton(paths):
        _apply_options(project, skip_muted, group_prefix)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    result = Result(project, rendered_set=rendered_set)
    name = project.name

    has_audio = any(t.kind == TrackKind.AUDIO and t.audio_clips for t in project.tracks)
    has_midi = any(t.kind == TrackKind.MIDI for t in project.tracks)

    if has_audio and ("aaf" in targets or "stems" in targets):
        with tempfile.TemporaryDirectory(prefix="conversor-") as workdir:
            prepared = prepare(project, Path(workdir), sample_rate, _stage(report_progress, 0.05, 0.7))
            result.rendered_clips = prepared.rendered
            if "aaf" in targets:
                path = output_dir / f"{name}.aaf"
                aaf.write(project, path, prepared, _stage(report_progress, 0.7, 0.85))
                result.outputs.append(path)
            if "stems" in targets:
                folder = output_dir / f"{name} - pistas"
                write_stems(prepared, folder, _stage(report_progress, 0.85, 0.95))
                result.outputs.append(folder)
    midi_without_audio = [
        t for t in project.tracks
        if t.kind == TrackKind.MIDI and not any(o.name == t.name + rendered_tracks.AUDIO_SUFFIX for o in project.tracks)
    ]
    if rendered_set is not None and rendered_set.references:
        folder = output_dir / f"{name} - referencias"
        rendered_tracks.copy_references(rendered_set, folder)
        result.outputs.append(folder)
    if "aaf" in targets and midi_without_audio and _from_ableton(paths):
        project.warn(
            "Las pistas MIDI se exportan como notas, sin el sonido de sus instrumentos. "
            "Para llevar también su audio con todos sus efectos, exporta en Ableton "
            "«Todas las pistas individuales» y añade esa carpeta al convertir."
        )
    report_progress(0.95, "Escribiendo MIDI e informe")
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
    report_progress(1.0, "Terminado")
    return result


def convert_many(sessions, output_root: str | Path | None = None, progress=None, **options) -> list[tuple[Path, Result | Exception]]:
    """Convierte varias sesiones de Ableton, cada una en su carpeta.

    Sin output_root, cada resultado va junto a su sesión en "<sesión> (convertido)";
    con output_root, en una subcarpeta con el nombre de la sesión.
    """
    results: list[tuple[Path, Result | Exception]] = []
    sessions = [Path(s) for s in sessions]
    for index, session in enumerate(sessions):
        folder = Path(output_root) / session.stem if output_root else default_output(session)
        stage = _stage(progress or (lambda f, t: None), index / len(sessions), (index + 1) / len(sessions))
        named = lambda fraction, text, session=session, stage=stage: stage(fraction, f"{session.stem}: {text}")  # noqa: E731
        try:
            results.append((session, convert(session, folder, progress=named, **options)))
        except (OSError, ValueError, SyntaxError) as error:
            results.append((session, error))
    return results


def _bar_to_beat(project: Project, bar: float) -> float:
    """Compás (empezando en 1) a tiempos, con el compás inicial de la sesión."""
    signature = project.time_signatures[0]
    return (bar - 1) * signature.numerator * 4 / signature.denominator


def default_output(session: Path) -> Path:
    return session.parent / f"{session.stem} (convertido)"


def _stage(progress, start: float, end: float):
    """Pasa el avance de una etapa (0..1) al tramo [start, end] del total."""
    return lambda fraction, text: progress(start + (end - start) * min(max(fraction, 0.0), 1.0), text)


def _apply_options(project: Project, skip_muted: bool, group_prefix: bool) -> None:
    if group_prefix:
        groups = {t.name: t for t in project.tracks if t.kind == TrackKind.GROUP}
        for track in project.tracks:
            chain, parent, seen = [], track.group, set()
            while parent and parent not in seen:
                seen.add(parent)
                chain.insert(0, parent)
                parent = groups[parent].group if parent in groups else None
            if chain and track.kind in (TrackKind.AUDIO, TrackKind.MIDI):
                track.name = " - ".join(chain + [track.name])
    exported = [t for t in project.tracks if t.kind in (TrackKind.AUDIO, TrackKind.MIDI)]
    muted = [t for t in exported if t.mute]
    if muted and skip_muted:
        project.tracks = [t for t in project.tracks if t not in muted]
        exported = [t for t in exported if t not in muted]
        project.warn(
            f"No se exportan {_count(len(muted), 'pista silenciada', 'pistas silenciadas')}: "
            + ", ".join(t.name for t in muted)
        )
    for track in exported:
        if track.mute:
            project.warn(
                f"La pista '{track.name}' está silenciada en Ableton; se exporta igualmente "
                "(puedes omitir las pistas silenciadas en las opciones)."
            )
        if track.solo:
            project.warn(f"La pista '{track.name}' tiene el solo activado en Ableton; el solo no se exporta.")
        if any(a.parameter == "mute" for a in track.automation):
            project.warn(f"La automatización de silencio de la pista '{track.name}' no se exporta.")


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
    if project.markers:
        lines.insert(3, f"Marcadores: {len(project.markers)}")
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


def devices_table(project: Project) -> str:
    """Efectos e instrumentos de cada pista, para rehacerlos en el otro DAW."""
    rows = [(t.name, t.devices) for t in project.tracks if t.devices]
    if project.master_devices:
        rows.append(("Master", project.master_devices))
    if not rows:
        return ""
    lines = [
        "Efectos e instrumentos (no se convierten: rehazlos en el otro DAW)",
        "------------------------------------------------------------------",
    ]
    lines.extend(f"{name}: {' → '.join(devices)}" for name, devices in rows)
    return "\n".join(lines)


def rendered_section(rendered_set) -> str:
    if rendered_set is None:
        return ""
    lines = ["Pistas renderizadas en Ableton (con efectos)", "--------------------------------------------"]
    for track, file in rendered_set.matched.items():
        lines.append(f"{track} ← {file.name}")
    if rendered_set.master:
        lines.append(f"Master ← {rendered_set.master.name} (solo como referencia)")
    if rendered_set.unmatched:
        lines.append("Sin pista correspondiente (no se usan): " + ", ".join(p.name for p in rendered_set.unmatched))
    if not rendered_set.matched:
        lines.append(
            "No se ha reconocido ningún archivo. Exporta desde la sesión con «Todas las pistas "
            "individuales» sin cambiar los nombres de los archivos."
        )
    return "\n".join(lines)


def mix_table(project: Project) -> str:
    """Volumen, panorama y estado de cada pista, para comprobarlos o ajustarlos a mano."""
    rows = []
    for track in project.tracks:
        if track.kind not in (TrackKind.AUDIO, TrackKind.MIDI):
            continue
        volume = "-inf dB" if track.volume_db == float("-inf") else f"{track.volume_db:+.1f} dB"
        if abs(track.pan) < 0.005:
            pan = "C"
        else:
            pan = f"{abs(track.pan) * 100:.0f}{'L' if track.pan < 0 else 'R'}"
        notes = []
        if track.mute:
            notes.append("silenciada")
        if track.solo:
            notes.append("solo")
        if any(a.parameter in ("volume", "pan") for a in track.automation):
            notes.append("con automatización" + (" (no exportada)" if track.kind == TrackKind.MIDI else ""))
        rows.append((track.name, _KIND_LABELS[track.kind], volume, pan, ", ".join(notes)))
    if not rows:
        return ""
    width = max(len(r[0]) for r in rows)
    lines = ["Mezcla de la sesión original", "-----------------------------"]
    lines.append(f"{'Pista':<{width}}  {'Tipo':<5}  {'Volumen':>8}  {'Pan':>4}")
    for name, kind, volume, pan, notes in rows:
        lines.append(f"{name:<{width}}  {kind:<5}  {volume:>8}  {pan:>4}  {notes}".rstrip())
    return "\n".join(lines)


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
        for p in result.outputs:
            if p.is_dir():
                parts.append(f"  - {p.name}/ ({len(list(p.glob('*.wav')))} WAV)")
            else:
                parts.append(f"  - {p.parent.name + '/' if p.suffix == '.als' else ''}{p.name}")
        parts.append("")
    if result.project.warnings:
        parts.append("Avisos:")
        parts.extend(f"  - {w}" for w in result.project.warnings)
        parts.append("")
    for table in (rendered_section(result.rendered_set), mix_table(result.project), devices_table(result.project)):
        if table:
            parts.extend([table, ""])
    parts.append(IMPORT_HELP)
    return "\n".join(parts)
