"""Punto de entrada de la aplicación empaquetada (PyInstaller)."""

import sys

if __name__ == "__main__":
    if "--autoprueba" in sys.argv:
        from conversor.selftest import main
    else:
        from conversor.gui import main
    sys.exit(main())
