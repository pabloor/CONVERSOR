# Conversor de sesiones entre DAWs

Aplicación de escritorio para pasar sesiones de un DAW a otro.

## Qué hace ahora

### De Ableton Live a Pro Tools, Logic, Cubase y FL Studio

**Origen:** Ableton Live (`.als`, versiones 10 a 12).

| Archivo | Contenido | Se abre en |
|---|---|---|
| `.aaf` | Pistas de audio con sus clips en su posición, el audio incrustado, el volumen y el panorama de cada pista (con su automatización) y la ganancia de cada clip | Pro Tools, Logic Pro, Cubase/Nuendo |
| `.mid` | Pistas MIDI con notas, envolventes de los clips (pitch bend, presión, controladores), panorama, tempo (incluidos los cambios), compás y marcadores | Todos (también FL Studio) |
| `<sesión> - pistas/` | Un WAV por pista de audio que empieza en el inicio de la sesión, con los clips ya colocados, procesados y con sus fundidos | Todos: se arrastran al compás 1 |
| `informe.txt` | Resumen de la sesión, avisos sobre lo que no se ha podido convertir, tabla de mezcla (volumen y panorama de cada pista), efectos e instrumentos de cada pista y cómo importar en cada DAW | — |

Opciones (en la aplicación y en la línea de comandos):

- **Frecuencia de muestreo** del audio exportado, para que coincida con el
  proyecto de Logic. Por defecto, la más habitual entre los archivos de la sesión.
- **Omitir las pistas silenciadas.**
- **Nombre del grupo delante del de cada pista** («Batería - Bombo»), para
  rehacer los Track Stacks en Logic. Activada por defecto.

Se pueden convertir varias sesiones a la vez: cada una va a su propia carpeta.

#### Sonido con efectos

El `.als` guarda qué efectos e instrumentos hay, pero no el sonido que
producen, y los dispositivos de Ableton solo funcionan dentro de Ableton. Para
llevar el sonido con todos sus efectos:

1. En Ableton: *Archivo > Exportar audio/vídeo*, «Pistas renderizadas: Todas
   las pistas individuales», empezando en el compás 1. No modifica la sesión.
2. En el conversor, elige esa carpeta en «Sonido con efectos» (o
   `--renderizadas CARPETA`).

Resultado en el AAF y en los WAV por pista:

- Cada pista de audio, **procesada** y cortada en los mismos clips que en
  Ableton, con las colas de reverb y delay hasta el siguiente clip. Va a 0 dB y
  al centro, porque la exportación de Ableton ya incluye su volumen y panorama.
- Debajo, su versión **limpia** con el volumen al mínimo, por si quieres rehacer
  la mezcla (`--sin-limpias` para no incluirla).
- Cada pista MIDI, también como audio («Synth (audio)») con el sonido de su
  instrumento; sus notas siguen en el `.mid`.
- Los retornos, como pistas propias.
- El master y los grupos, solo como referencia en la carpeta `referencias`:
  ya contienen el audio de sus pistas y sonarían dos veces. Los efectos de un
  grupo no están en las pistas procesadas de sus miembros (el informe lo avisa).

Cada archivo exportado se relaciona con su pista por el nombre; el informe
indica los que no encajan. Si la exportación no empieza en el compás 1, indícalo
en «La exportación empieza en el compás» (`--inicio-render`).

Pro Tools, Logic y Cubase guardan sus sesiones en formatos cerrados que no se
pueden escribir directamente. AAF y MIDI son los formatos de intercambio que
todos ellos importan.

#### En Logic Pro

1. *Archivo > Abrir* y elige el `.mid`: se crea un proyecto con el tempo, el
   compás y las pistas MIDI.
2. *Archivo > Importar > Archivo AAF* y elige el `.aaf`: se añaden las pistas
   de audio.
3. Si algo falla con el AAF, arrastra los WAV de la carpeta `pistas` al
   compás 1 y elige *Crear pistas nuevas*.

El AAF empieza en el código de tiempo 01:00:00:00, que es donde empiezan por
defecto los proyectos de Logic. El MIDI no incluye el volumen (CC7), porque
Logic lo interpreta con otra curva y las pistas sonarían más fuertes. Los
volúmenes están en la tabla de mezcla del informe.

#### Fidelidad del audio

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
- **Fundidos de los clips**: se aplican en los WAV por pista y se declaran en
  el AAF (propiedades estándar de fundido, que no todos los DAW leen).

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
- Sin la carpeta de pistas exportadas, el audio sale sin efectos y las pistas
  MIDI solo como notas (ver «Sonido con efectos»).
- Con las pistas exportadas, se supone que la exportación individual de
  Ableton incluye el volumen y el panorama de cada pista; falta confirmarlo con
  una sesión real.
- No se convierten las envolventes de los clips de audio (volumen, transposición
  dentro del clip).
- Los grupos y los retornos no se convierten, y el solo de las pistas tampoco.
  El informe lo avisa.
- Las notas con probabilidad o variación de velocidad se exportan todas, con
  su velocidad base. El informe lo avisa.
- Falta confirmar con sesiones reales las unidades de los fundidos (tiempos en
  clips con warp, segundos sin warp) y la numeración de los controladores MIDI
  de las envolventes.
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

### Aplicación para Mac (chip Apple)

1. Descarga la última versión:
   https://github.com/pabloor/CONVERSOR/releases/latest/download/Conversor.dmg
   (las versiones y sus cambios están en la página *Releases* del repositorio).
2. Abre `Conversor.dmg` y arrastra **Conversor** a la carpeta Aplicaciones.
3. La primera vez, macOS la bloquea porque no está firmada por Apple. Ábrela
   con doble clic, cierra el aviso y ve a *Ajustes del Sistema > Privacidad y
   seguridad*. Abajo aparece «Se ha bloqueado Conversor»: pulsa *Abrir
   igualmente* y confirma. Solo hace falta una vez.

Para abrir una sesión, arrástrala a la ventana o al icono del Dock, o usa
*Abrir con > Conversor* en el Finder.

**Actualizaciones:** al abrirse, la aplicación comprueba si hay una versión
nueva en GitHub y pregunta si quieres instalarla (también desde *Ayuda >
Buscar actualizaciones…*). Descarga el `.dmg`, comprueba su huella SHA-256
con la que publica GitHub, sustituye la aplicación y la vuelve a abrir. Se
puede desactivar en *Ayuda > Buscar actualizaciones al abrir*. Si Conversor no
está en una carpeta en la que puedas escribir, abre la página de descarga.

Para publicar una versión nueva: sube la versión en `pyproject.toml` y
`src/conversor/__init__.py` y añade sus cambios a `CHANGELOG.md`. Después, en
la pestaña *Actions*, elige «Aplicación para Mac», pulsa *Run workflow*, marca
«Publicar» y lánzalo (o sube una etiqueta `vX.Y.Z`). GitHub Actions compila la
aplicación, crea la etiqueta y el Release con el `.dmg`.
Cada cambio subido a cualquier rama también deja el `.dmg` en la pestaña
*Actions* (artefacto `Conversor-mac`).

Para compilarla en tu propio Mac: crea un entorno con el proyecto y PyInstaller
(`pip install -e . "pyinstaller>=6.10"`) y ejecuta `packaging/build_mac.sh`.
El resultado queda en `dist/`.

### Desde el código fuente

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
conversor *.als --frecuencia 48000 --omitir-silenciadas   # varias sesiones
conversor "Mi canción.als" --renderizadas "Mi canción exportada/"   # con efectos
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
