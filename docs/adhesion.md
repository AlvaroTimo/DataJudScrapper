# Termos de adesão: extracción y limpieza

El alcance de la versión nueva son instrumentos completos de adhesión a tarjetas de crédito,
incluidos títulos equivalentes y propuestas de emisión con aceptación. Excluye CCB,
saques, reglamentos independientes, consentimientos separados, seguros independientes,
informes biométricos y fragmentos dentro de escritos. Conserva las cláusulas y los anexos
que integran el propio termo. No elimina originales.

## Resultado del primer piloto

El piloto terminó los 25 procesos: cobertura útil 0.0 % (0/12) y fiabilidad de entregados 0.0 % (0/6). **No alcanzó el 80 % y no se amplió al lote.**
Los fallos incluyen residuos visibles y eliminación de contenido que debía conservarse.
La integridad de los PDF y las pruebas de software no demuestran calidad contractual.
Consulte el [resultado, evidencia y limitaciones de referencia](adhesion-pilot-20260920.md).

## Preparación y backup

```bash
uv sync --locked --extra dev --extra contracts
datajud-adhesion prepare --batch 77248dcf-90a5-427a-bbea-4ba6c14e502c
datajud-adhesion verify-backup
```

La preparación exige que los procesos antiguos hayan terminado. Archiva sus contratos,
temporales, revisiones e informes bajo `data/backups/<fecha-hora>/`, con un inventario de
SHA-256, un diario reanudable y una copia consistente de SQLite. Los originales y el
catálogo de descargas permanecen en su lugar. El scraper puede resolver los contratos
archivados de los originales previamente descartados.

La preparación inicial identifica 1.000 procesos del lote: 908 originales disponibles y
92 pendientes de recuperación. Congela 25 procesos mediante muestreo uniforme sin
reemplazo, semilla 20260920, excluyendo toda exposición previa registrada. Otros 30
procesos, semilla 20260921, forman el conjunto de desarrollo. También están disponibles
los ejemplos ya explorados. Los archivos de selección no se reemplazan por casos fáciles.

`datajud-adhesion restore-backup <carpeta>` revierte los movimientos sin sobrescribir
directorios activos ni sustituir el catálogo vivo por la copia histórica de SQLite.

## Ejecución

```bash
datajud-adhesion run --manifest data/adhesion-v1/development.json
datajud-adhesion run --document <document-id>
datajud-adhesion freeze
datajud-adhesion reference
```

