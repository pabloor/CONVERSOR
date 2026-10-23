# Conversor de sesiones entre DAWs

Aplicación de escritorio para pasar sesiones de un DAW a otro.

## Qué hace ahora

### De Ableton Live a Pro Tools, Logic, Cubase y FL Studio

**Origen:** Ableton Live (`.als`, versiones 10 a 12).

| Archivo | Contenido | Se abre en |
|---|---|---|
| `.aaf` | Pistas de audio con sus clips colocados en la línea de tiempo y el audio incrustado | Pro Tools, Logic Pro, Cubase/Nuendo |
| `.mid` | Pistas MIDI con notas, tempo (incluidos los cambios), compás, volumen y panorama | Todos (también FL Studio) |
| `informe.txt` | Resumen de la sesión, avisos sobre lo que no se ha podido convertir y cómo importar en cada DAW | — |

Pro Tools, Logic y Cubase guardan sus sesiones en formatos cerrados que no se
pueden escribir directamente. AAF y MIDI son los formatos de intercambio que
todos ellos importan.

### De Pro Tools a Ableton Live

**Origen:** el AAF exportado desde Pro Tools y, opcionalmente, el MIDI de la
misma sesión. Ábrelos juntos.

1. En Pro Tools, selecciona las pistas y usa *Archivo > Exportar > Pistas
   seleccionadas como nuevo AAF/OMF*, con formato WAV y el audio incrustado o
   copiado.
2. Si la sesión tiene pistas MIDI o cambios de tempo, usa también *Archivo >
   Exportar > MIDI*. El AAF no guarda el tempo, así que sin el MIDI hay que
   indicarlo a mano (si no, se usa 120 BPM).

**Resultado:** una carpeta `<sesión> Project` con el `.als` y el audio copiado
en `Samples/Imported`. Incluye:

- Pistas de audio con sus clips en su posición, el desplazamiento dentro del archivo y la ganancia de clip.
- Pistas estéreo: Pro Tools exporta un archivo mono por canal y el conversor los une en un WAV estéreo.
- Volumen de pista y su automatización, cuando vienen en el AAF.
- Pistas MIDI con sus notas, el tempo (incluidos los cambios) y el compás.

El set se crea a partir de una plantilla. La incluida usa el formato de Live 11,
que también abre Live 12. Si tu versión de Ableton no la acepta, guarda un set
vacío con una pista de audio y otra MIDI y úsalo como plantilla (opción
*Plantilla* en la aplicación o `--plantilla` en la línea de comandos). Así
también conservas tu configuración: retornos, dispositivos de la pista MIDI,
etc.

### Limitaciones conocidas

- Ableton: solo se leen los clips del arreglo (Arrangement), no los de la vista Session.
- El AAF solo incrusta audio WAV PCM. Los AIFF, MP3 o FLAC hay que convertirlos antes a WAV.
- Los clips de audio estirados con warp o repetidos en bucle se exportan sin
  estirar y sin repetir. Consolídalos en Ableton (Ctrl+J / Cmd+J) antes de convertir.
- La mezcla y la automatización de las pistas de audio todavía no se escriben en
  el AAF. En las pistas MIDI se exportan como CC7 (volumen) y CC10 (panorama).
- Los grupos, los retornos y los plugins no se convierten.
- Pro Tools: los fundidos y los crossfades no se convierten. El audio en MXF
  (exportación «compatible con Avid») no lo abre Ableton; exporta en WAV.

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

Arrastra a la ventana la sesión `.als`, o el `.aaf` y el `.mid` de Pro Tools a
la vez. Después elige los formatos y pulsa **Convertir**.
Por defecto el resultado se guarda en una carpeta `<sesión> (convertido)` junto
a la sesión.

Línea de comandos:

```bash
conversor "Mi canción.als" -o salida/
conversor "Mi canción.als" --formato midi
conversor Cancion.aaf Cancion.mid                # Pro Tools -> Ableton
conversor Cancion.aaf --tempo 92 --plantilla "Vacío.als"
```

## Desarrollo

```bash
pip install -e ".[dev]"
pytest
```

Estructura:

- `src/conversor/model.py`: modelo intermedio (pistas, clips, notas, tempo, automatización).
- `src/conversor/formats/`: un módulo por formato, cada uno con `read` y/o `write` (`ableton.py`, `aaf.py`, `midi.py`).
- `src/conversor/templates/`: plantilla de set de Ableton usada al escribir `.als`.
- `src/conversor/convert.py`: une lectores y escritores y genera el informe.
- `src/conversor/gui.py`: aplicación de escritorio (PySide6).
- `src/conversor/cli.py`: línea de comandos.

Para añadir un formato nuevo, crea un lector o escritor que trabaje con el
modelo de `model.py` y regístralo en `convert.py`.
