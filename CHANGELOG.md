# Cambios

## 0.4.1

- Corrige el error «CERTIFICATE_VERIFY_FAILED» al buscar actualizaciones: la
  aplicación usa ahora los certificados de macOS para conectar con GitHub.
- La autoprueba de cada versión comprueba la conexión con GitHub desde la
  aplicación ya empaquetada.

## 0.4.0

- Sonido con efectos: además del `.als`, se puede indicar la carpeta con las
  pistas exportadas desde Ableton («Todas las pistas individuales»). Cada
  pista llega a Logic procesada, con todos sus efectos e instrumentos, cortada
  en los mismos clips que en Ableton y con las colas de reverb y delay hasta
  el siguiente clip.
- Debajo de cada pista procesada va su versión limpia, con el volumen al
  mínimo (se puede desactivar).
- Las pistas MIDI llegan también como audio con el sonido de su instrumento;
  los retornos, como pistas propias; el master y los grupos, como referencia.

## 0.3.0

- Actualizaciones automáticas: al abrirse, la aplicación comprueba si hay una
  versión nueva en GitHub y pregunta si se quiere instalar. La descarga se
  comprueba con su SHA-256 antes de sustituir la aplicación.
- Menú Ayuda: «Buscar actualizaciones…», «Buscar actualizaciones al abrir» y
  página de versiones.

## 0.2.0

- Marcadores (locators) de Ableton en el MIDI: Logic los importa como marcadores.
- El informe lista los efectos e instrumentos de cada pista y del master, para
  rehacerlos en el otro DAW.
- Fundidos de los clips: aplicados en los WAV por pista y declarados en el AAF.
- Envolventes de los clips MIDI (pitch bend, presión, controladores) en el MIDI.
- Aviso de notas con probabilidad o variación de velocidad (Live 11/12).
- Opciones: frecuencia de muestreo, omitir pistas silenciadas y nombre del
  grupo delante del de cada pista.
- Barra de progreso, conversión de varias sesiones a la vez y apertura de
  sesiones soltándolas sobre el icono del Dock o con «Abrir con».
- Versiones publicadas en GitHub Releases.

## 0.1.0

- Ableton Live a AAF (con audio procesado como en Ableton: warp, bucles,
  transposición), MIDI y un WAV por pista, con informe e instrucciones para Logic.
- Pro Tools (AAF + MIDI) a Ableton Live.
- Aplicación para Mac con chip Apple.