El inventario de texto/OCR anterior se reutiliza únicamente si coincide con el original,
las versiones de extracción y la configuración OCR. No se reutilizan decisiones ni
máscaras antiguas. Si un inventario antiguo no registra hashes de modelos y versión de
runtime OCR compatibles, se vuelve a reconocer la página. El uso selectivo y la reutilización
de OCR siguen el principio descrito
en la [documentación de PyMuPDF](https://pymupdf.readthedocs.io/en/latest/recipes-ocr.html).
La versión `complete_card_adhesion_index_v2` sigue este flujo:

1. Lee marcadores y enlaces del índice, convierte sus destinos en páginas físicas y
   comprueba que los rangos cubran el PDF sin huecos ni solapamientos. Si el índice falta
   o es inconsistente, busca por contenido en todo el documento.
2. Prioriza anexos por títulos normalizados, aliases y contexto del banco. Examina también
   las dos primeras páginas de cada anexo para descubrir formularios con nombres opacos.
   Una pista en el índice propone candidatos; no confirma un contrato.
3. Clasifica visualmente las páginas candidatas y delimita cada instrumento por su
   contenido. Un anexo judicial puede contener varios contratos y documentos ajenos.
   Las cláusulas que mencionan CCB no equivalen a un título de CCB independiente.
4. Verifica cierres sospechosos y continuaciones. Firmas y contadores son evidencia,
   pero no fijan por sí solos el final; pueden seguir beneficios integrantes, incluso
   con contadores 5/4 y 6/4. Cruzar entre anexos exige confirmación visual explícita.
5. Busca en el contenido residual aunque ya haya encontrado un contrato, sin volver a
   clasificar las mismas páginas. Así puede encontrar otra adhesión o copia en un anexo
   que no se seleccionó inicialmente.
6. Valida por separado la primera y última página. Distingue instrumentos personalizados
   y plantillas completas. Conserva las ubicaciones de las copias y envía las dudas a
   revisión; una respuesta incompleta o inválida no constituye un negativo.

Para reproducciones completas o páginas compartidas con documentos personales, el modelo
solo puede escoger recortes medidos en el PDF. Se compara el recorte con la página original
para comprobar que conserva todo el contenido contractual. Sin un recorte seguro, el caso
queda pendiente. La limpieza y el PDF final usan exactamente ese recorte, con las palabras
y máscaras trasladadas a sus coordenadas, incluyendo rotación. La revisión muestra la
página completa, el recorte fuente y la salida limpia.

El inventario se extrae y guarda por página, ligado al hash del original y a la configuración
de texto/OCR. Primero lee texto nativo; usa OCR a 200 dpi cuando falta texto útil o está
corrupto y ofrece un reintento a 300 dpi para validar un inicio ambiguo. Un reintento no
sustituye silenciosamente el texto anterior. El fallback residual puede necesitar OCR de
muchas páginas en una ejecución sin caché: priorizar el índice no garantiza reducir todo
el coste de OCR. Los manifiestos registran candidatos primarios/residuales, motivos,
correcciones de límites, recortes y contadores de lectura/OCR/caché.

La limpieza propone bloques y celdas medidos sobre el documento. El modelo solo puede
seleccionar regiones existentes. Las reglas intentan proteger cláusulas, opciones contratadas, tasas,
importes, fechas de ejecución y datos corporativos; su conservación efectiva se evalúa
contra el original y no se infiere de la aceptación automática. En escaneos se corrige la inclinación
para OCR y se trasladan las coordenadas a la imagen original. El PDF final se reconstruye
a 300 dpi sin objetos ni capas ocultas del original.

La segmentación de escaneos usa RapidOCR sobre una copia enderezada; Tesseract vuelve a
leer la salida limpia. Se incorporan regiones medidas de imágenes y QR para cubrir
fotografías y marcas que no aparecen en el texto OCR.
La resolución de 300 dpi y la corrección de inclinación están respaldadas por las
[recomendaciones de Tesseract](https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html).

En un entorno nuevo, instalar los modelos locales con
`uv run python scripts/setup_local_model.py --model qwen3.5:27b` y
`uv run python scripts/setup_local_model.py --model qwen3-vl:8b-instruct-q8_0`.
El entorno utilizado para este piloto ya los tiene instalados. El código del flujo llama
únicamente al servicio local de inferencia. La adjudicación adicional por Codex se registra
por separado en la evidencia de revisión.

Los controles vuelven a leer los píxeles limpios, buscan identificadores y comparan la
conservación del contenido. El detector de residuos recibe únicamente la imagen limpia;
el comparador de conservación recibe ambas imágenes. Se admite una reparación automática.
Una duda inicial solo se resuelve si los controles finales independientes son concluyentes;
esa resolución queda registrada. Los resultados inciertos van a `quarantine`, separados de
`accepted`.

El estado está en `data/adhesion-v1/state.sqlite3`. Las ejecuciones tienen huellas del
código, modelos y configuración; se reanudan por documento/página sin reutilizar resultados
de otra versión. Los estados terminales son `completed`, `no_target`, `needs_review` y
`error`; `running` representa una ejecución en curso. Los manifiestos no incluyen valores
personales; OCR, regiones y cachés de inferencia son datos privados de trabajo.

Las salidas del piloto y del lote están en
`data/adhesion-v1/outputs/<configuración>/accepted/` o `quarantine/`. Los ensayos de
desarrollo se separan en `development-outputs/<configuración>/`. Para localizar una
salida, usar su manifiesto y su hash; una aprobación de desarrollo no valida el piloto.

## Referencia independiente y evaluación

`freeze` fija la versión antes de abrir las fuentes reservadas. `reference` presenta
**todas** las páginas de los 25 originales al revisor visual local, sin predicciones ni
máscaras del extractor, guardando imágenes, hashes y procedencia de cada observación.
La revisión es **asistida por IA, no humana**. Usa `qwen3-vl:8b-instruct-q8_0` y un prompt
separado del extractor `qwen3.5:27b`. Son checkpoints distintos de la familia Qwen y pueden
compartir errores. Se registran sus hashes y se conserva una copia del código congelado.

La referencia incluye formularios completos sin rellenar: el alcance no exige que el
instrumento esté firmado. Los formularios complementarios de una misma solicitud se
consideran un solo instrumento cuando forman conjuntamente su adhesión. Las copias
repetidas conservan todas sus ubicaciones y cuentan una sola vez. Estas decisiones se
fijan sobre el original antes de ejecutar predicciones.

Las páginas positivas, fragmentarias o inciertas requieren adjudicación visual ampliada
antes de registrar `gold.json`. No puede modificarse la referencia después de ejecutar
predicciones de esa muestra.

En el piloto del 20 de septiembre de 2026, las etiquetas del primer revisor visual
confundieron páginas judiciales con adhesiones. Se conservaron esas observaciones y se
interrumpió ese pase. La referencia usa descripciones visuales independientes de cada
página, una imagen por consulta, con el mismo checkpoint de revisión, seguidas de
adjudicación de los posibles instrumentos sobre las imágenes originales. La sustitución del procedimiento de referencia
queda documentada en `data/adhesion-v1/reference-adjudication-protocol.json`; no modifica
el extractor congelado. Los prompts, herramientas auxiliares, respuestas e imágenes con
hash se conservan en ese espacio privado. Las páginas vistas directamente por Codex se
enumeran por separado de las observadas por el modelo local.
Las primeras descripciones por grupos también se conservaron como evidencia descartada:
se repitió la observación individual de todas las páginas al detectar desplazamientos entre
imágenes del mismo grupo. Estas correcciones pertenecen a la referencia, antes del piloto.

```bash
datajud-adhesion record-source referencia.json
datajud-adhesion run --manifest data/adhesion-v1/holdout.json
datajud-adhesion review --document <document-id>
datajud-adhesion record-output revision.json
datajud-adhesion evaluate
```

La referencia contiene `document_id`, `method: ai_assisted_visual`, `reviewer`,
`adjudicated_pages` e `instruments`, cada uno con `pages` y `occurrences`.
Una fuente que siga ilegible o ambigua debe conservar `uncertain_pages`; el piloto queda
inconcluso y no habilita el lote completo mientras esa referencia no pueda establecerse.
La revisión de salida contiene `document_id`, `run_id`, `method`, `reviewer` e `instruments`;
por salida registra `contract_id`, `output_sha256`, `observed_pages`, `residual_pii_pages`
y `damaged_content_pages`. Renderizar una página no constituye revisarla.
También se registra `foreign_content_pages` si la salida conserva texto judicial u otro
contenido ajeno. Una reproducción íntegra dentro de un escrito cuenta en la referencia
como termo real; si el extractor no logra separarla, cuenta como omisión, no como negativo.
Si la revisión no puede establecer limpieza o conservación, registrar `uncertain: true`;
esa salida no cuenta como útil aunque el control automático la haya aceptado.

El informe exige los 25 casos y calcula:

- Cobertura útil: termos reales entregados completos, limpios y sin daño / termos reales.
- Fiabilidad: salidas entregadas que cumplen las tres condiciones / salidas entregadas.
- Precisión y recuperación de extracción, límites erróneos, residuos, daño y cuarentena.

Ambas tasas principales deben alcanzar 80 %. Pendientes, errores, omisiones y abstenciones
no se cuentan como aciertos. Los negativos no inflan los denominadores de contratos.
Una muestra sin positivos es inconclusa; una muestra con pocos positivos aporta evidencia
limitada. Los casos fallidos y sus salidas no se sustituyen ni corrigen manualmente.

`evaluate` guarda `evaluation.json` y un `report.md` con denominadores, tasas, resultados
por proceso y enlaces a la evidencia visual adjudicada.

Solo un informe completo favorable permite:

```bash
datajud-adhesion run --manifest data/adhesion-v1/corpus.json
```

Cambiar código, reglas o modelos después de congelar la prueba invalida su uso como
prueba nueva. Una iteración posterior requiere otra muestra independiente; la anterior
queda como regresión y su resultado histórico se conserva.

## Evaluar la versión con índice sin alterar el piloto histórico

`--workspace` selecciona una cohorte aislada para todos los comandos; el valor por defecto
sigue siendo `adhesion-v1`. Una cohorte nueva reutiliza el backup ya preparado del mismo
lote y no vuelve a mover los resultados históricos. La preparación excluye las selecciones
y ejecuciones previas, además de los manifiestos de exposición indicados explícitamente.
Los manifiestos de exclusión quedan ligados por hash y deben conservarse para reanudar.

```bash
datajud-adhesion --workspace adhesion-index-v2 prepare \
  --batch 77248dcf-90a5-427a-bbea-4ba6c14e502c --seed 20261008 \
  --exclude-manifest /ruta/conservada/selection-50.json \
  --exclude-manifest /ruta/conservada/selection-extra10.json
datajud-adhesion --workspace adhesion-index-v2 run \
  --manifest data/adhesion-index-v2/development.json
datajud-adhesion --workspace adhesion-index-v2 freeze
datajud-adhesion --workspace adhesion-index-v2 reference
```

Después se registra la adjudicación fuente, se ejecuta y revisa el holdout de esa cohorte y
se evalúa con los mismos comandos anteriores, añadiendo `--workspace adhesion-index-v2`.
La configuración nueva no puede reutilizar la congelación del primer piloto ni habilita
por sí sola la ejecución del corpus completo.

## Pruebas

```bash
uv run --no-sync pytest
uv run --no-sync ruff check .
uv sync --locked --extra dev --extra contracts --check
```

Se conservan pruebas de píxeles, ausencia de capas ocultas, rotación, OCR e índice PDF.
Las nuevas pruebas cubren separación de muestras, límites de instrumentos, bloques mixtos,
datos económicos, integridad del backup, restauración y reanudación. Los ejemplos de
prueba son sintéticos; los documentos reales permanecen fuera de Git.
También se verifican fallback después de un primer éxito, índices ausentes/inconsistentes,
enlaces sin marcadores, límites entre anexos, contadores inconsistentes, plantillas,
coordenadas de recortes y máscaras con rotación, caché por página y aislamiento de cohortes.

La regresión de selección sobre los 50 PDF analizados y los 10 adicionales cubrió las
35 y 5 ocurrencias de referencia, respectivamente. Es cobertura de candidatos sobre
casos ya explorados, no precisión de extracción ni evaluación de anonimización. Una prueba
local adicional de cinco anexos previamente explorados recuperó los cinco rangos esperados,
incluidos los beneficios de BMG y el recorte de un formulario que comparte página con una
identidad. Persisten ambigüedades en otros documentos del anexo BMG; quedan en revisión.
Estos ensayos no sustituyen un piloto independiente de fuentes completas.
