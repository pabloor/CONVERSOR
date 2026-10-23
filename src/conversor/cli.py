"""Uso: conversor SESION [SESION...] [-o CARPETA] [--formato aaf|midi|stems|ableton]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .convert import TARGETS, build_report, convert, convert_many, default_output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="conversor",
        description=(
            "Convierte sesiones entre DAWs. Ableton (.als) se exporta a AAF, MIDI y un WAV "
            "por pista; un AAF de Pro Tools (con su MIDI opcional) se exporta a Ableton. "
            "Con varias sesiones .als, se convierte cada una en su carpeta."
        ),
    )
    parser.add_argument(
        "sessions", type=Path, nargs="+", metavar="sesion",
        help="sesión de origen: .als (una o varias), o .aaf y/o .mid exportados de la misma sesión",
    )
    parser.add_argument(
        "-o", "--output", type=Path, help="carpeta de destino (por defecto, junto a la sesión)"
    )
    parser.add_argument(
        "-f", "--formato", choices=TARGETS, action="append",
        help="formato de salida; se puede repetir (por defecto, según el origen)",
    )
    parser.add_argument("--frecuencia", type=int, help="frecuencia de muestreo del audio exportado (p. ej. 44100 o 48000)")
    parser.add_argument("--omitir-silenciadas", action="store_true", help="no exportar las pistas silenciadas")
    parser.add_argument("--sin-grupo", action="store_true", help="no anteponer el nombre del grupo al de cada pista")
    parser.add_argument("--tempo", type=float, help="tempo en BPM si el origen no lo incluye (AAF sin MIDI)")
    parser.add_argument("--plantilla", type=Path, help="set vacío de Ableton para usar como plantilla")
    args = parser.parse_args(argv)

    options = dict(
        targets=args.formato,
        template=args.plantilla,
        tempo=args.tempo,
        sample_rate=args.frecuencia,
        skip_muted=args.omitir_silenciadas,
        group_prefix=not args.sin_grupo,
    )
    sessions = args.sessions
    if len(sessions) > 1 and all(s.suffix.lower() == ".als" for s in sessions):
        failed = 0
        for session, result in convert_many(sessions, args.output, **options):
            if isinstance(result, Exception):
                failed += 1
                print(f"Error en {session.name}: {result}", file=sys.stderr)
            else:
                folder = args.output / session.stem if args.output else default_output(session)
                print(f"{session.name}: {len(result.project.tracks)} pistas -> {folder}")
        return 1 if failed else 0

    output = args.output or default_output(sessions[0])
    try:
        result = convert(sessions, output, **options)
    except (OSError, ValueError, SyntaxError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    print(build_report(result))
    print(f"Resultado en: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
