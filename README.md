# Anonimizador reversible local

## Instalador de Windows

Descargue `EnigmaLex-Setup-<versión>.exe` desde [Releases](https://github.com/Telecampux/EnigmaLex/releases). El instalador incluye Python, las bibliotecas, Tesseract OCR y los modelos español e inglés. Crea un acceso directo **EnigmaLex** en el menú Inicio y, por defecto, en el escritorio con el icono de la aplicación. Los documentos se guardan por defecto en `Documentos\EnigmaLex`, fuera de la carpeta del programa.

Para compilar una versión nueva en Windows con Python 3.13, Tesseract OCR de UB Mannheim e Inno Setup 6 instalados, ejecute `powershell -ExecutionPolicy Bypass -File .\Instalador\compilar.ps1`.

Las carpetas iniciales están junto al programa: `ORIGINALES`, `ANONIMIZADOS` y `PROVIDENCIAS`. En **Configurar carpetas…** puede elegir otras ubicaciones para este usuario. La elección se guarda localmente en `%LOCALAPPDATA%\Anonimizador\carpetas.json`. La aplicación comprueba que las carpetas estén disponibles y que pueda escribir en las dos de salida antes de procesar.

Aplicación de escritorio en español para Windows, con selección de varios archivos. No sube documentos a servicios externos. Necesita Python 3.11 o posterior e internet únicamente para instalar dependencias.

## Iniciar

Haga doble clic en **iniciar.bat**. La primera ejecución crea un entorno virtual e instala las dependencias. También puede ejecutar:

```powershell
python -m pip install -r requirements.txt
powershell -NoProfile -ExecutionPolicy Bypass -File .\instalar_ocr.ps1
python app.py
```

1. Seleccione **Anonimizar archivos** y agregue documentos.
2. Si desea incluir otros datos o variables distintos de nombres, identificaciones y correos, escríbalos en el cuadro, uno por línea. Se buscan sin distinguir mayúsculas; incluya las variantes que también deban ocultarse.
3. La detección automática propone nombres propios de personas, identificaciones y correos electrónicos. Reconoce nombres junto a etiquetas de persona y formulario, e identificaciones junto a sus etiquetas. También propone correos electrónicos; no propone teléfonos, direcciones ni IP. Son reglas heurísticas: revise los resultados. En la revisión puede seleccionar si se limpian los metadatos de la copia para compartir; la copia `.restauracion` conserva el original íntegro y debe permanecer privada.
4. Elija la carpeta de salida y pulse **Anonimizar**. El avance muestra únicamente los nombres e identificaciones procesados, agrupados por página. En esta etapa no se guardan resultados ni copias de recuperación.
5. La ventana de revisión muestra primero los **metadatos** encontrados en el original (por ejemplo, autor, propiedades Office, EXIF y fechas internas del contenedor). También aparecen al inicio de la lista de propuestas en azul, marcados por defecto; su casilla controla la limpieza de los metadatos del archivo. La pestaña Metadatos muestra la lista completa. Después se muestra cada nombre, identificación o correo, su contexto y su ubicación. Los nombres de personas, nombres en formularios, profesionales e identificaciones detectados quedan marcados automáticamente, incluidos los nombres o identificaciones añadidos manualmente. En la pestaña visual los datos marcados se muestran con códigos; desmarcarlos permite volver a verlos en la vista previa.
6. Revise los nombres e identificaciones marcados automáticamente y desmarque los que desea conservar. Puede autorizar una aparición y conservar otra igual.
7. Si falta un nombre o identificación ilegible, puede dibujar su zona en la pestaña visual y autorizarla en la revisión.
8. Pulse **Autorizar seleccionados y generar** para guardar. Cancelar la revisión no genera ningún archivo. Si no hay coincidencias, se muestra el texto reconocido y se puede autorizar explícitamente una copia sin coincidencias o el sustituto indicado en los avisos.
9. Revise el resultado antes de compartirlo. Cero coincidencias no significa que el archivo carezca de datos sensibles.

En tutelas manuscritas escaneadas, la revisión visual muestra una fila por cada página aunque el OCR haya propuesto datos. Compruebe todas las apariciones del nombre, cédula, radicado, TD, NUI, ubicación precisa (patio o estructura) y huella. Dibuje zonas adicionales sobre lo que el OCR no detecte y compruebe el PDF generado antes de compartirlo. El original se conserva en la recuperación local.

En escritos manuscritos, el análisis busca etiquetas de identidad aun cuando el OCR las lea con pequeñas diferencias. Propone cajas ajustadas a los valores del nombre, números, ubicación precisa y huella, dejando visibles las etiquetas y el texto vecino. Las zonas aparecen seleccionadas automáticamente: no es necesario dibujarlas cuando el detector las ubica. La detección sigue siendo heurística; revise el resultado antes de compartirlo, sobre todo si la letra es difícil de leer o el documento tiene otra disposición.

**Cancelar anonimización** permite detener el análisis, el OCR en curso o la generación. Se interrumpe el lote y se elimina la salida incompleta del archivo en curso; los originales permanecen intactos. Los archivos del lote que ya terminaron se conservan. Durante la revisión puede usar **Cancelar sin generar archivos**.

### Anexos e historias clínicas

El OCR combina la lectura de página completa con contraste mejorado, texto disperso y columnas. Se proponen nombres de personas, identificaciones y correos electrónicos, como `CC-12345678`, incluidos los nombres de profesionales etiquetados y nombres separados de sus etiquetas por un salto de línea. Las lecturas adicionales pueden tardar más y las propuestas deben revisarse.

La lectura adaptativa y la eliminación de ruido ayudan con autorizaciones y cédulas fotocopiadas. En PDF se identifican primero los nombres y números personales del documento y después se buscan sus repeticiones en los anexos, incluso con apellidos antes de nombres o pequeñas variaciones del OCR. Se reutiliza el OCR al generar; no se vuelve a reconocer el documento después de autorizar.

En **Ubicación visual (OCR)** puede inspeccionar las propuestas sobre la imagen. Los datos reconocidos se sustituyen por códigos en el espacio del campo.

Se generan un archivo con los datos sustituidos por códigos y una **copia de recuperación `.restauracion` sin cifrar**. Los originales permanecen intactos. En campos visuales estrechos, la etiqueta se recorta dentro del campo; el código completo se conserva en el software para restaurar un documento derivado que lo contenga.

## Desanonimizar

La pestaña **Restaurar archivos anonimizados** tiene dos opciones: recuperar una copia idéntica del archivo original, sea cual sea su formato, o sustituir los códigos en la sentencia, fallo, auto u otro documento elaborado por el sustanciador. **Los datos personales se procesan solo en el sistema local.**

Conserve la copia `.restauracion`: contiene el original y los campos guardados sin cifrar. Comparta únicamente el documento anonimizado. Sin la copia de recuperación no se pueden recuperar los datos eliminados.

### Restaurar un documento elaborado por el sustanciador

Las nuevas copias `.restauracion` guardan también la correspondencia entre cada código autorizado y su dato original. El restaurador detecta los códigos que ya aparecen en el documento y carga automáticamente los datos originales sin perder el texto nuevo del sustanciador. No necesita copiar ni marcar los campos uno por uno.

1. Descargue el documento todavía anonimizado desde la plataforma donde trabaja el sustanciador.
2. En **Restaurar archivos anonimizados**, elija **Restaurar sentencia, fallo, auto u otro documento elaborado por el sustanciador** y seleccione el archivo descargado.
3. Seleccione la recuperación local del caso si no se encuentra automáticamente y pulse **Restaurar documento del sustanciador en local**. Revise la copia restaurada antes de guardarla o entregarla.

Para recuperar el archivo original sin cambiar un solo byte, elija **Restaurar una copia idéntica del archivo original**, seleccione el archivo anonimizado o su recuperación `.restauracion` y pulse **Restaurar copia exacta del original**.
Los códigos y sus datos originales aparecen directamente en la pantalla de restauración. Los controles para añadir datos sensibles y activar detección se muestran solo al anonimizar. Al cargar otro documento o intentar cargar una tabla inválida se limpia la recuperación anterior.

Los códigos nuevos incluyen un identificador propio del análisis, para que dos documentos con un primer nombre anonimizado no compartan el mismo código. Si aparece un código que no está en la recuperación seleccionada, se informa el problema y no se genera una copia incompleta. Las tablas anteriores siguen siendo compatibles; sus códigos genéricos no identifican por sí solos el caso y requieren elegir su recuperación correcta.

Se admiten Word `.docx`, texto `.txt`, `.md`, `.log`, `.rst` y PDF con los códigos como texto seleccionable. En Word se conservan imágenes, estilos y las partes no modificadas; se reconocen códigos divididos entre fragmentos de formato, encabezados y pies. En PDF el dato se ajusta al espacio del código: si no cabe con letra legible, se solicita usar Word. Un escaneo o un bloque negro no es un código editable.

Las recuperaciones antiguas siguen restaurando el original completo, pero no contienen esta tabla de campos. Para usarla, restaure el original y vuelva a anonimizarlo con esta versión. Las zonas dibujadas no generan códigos de texto porque pueden contener fotos, firmas o información ilegible.

## Archivos de cualquier extensión

Solo se descartan audios y videos, también cuando están dentro de un ZIP. La extensión no determina si un archivo se acepta: se inspecciona su contenido.

| Contenido detectado | Tratamiento |
|---|---|
| Texto con cualquier extensión | Sustitución de datos; JSON y XML conservan su estructura cuando son válidos |
| Office / OpenDocument | Sustitución de texto y OCR selectivo en imágenes incrustadas; limpieza de metadatos principales |
| PDF | Redacción de texto; OCR local a 300 DPI en páginas escaneadas o con imágenes; se omiten adjuntos |
| Imágenes | OCR local en español e inglés: se muestran códigos en los campos sensibles y se conserva el resto de la imagen; se eliminan metadatos |
| ZIP | Se procesan sus archivos recursivamente, se descarta audio/video y se renombra cada entrada |
| Otros binarios, documentos protegidos, dañados o contenido que no se pueda interpretar | Se genera un PDF sustituto con el contenido completamente omitido y un aviso en el registro |

Aceptar cualquier extensión no significa conservar el contenido visible de todos los formatos. En formatos que no se pueden interpretar, el PDF sustituto **no es una conversión del documento**: omite todo el contenido. Se informa expresamente en el registro. El original se conserva íntegro en la copia `.restauracion`, también en estos casos. En imágenes y escaneos se reconocen palabras con Tesseract y se tachan las áreas que coinciden con los nombres o identificaciones manuales y los patrones automáticos. Los nombres manuales pueden abarcar varios renglones. Las sugerencias de nombres de personas se basan en etiquetas, no en reconocimiento general de entidades.

Los documentos con macros u objetos binarios incrustados también generan un PDF sustituto. Los PDF con contraseña se procesan de esta manera sin pedir contraseña. Los archivos de salida tienen nombres genéricos para evitar que el nombre original revele datos. La restauración recupera el nombre base y la extensión original.

Límite de entrada: 100 MB por archivo; contenido ZIP/Office descomprimido: 300 MB y hasta ocho niveles de ZIP anidados. Son límites operativos, no exclusiones de formatos. Los archivos que superan el límite de entrada generan un error explícito. Se procesa un archivo a la vez y los errores no detienen el lote.

Revise los resultados y los avisos del registro. Los documentos complejos pueden cambiar de formato, perder firmas digitales, enlaces o propiedades. Las reglas pueden omitir datos que no coincidan con los patrones ni la lista manual. Esta herramienta no certifica que un documento esté libre de información identificable.

## OCR local instalado

El OCR usa Tesseract con los modelos oficiales `spa` y `eng` en la carpeta `tessdata`. Las imágenes se procesan localmente; no se envían a un servicio externo. El instalador descarga el motor y los modelos una vez. `iniciar.bat` comprueba la instalación.

En PDF escaneados o mixtos se procesa la apariencia de cada página a 300 DPI. La salida conserva su tamaño visible, reemplaza los campos seleccionados por etiquetas y contiene los códigos completos como texto no visible. En imágenes multipágina se procesa cada fotograma/página. Las páginas de PDF con texto nativo sustituyen los campos por etiquetas.

El registro muestra cuántas imágenes/páginas recibieron OCR y avisa si hay palabras de baja confianza o no se reconoce texto. El reconocimiento puede fallar con documentos borrosos, girados, manuscritos o diseños complejos: revise siempre las propuestas y el resultado. Si el motor falla o falta, el procesamiento informa un error y no genera un documento marcado como anonimizado.

Documentación del motor: [Tesseract: uso de la línea de comandos y salida TSV](https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html).

## Consola

```powershell
python anonimizador.py anonimizar documento.docx --datos sensibles.txt --salida salida
python anonimizador.py restaurar salida\documento_anon_XXXXXXXX.docx --salida recuperados
python -m unittest -v
```

La consola también muestra propuestas y pide los números que autoriza (o `TODAS`); Enter cancela sin guardar.

`sensibles.txt`: UTF-8 con un dato por línea. Use `--solo-manual` para desactivar las reglas automáticas.


Carpetas de trabajo: `C:\TUTELAS\ORIGINALES` contiene los archivos originales. `C:\TUTELAS\ANONIMIZADOS` contiene los archivos anonimizados, sus copias de recuperación y los archivos restaurados.
