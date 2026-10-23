"""Uso: conversor SESION [SESION...] [-o CARPETA] [--formato aaf|midi|ableton]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .convert import TARGETS, build_report, convert


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="conversor",
        description=(
            "Convierte sesiones entre DAWs. Ableton (.als) se exporta a AAF y MIDI; "
            "un AAF de Pro Tools (con su MIDI opcional) se exporta a Ableton."
        ),
    )
    parser.add_argument(
        "sessions", type=Path, nargs="+", metavar="sesion",
        help="sesión de origen: .als, o .aaf y/o .mid exportados de la misma sesión",
    )
    parser.add_argument(
        "-o", "--output", type=Path, help="carpeta de destino (por defecto, junto a la sesión)"
    )
    parser.add_argument(
        "-f", "--formato", choices=TARGETS, action="append",
        help="formato de salida; se puede repetir (por defecto, según el origen)",
    )
    parser.add_argument("--tempo", type=float, help="tempo en BPM si el origen no lo incluye (AAF sin MIDI)")
    parser.add_argument("--plantilla", type=Path, help="set vacío de Ableton para usar como plantilla")
    args = parser.parse_args(argv)

    first = args.sessions[0]
    output = args.output or first.parent / f"{first.stem} (convertido)"
    try:
        result = convert(args.sessions, output, args.formato, args.plantilla, args.tempo)
    except (OSError, ValueError, SyntaxError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    print(build_report(result))
    print(f"Resultado en: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
