# Conversor de sesiones entre DAWs

Aplicación de escritorio para pasar sesiones de un DAW a otro.

## Qué hace ahora

**Origen:** Ableton Live (`.als`, versiones 10 a 12).

**Destino:**

| Archivo | Contenido | Se abre en |
|---|---|---|
| `.aaf` | Pistas de audio con sus clips colocados en la línea de tiempo y el audio incrustado | Pro Tools, Logic Pro, Cubase/Nuendo |
| `.mid` | Pistas MIDI con notas, tempo (incluidos los cambios), compás, volumen y panorama | Todos (también FL Studio) |
| `informe.txt` | Resumen de la sesión, avisos sobre lo que no se ha podido convertir y cómo importar en cada DAW | — |

Pro Tools, Logic y Cubase guardan sus sesiones en formatos cerrados que no se
pueden escribir directamente. AAF y MIDI son los formatos de intercambio que
todos ellos importan.

### Limitaciones conocidas

- Solo se leen los clips del arreglo (Arrangement), no los de la vista Session.
- El AAF solo incrusta audio WAV PCM. Los AIFF, MP3 o FLAC hay que convertirlos antes a WAV.
- Los clips de audio estirados con warp o repetidos en bucle se exportan sin
  estirar y sin repetir. Consolídalos en Ableton (Ctrl+J / Cmd+J) antes de convertir.
- La mezcla y la automatización de las pistas de audio todavía no se escriben en
  el AAF. En las pistas MIDI se exportan como CC7 (volumen) y CC10 (panorama).
- Los grupos, los retornos y los plugins no se convierten.

## Instalación

Hace falta Python 3.10 o posterior.

```bash
pip install -e .
```

## Uso

Aplicación de escritorio:

```bash
conversor-gui
```

Arrastra la sesión `.als` a la ventana, elige los formatos y pulsa **Convertir**.
Por defecto el resultado se guarda en una carpeta `<sesión> (convertido)` junto
a la sesión.

Línea de comandos:

```bash
conversor "Mi canción.als" -o salida/
conversor "Mi canción.als" --solo midi
```

## Desarrollo

```bash
pip install -e ".[dev]"
pytest
```

Estructura:

- `src/conversor/model.py`: modelo intermedio (pistas, clips, notas, tempo, automatización).
- `src/conversor/formats/`: un módulo por formato (`ableton.py` lee; `aaf.py` y `midi.py` escriben).
- `src/conversor/convert.py`: une lectores y escritores y genera el informe.
- `src/conversor/gui.py`: aplicación de escritorio (PySide6).
- `src/conversor/cli.py`: línea de comandos.

Para añadir un formato nuevo, crea un lector o escritor que trabaje con el
modelo de `model.py` y regístralo en `convert.py`.
