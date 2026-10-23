"""Uso: conversor SESION [-o CARPETA] [--solo aaf|midi]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .convert import TARGETS, build_report, convert


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="conversor",
        description="Convierte una sesión de DAW a AAF (audio) y MIDI (notas, tempo).",
    )
    parser.add_argument("session", type=Path, help="sesión de origen (.als)")
    parser.add_argument(
        "-o", "--output", type=Path, help="carpeta de destino (por defecto, junto a la sesión)"
    )
    parser.add_argument("--solo", choices=TARGETS, help="genera solo este formato")
    args = parser.parse_args(argv)

    output = args.output or args.session.parent / f"{args.session.stem} (convertido)"
    targets = (args.solo,) if args.solo else TARGETS
    try:
        result = convert(args.session, output, targets)
    except (OSError, ValueError, SyntaxError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    print(build_report(result))
    print(f"Resultado en: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
