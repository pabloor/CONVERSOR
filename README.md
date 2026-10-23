# Conversor de sesiones entre DAWs

Aplicación de escritorio para pasar sesiones de un DAW a otro.

## Qué hace ahora

### De Ableton Live a Pro Tools, Logic, Cubase y FL Studio

**Origen:** Ableton Live (`.als`, versiones 10 a 12).

| Archivo | Contenido | Se abre en |
|---|---|---|
| `.aaf` | Pistas de audio con sus clips en su posición, el audio incrustado, el volumen y el panorama de cada pista (con su automatización) y la ganancia de cada clip | Pro Tools, Logic Pro, Cubase/Nuendo |
| `.mid` | Pistas MIDI con notas, tempo (incluidos los cambios), compás, volumen y panorama | Todos (también FL Studio) |
| `informe.txt` | Resumen de la sesión, avisos sobre lo que no se ha podido convertir y cómo importar en cada DAW | — |

Pro Tools, Logic y Cubase guardan sus sesiones en formatos cerrados que no se
pueden escribir directamente. AAF y MIDI son los formatos de intercambio que
todos ellos importan.

El objetivo es que el audio suene exactamente como en Ableton:

- **Clips sin cambios** (a la velocidad original del archivo): el AAF
  referencia el archivo completo. Conservan los márgenes para alargarlos en el
  otro DAW.
- **Clips con warp** que estiran el audio (por ejemplo, un loop de 100 BPM en
  una sesión a 128), con varios warp markers o con transposición: se procesan
  con [Rubber Band](https://breakfastquay.com/rubberband/), el mismo motor de
  estiramiento que usan otros DAW. En modo Re-Pitch, y en clips sin warp
  transpuestos, se cambia la velocidad como hace Ableton.
- **Clips en bucle**: cada vuelta se exporta como un clip.
- **AIFF, MP3, FLAC u otra frecuencia de muestreo**: se convierten a WAV de 24
  bits a la frecuencia de la sesión.
- **Clips desactivados**: no se exportan.

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
- No se convierten los efectos y los instrumentos (plugins). Para llevar el
  sonido de una pista MIDI o con efectos, usa en Ableton «Congelar y aplanar»
  (Freeze and Flatten) antes de convertir.
- No se convierten los fundidos de los clips ni las envolventes dentro de los
  clips. El informe avisa de los clips con fundidos.
- Los grupos y los retornos no se convierten, y el silencio y el solo de las
  pistas tampoco. El informe lo avisa.
- En los clips procesados con Rubber Band, los golpes pueden adelantarse unos
  milisegundos, como en cualquier estiramiento de tiempo.
- Rubber Band (`pylibrb`) necesita Python 3.12 o posterior y no tiene versión
  para Mac con Intel. Sin él, los clips estirados se exportan sin estirar y el
  informe lo avisa.
- Falta comprobarlo con sesiones reales en Pro Tools, Logic y Cubase,
  sobre todo las pistas estéreo y la automatización en Pro Tools.
- Pro Tools → Ableton: los fundidos y los crossfades no se convierten. El audio en MXF
  (exportación «compatible con Avid») no lo abre Ableton; exporta en WAV.

## Instalación

Hace falta Python 3.12 o posterior (3.10 funciona, pero sin Rubber Band).

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
- `src/conversor/audio.py`: prepara el audio de cada clip (warp, bucles, transposición, conversión de formato).
- `src/conversor/convert.py`: une lectores y escritores y genera el informe.
- `src/conversor/gui.py`: aplicación de escritorio (PySide6).
- `src/conversor/cli.py`: línea de comandos.

Para añadir un formato nuevo, crea un lector o escritor que trabaje con el
modelo de `model.py` y regístralo en `convert.py`.
