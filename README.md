# DataJud Scraper

El scraper descarga expedientes y procesa cada PDF en **dos fases independientes**:
extracción de termos y documentos equivalentes de adhesión a tarjetas, y anonimización
de los contratos extraídos. Ambas se ejecutan por defecto después de validar cada descarga.
Después de superar todos los controles automáticos, elimina el original y los contratos
sin anonimizar; la conservación es configurable. Cada fase tiene PDF, manifiesto y estado propios.
Consulte la [metodología, comandos y evaluación](docs/adhesion.md).
Los controles automáticos y la revisión visual asistida por IA se registran por separado.

**El primer piloto no alcanzó el 80 % requerido.** El comando de corpus de evaluación
conserva ese requisito. La integración operativa del scraper utiliza `adhesion-scraper`,
separado de las cohortes de evaluación; su activación no acredita una nueva tasa de calidad.
Consulte los [resultados y limitaciones](docs/adhesion-pilot-20260920.md).

Importa el dataset JSONL de PROJUDI/TJBA y descarga sus PDF consolidados mediante lotes
reanudables. Conserva todos los registros y la metadata original, incluidos campos extra.
La entrada principal es `url_download` de tipo `DownloadProcesso`; esta versión no descarga
documentos individuales de `DownloadArquivo`.

## Instalación

Compatible con macOS y Linux. Desde la raíz del proyecto:

```bash
uv sync --locked --extra dev --extra contracts
```

`.python-version` fija Python 3.14.0; `uv.lock` fija las dependencias y sus hashes.
No es necesario activar el entorno virtual.

Para los modos `both` y `extract`, prepare el OCR de extracción y el modelo local.
El modo `both` requiere además OCR de anonimización:

```bash
.venv/bin/python -m datajud_scraper.ocr_models --quality fast
.venv/bin/python -m datajud_scraper.ocr_models --quality best
.venv/bin/python -m datajud_scraper.adhesion.ocr
.venv/bin/python scripts/setup_local_model.py --model qwen3.5:27b
```

También debe estar disponible Tesseract. En Linux, `scripts/setup_table_ocr.py` prepara
el runtime local soportado; los scripts de instalación de runtimes requieren Linux.
El servicio de inferencia debe responder en `127.0.0.1:11434`. Los modelos se descargan
durante la preparación; el procesamiento de documentos usa inferencia local.
Si falta el backend, el lote se pausa con `contract_processing_unavailable` **antes de
descargar el PDF**. `--contract-mode none` permite descargar sin esos requisitos.

## Fases y conservación

| Modo | Comportamiento |
| --- | --- |
| `--contract-mode both` (predeterminado) | Descarga → extracción → anonimización → controles → limpieza de fuentes privadas. |
| `--contract-mode extract` | Descarga y guarda contratos sin anonimizar para inspección o anonimización posterior. |
| `--contract-mode none` | Descarga y valida el expediente. |

`--contract-retention purge` es el valor por defecto. Solo elimina fuentes cuando **todos**
los contratos detectados superan los controles de privacidad y conservación, la búsqueda
queda resuelta y existe al menos una salida anonimizada cuyo hash se verifica. Conserva
originales ante errores, dudas o ausencia de contratos. También elimina los inventarios
OCR y cachés privados del documento en el espacio operativo. Conserva los manifiestos,
las huellas y los PDF anonimizados. El borrado es de archivos locales; no borra backups
históricos ni garantiza eliminación física de bloques del disco.

Para revisar los PDF de ambas fases, use `--contract-retention keep` desde el inicio.
`extract` y `none` conservan sus fuentes independientemente de esta opción. Cambiar a
`keep` no recupera archivos ya eliminados. Cada `resume` recibe sus propias opciones;
para continuar una extracción con anonimización, use `resume <batch-id> --contract-mode both`.

`results.jsonl` registra `contract_processing`, con estado, retención y rutas por fase,
además de `contract_paths`. `report.md` enlaza los documentos y manifiestos. Los contratos
inciertos figuran en cuarentena y el lote termina con `processing_needs_review` /
`completed_with_errors`; no se presentan como salidas listas. La anonimización puede
reanudarse desde el manifiesto de extracción sin repetir la búsqueda del contrato.

## Rendimiento y equipos con menos recursos

La ejecución reutiliza la sesión HTTP entre expedientes y vuelve a inicializarla si expira.
La ficha de cada proceso se sigue comprobando antes de descargar. El intervalo HTTP, los
límites persistentes y las pausas solicitadas por el tribunal siguen aplicándose.

El inventario extrae texto nativo una sola vez por página y ejecuta el OCR por procesos,
con un PDF independiente en cada proceso. `--ocr-workers 0` selecciona automáticamente
hasta ocho procesos, limitado por CPU disponible (afinidad y cuota del contenedor), memoria
libre y tamaño del PDF. Con
`--ocr-workers 1` se ejecuta en serie. `--ocr-memory-mb` es un presupuesto **estimado para
seleccionar concurrencia**, no un límite estricto del sistema operativo ni la RAM del modelo.
El inventario mantiene hasta 64 páginas por caché en memoria; el resto queda en disco.
Las cachés OCR son compactas, privadas y se publican de forma atómica; los manifiestos y
las salidas conservan sincronización a disco y verificación de hashes.
El OCR de las páginas restantes comienza en segundo plano mientras se consumen las sondas
y se consulta al modelo. Los reintentos a 300 dpi también usan el mismo grupo de procesos;
cada consumidor espera y verifica únicamente los resultados que necesita. Una interrupción
termina los procesos pendientes. El recorrido residual sigue examinando todas las páginas.

El OCR neuronal de fotografías/escaneos puede usar CUDA de forma explícita. Para preparar
un entorno separado en Linux x86_64 con el Python fijado por el proyecto:

```bash
UV_PROJECT_ENVIRONMENT=.venv-cuda uv sync --locked --extra contracts-cuda
DATAJUD_NEURAL_OCR_DEVICE=cuda .venv-cuda/bin/python -m datajud_scraper.adhesion.ocr
DATAJUD_NEURAL_OCR_DEVICE=cuda .venv-cuda/bin/datajud-scraper resume <batch-id> \
  --contract-mode both --contract-retention keep
```

`contracts` instala ONNX en CPU; `contracts-cuda` instala ONNX GPU y sus bibliotecas CUDA
y cuDNN. Son extras excluyentes para evitar que dos distribuciones sobrescriban el mismo
módulo. El driver NVIDIA debe ser compatible con esas bibliotecas. Se comprueba el proveedor
activo de las tres sesiones neuronales: un fallo CUDA no se presenta como una medición GPU
con ejecución en CPU, tampoco durante la inferencia. El dispositivo y sus opciones forman
parte de la firma de anonimización.
La opción utiliza el dispositivo 0, desactiva TF32 y limita la arena a 2048 MiB **por sesión**;
esto no limita toda la VRAM del proceso ni incluye Ollama. El valor predeterminado sigue
siendo `cpu`. El OCR Tesseract del inventario y la revisión independiente de la salida
siguen en CPU. CUDA se ha verificado en la RTX 5090; falta verificar memoria y latencia
al compartir la Quadro de 16 GB con el modelo visual.

La consola muestra lectura/OCR, inferencias y fases. `results.jsonl` incluye `timings`
(`scraper_seconds`, `contract_seconds`, `total_seconds`) y las métricas de contratos
separan extracción, anonimización, inferencia y reutilización de resultados.
`model_backend_seconds` distingue carga de pesos, evaluación de entrada y generación de
salida según lo informado por Ollama. Un valor `null` indica que faltó esa medida, no que
el trabajo tardara cero segundos.

Para equipos con GPU de 16 GB, puede instalar un modelo menor y seleccionarlo explícitamente:

```bash
.venv/bin/python scripts/setup_local_model.py --model qwen3.5:4b
.venv/bin/datajud-scraper resume <batch-id> \
  --contract-mode both --contract-retention keep \
  --local-model qwen3.5:4b --ocr-workers 0 --ocr-memory-mb 1024
```

También se ensayó `qwen3.5:9b`, instalable con el mismo script. Ollama informó 6,7 GB de
memoria residente con contexto de 32768 tokens en el host medido; no es una medida de
VRAM máxima ni una validación en la Quadro objetivo. El expediente dejó un grupo para
revisión, frente a dos con 27B y diez con 4B. Menos revisiones pendientes no demuestra
mejor precisión: falta un conjunto de contratos reales etiquetados para compararlos.

El modelo 27B sigue siendo el predeterminado. El 4B permite reducir los recursos necesarios,
pero requiere evaluar su calidad: en el PDF ensayado dejó diez grupos para revisión frente
a dos del 27B. Ninguna de esas ejecuciones confirmó un contrato; esa comparación no mide
precisión ni cobertura de positivos. Cambiar modelo, contexto o límite de salida cambia la
firma del procesamiento y evita reutilizar decisiones incompatibles. Una respuesta truncada
o con el contexto saturado se rechaza. El OCR conserva las resoluciones de 200/300 dpi y las
salidas rasterizadas conservan 300 dpi, sus píxeles y los controles de anonimización.

Mediciones del 9 de octubre de 2026 sobre el expediente de 290 páginas y 40,5 MB:

| Medida | Resultado |
| --- | ---: |
| Extracción original, 27B | 228,9 s |
| Extracción y fase de anonimización, optimizado, 27B y 12 procesos / 4096 MiB | 40,1–59,9 s (ejecución anterior: 52,9 s) |
| Inventario completo de 290 páginas, 74 con OCR, 12 procesos | 22,9 s |
| Reutilización del inventario | 0,125 s |
| Reutilización de fases verificadas, sin preparación del runtime ni validación de fuente | 0,0044 s |
| Extracción y fase de anonimización, 4B y concurrencia automática | 65,5 s |
| Extracción y fase de anonimización, 9B y 12 procesos / 4096 MiB | 50,0 s; 50,6 s incluyendo preparación y validación |

El caso ensayado terminó en `needs_review` sin contratos confirmados: la fase de anonimización
no tuvo páginas que anonimizar. Estos números **no prueban la latencia de anonimizar contratos
positivos**. Los benchmarks usan espacios temporales independientes del lote y cachés nuevas
en la primera ejecución; la segunda reutiliza resultados. El sistema operativo puede tener
los archivos en su caché. El host medido tiene Core Ultra 9 285K, RTX 5090 de 32 GB y 123 GiB
de RAM, con otros trabajos activos; no es la Quadro RTX 5000 de 16 GB del equipo objetivo.
El estado en memoria de Ollama no se reinicia: la última ejecución 27B informó 0,13 s de
carga y 6,17 s de consultas; la primera 9B informó 4,35 s de carga y 13,75 s de consultas.
Estos ensayos incluyen diferentes estados del runtime y no permiten atribuir la variación
completa al código ni comparar modelos como si ambos estuvieran recién cargados.

También se completó un contrato sintético positivo de una página, con 27B: extracción
7,9 s, anonimización 13,6 s, seis consultas al modelo y estado final `completed`. Es una
prueba funcional con datos ficticios, no una evaluación de precisión sobre contratos reales.
Una prueba de descarga real, con sesión reutilizada y ambas esperas de cortesía fijadas
explícitamente a cero, tardó 21,8 s y 15,7 s para los dos primeros expedientes. Una ejecución
previa del mismo par tardó 18,5 s en total: la red y el servidor varían entre ejecuciones.

Los objetivos de 3 s para descargar y 5 s para extracción y anonimización **no se alcanzaron
para PDF nuevos escaneados**. El intervalo HTTP predeterminado ya añade varias esperas;
además, el servidor y el tamaño de los documentos no ofrecen una cota de descarga. Solo el
OCR del ejemplo supera 5 s. Una caché verificada puede cumplir tiempos mucho menores,
pero eso no equivale a procesar un documento nuevo. No se omiten páginas ni controles para
presentar un resultado incompleto como terminado.

Opciones evaluadas y decisiones:

| Opción | Decisión |
| --- | --- |
| Parsear texto una vez; reutilizar texto normalizado | Implementado. Se conservaron palabras posicionadas y decisiones OCR en las 290 páginas contrastadas. |
| Paralelizar OCR | Implementado con procesos aislados, concurrencia por CPU/RAM y terminación al interrumpir. |
| Hilos con PyMuPDF | Descartado: su documentación no admite acceso concurrente desde hilos. |
| Cachés compactas y menos sincronizaciones | Implementado para inventarios regenerables; manifiestos y PDF siguen siendo durables. |
| Evitar renders repetidos; comprimir PNG con menor esfuerzo | Implementado sin pérdida de píxeles, con verificación posterior. |
| Reutilizar HTTP y cookies | Implementado por lote con recuperación de sesión; se conserva comprobación de secreto por proceso. |
| Modelos menores y contexto/salida configurables | Implementado como opciones; 4B y 9B ensayados, sin evidencia suficiente para sustituir el 27B predeterminado. |
| Omitir OCR, búsqueda residual o revisiones visuales | No aplicado: podría omitir contratos o aceptar contenido sin verificar. |
| Bajar dpi o sustituir el OCR | Requiere comparación independiente de detección, legibilidad y privacidad; no aplicado por defecto. |
| OCR con CUDA/TensorRT | CUDA neuronal disponible mediante un entorno separado, con proveedor activo verificado. TensorRT y la Quadro objetivo no están validados. |
| Modelo entrenado específicamente para estas plantillas | Requiere etiquetas y evaluación independiente; no existe un conjunto validado vigente que permita sustituir el verificador. |
| Solapar descargas y procesamiento; paralelizar expedientes | Puede mejorar rendimiento de lotes, no garantiza latencia por PDF; requiere coordinar sesiones y límites globales. |
| Aplazar trabajo o imponer un timeout de 5 s | Limitaría la espera dejando trabajo pendiente; no satisface el requisito de procesamiento completo. |
| Caché de resultados ya verificados | Implementada y medida; depende de coincidencia de fuente, configuración y artefactos. |

Fuentes técnicas: [multiprocesamiento en PyMuPDF](https://pymupdf.readthedocs.io/en/latest/recipes-multiprocessing.html),
[concurrencia en Tesseract](https://tesseract-ocr.github.io/tessdoc/FAQ.html#can-i-increase-speed-of-ocr),
[contexto y memoria de Ollama](https://docs.ollama.com/context-length) y
[modelo visual Qwen 4B](https://ollama.com/library/qwen3.5:4b),
[Qwen 9B](https://ollama.com/library/qwen3.5:9b) y
[duraciones de inferencia Ollama](https://docs.ollama.com/api/chat).
La instalación GPU y su precarga siguen la
[documentación del proveedor CUDA de ONNX Runtime](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html).

Auditoría adicional del 10 de octubre de 2026, con objetivo de **10 s para extracción más
anonimización**. Se midieron inventario, OCR, reintentos, inferencia, máscaras, salida y
validación, incluyendo contratos sintéticos positivos con datos ficticios. La comparación
de código usa un snapshot del commit anterior y espacios privados nuevos, con el 27B ya
cargado en ambos casos. Las cachés de aplicación están vacías; Ollama y el sistema operativo
pueden reutilizar su estado en memoria.

| Ensayo | Procesamiento | Resultado |
| --- | ---: | --- |
| 290 páginas, snapshot anterior, 27B, 12 procesos / 4096 MiB | 39,7 s | 74 páginas OCR, 2 reintentos, 2 grupos pendientes |
| Mismo PDF, código actual, misma concurrencia y modelo cargado | 33,5 s | Mismos contadores OCR y 2 grupos pendientes |
| Mismo PDF, código actual, 23 procesos / 8192 MiB | 26,2 s | Mismos contadores OCR y 2 grupos pendientes |
| Mismo PDF, código actual, 12 procesos, tras cambiar de modelo | 51,4 s | 24,6 s de inferencia, incluidos 6,5 s de carga |
| Inventario de otro expediente, 469 páginas, 23 procesos / 8192 MiB | 32,8 s | 95 páginas OCR; no incluye detección ni anonimización |
| Positivo nativo de una página, 27B | 17,0 s | Completado y comprobado por píxeles; 15,7 s en seis inferencias |
| Positivo nativo de una página, 8B Q8, contexto 8192, modelo cargado | 10,7 s | Completado; identificadores y cláusulas comprobados por píxeles |
| Positivo escaneado de una página, 8B Q8, contexto 8192, OCR neuronal CPU | 13,7 s | Completado; identificadores y cláusulas comprobados por píxeles |
| Positivo escaneado de una página, mismas opciones y OCR neuronal CUDA cargado | 11,4 s | Completado; identificadores, pie judicial y cláusulas comprobados por píxeles |
| Positivo nativo, 4B / 9B | 2,3 s / 4,4 s | Sin contrato confirmado; revisión pendiente, no anonimización completa |

La reducción con 12 procesos es del 16%; el ensayo de 23 procesos también cambia el
presupuesto de recursos y no mide solamente una mejora de código. Todos estos ensayos
utilizan el host Core Ultra 9 / RTX 5090 descrito arriba. En los expedientes sin contratos
confirmados la fase de anonimización no procesa páginas: no deben compararse con positivos.
Las muestras sintéticas cambian nombre e importe entre pruebas para evitar reutilizar
exactamente la misma entrada visual. No sustituyen un corpus real etiquetado.

| Componente auditado | Hallazgo y cambio |
| --- | --- |
| Fuente, SHA-256 e identidad del proceso | Unos 0,04 s en el PDF de 40,5 MB; se conserva la validación. |
| Texto nativo, coordenadas y límites | El perfil registró 1,07 s en normalizar coordenadas; cuerpo y encabezado ahora comparten una conversión. Se conservan las reglas de separación y evidencia. |
| OCR del inventario | 74 páginas únicas; no hay duplicados de imagen completos que permitan evitar OCR. Se solapa el trabajo con las inferencias, manteniendo los límites de CPU/RAM. |
| Reintentos de confirmación | Dos OCR a 300 dpi consumían 7,05 s en serie. Se programan en paralelo sin cambiar la resolución ni el contenido examinado. |
| Modelo visual | Domina el positivo 27B. Los modelos 4B/9B rápidos dejaron revisión pendiente. Aumentar `num_batch` a 1024 no mejoró el positivo 8B: 10,7–10,8 s. No se cambia el modelo predeterminado. |
| OCR neuronal de privacidad | En la misma imagen de prueba, CUDA activo produjo las mismas 176 palabras y cajas: 0,19 s tras inicializar, frente a 1,05–2,01 s en CPU. El primer pase CUDA tardó 0,90 s. |
| Máscaras y comprobaciones | Se reutiliza la codificación PNG de cada imagen entre controles independientes; una reparación genera otra imagen. Se corrigió un carácter parcialmente visible al borde de un bloque personal escaneado, conservando los píxeles contractuales. |
| Recorte, memoria y salida | Se evita copiar un recorte de página completa. Continúan las salidas a 300 dpi, publicación durable, verificación de artefactos y cachés ligadas a fuente/configuración. |

**El máximo de 10 s no se alcanzó para documentos nuevos.** El OCR exhaustivo de expedientes
grandes y las seis inferencias de un positivo siguen superándolo. Las mejoras medidas no
constituyen una garantía para cualquier número de páginas, escaneo o hardware. Reemplazar
los verificadores por decisiones más ligeras requiere medir cobertura y privacidad con
contratos reales etiquetados; no se acepta un resultado pendiente como cumplimiento del plazo.

Para reproducir las mediciones sin descargar de nuevo ni alterar el lote:

```bash
.venv/bin/python scripts/benchmark_pipeline.py \
  --document-id 55cc4e9c-8fa1-4568-a8da-6687feb4f1c1 \
  --phase both --model qwen3.5:27b --workers 12 --memory-mb 4096 \
  --repeat 2 --output data/performance/full-27b.json
```

`--phase native`, `inventory` y `detect` permiten medir las etapas por separado. El benchmark
mantiene el catálogo en modo lectura y elimina únicamente su espacio temporal privado.
`model_metadata_seconds` mide las consultas de versión/modelos instalados, no la carga de
pesos. `model_backend_seconds.load_seconds` mide la carga informada en las inferencias;
esas duraciones ya están incluidas en `model_seconds`. `measured_total_seconds` incluye
preparación, validación de fuente y procesamiento. Las repeticiones no vuelven a preparar
el runtime. `worker_limit` muestra la concurrencia permitida y `parallel_workers_used`
los procesos utilizados por el inventario; una reutilización sin procesos informa cero.

Para medir un positivo ficticio con el modelo real y comprobar los píxeles de salida:

```bash
.venv/bin/python scripts/benchmark_pipeline.py \
  --synthetic-positive scanned --fixture-seed 22 --phase both \
  --model qwen3-vl:8b-instruct-q8_0 --context-tokens 8192 \
  --output data/performance/positive-scanned.json
```

`--synthetic-positive native` ensaya texto nativo; `scanned` ensaya la imagen con pie nativo,
como los expedientes escaneados del catálogo. `--fixture-seed` cambia datos ficticios e
importe. `extraction_seconds` y `anonymization_seconds` separan ambas fases;
`neural_ocr_device` registra CPU/CUDA. `fixture_checks` comprueba identificadores conocidos,
pie judicial y conservación de cláusulas, aunque la fase informe `completed`. Estos controles
de referencia se ejecutan después de medir el procesamiento. La muestra y sus artefactos
temporales se eliminan al finalizar. Para CUDA, use el mismo comando con `.venv-cuda/bin/python`
y `DATAJUD_NEURAL_OCR_DEVICE=cuda`.

## Piloto y lotes

```bash
.venv/bin/datajud-scraper scrape-dataset \
  external/dataset/dataset.jsonl \
  --metadata external/dataset/dataset.metadata.json \
  --limit 15 --sample diverse --seed 20260908
```

Primero valida **todo** el JSONL y su metadata: estructura, CNJ y dígito verificador,
año, identificadores, fechas, HTTPS, host, rutas y coherencia de los parámetros. Rechaza
CNJ/ID duplicados, JSON ambiguo y filas inválidas indicando su línea, antes de crear el
almacenamiento o acceder al portal. El sidecar requiere `version_esquema: 2` y un
`total_registros` coherente. Los campos originales requeridos son los 16 presentes en el
dataset entregado; `subject`, `classe`, `orgao_julgador` e `is_secret` admiten `null`;
`codigo_hash` es `null` para estas fichas. Las fechas importadas deben ser válidas y
`distribution_at` debe incluir su zona horaria. Una metadata opcional ausente en la ficha,
como `Assunto`, no impide descargar.

La importación es idempotente por la huella conjunta de ambos archivos. A continuación
crea un lote con identificador UUID y guarda su selección **antes de hacer peticiones**.
El límite predeterminado es 15 registros, contando también omisiones y fallos. No sustituye
registros fallidos. `--all` es la opción explícita para recorrer el dataset completo:

```bash
.venv/bin/datajud-scraper scrape-dataset external/dataset/dataset.jsonl \
  --metadata external/dataset/dataset.metadata.json --all
```

`--sample first` elige las primeras filas. La muestra `diverse`, diseñada para este dataset,
selecciona, sin repetir: un caso sin `subject`, uno sin asuntos DataJud, tres sin enlaces
(uno con avisos y dos sin ellos), dos adicionales con avisos y enlaces y ocho ordinarios
(cuatro de 2026, dos de 2025, uno de 2024 y uno anterior). En cada paso prioriza juzgados y
clases aún no representados; desempata por SHA-256 de `"<seed>:<CNJ>"`. Si el límite es
menor, corta esa secuencia; si faltan candidatos o el límite es mayor, completa con el
mismo criterio determinista. Con la semilla 20260908 y los archivos entregados produce
15 juzgados, seis clases y cinco años. `--all` conserva el orden completo del archivo.

El comando imprime un resumen JSON en stdout y progreso breve en stderr. Los informes
quedan en `data/reports/<batch-id>/`:

- `manifest.json`: selección inmutable, líneas y CNJ.
- `results.jsonl`: original completo, metadata verificada, URL efectiva, resultado,
  archivo, SHA-256, páginas, bytes, duración e historial de intentos por registro.
- `summary.json` y `report.md`: estado, resultados, tiempos, intentos y causas de pausa.

SQLite guarda el progreso después de cada registro. Los informes se actualizan por
registro en el piloto y aproximadamente cada minuto en lotes mayores, además del inicio,
el final y las pausas.

## Consultar y reanudar

```bash
.venv/bin/datajud-scraper status <batch-id>
.venv/bin/datajud-scraper resume <batch-id>
.venv/bin/datajud-scraper resume <batch-id> --retry-failed
```

`status` solo lee SQLite. `resume` utiliza los originales importados y conserva exactamente
el manifiesto, aunque los archivos de entrada ya no estén disponibles. Los registros con
intentos agotados solo se reintentan con `--retry-failed`. Una pausa por cinco fallos de
infraestructura requiere ese indicador para reabrir los intentos; las pausas temporales
siguen respetando su fecha incluso con él.

Los PDF existentes se comprueban localmente. Si siguen válidos, el resultado es
`already_exists`, sin acceso a la red ni duplicados; se ejecutan las fases pendientes según
el modo seleccionado. Si el original fue eliminado tras una anonimización verificada,
`contracts_preserved` reutiliza las salidas comprobadas sin volver a descargar.
Los ausentes o corruptos sin salidas verificadas se reparan.
`--refresh` permite verificar la ficha y solicitar una nueva captura; un SHA-256 idéntico
produce `unchanged`, y uno nuevo conserva una nueva versión. Las omisiones por secreto
se mantienen al reanudar salvo `--refresh`; un lote nuevo vuelve a consultar su estado.

Ctrl-C pausa el lote, conserva el progreso y elimina el temporal incompleto. Tras una
terminación abrupta, `resume` recupera estados en ejecución y limpia temporales o archivos
publicados que aún no se hubieran catalogado. No se reintentan errores de programación
silenciosamente: se pausa y se expone la excepción para corregirla.

Códigos de salida: 0 = lote completado / consulta de estado; 1 = completado con fallos;
2 = entrada o configuración inválida; 3 = pausa; 5 = fallo de almacenamiento inicial;
130 = interrupción. Las omisiones legítimas figuran por separado en `counts`.

## Sesión e identidad

Cada proceso utiliza un cliente HTTPX nuevo con HTTP/1.1, una conexión activa y una
persistente, y `keepalive_expiry=None`. Cookies y conexión se conservan en este orden:

1. Abrir la consulta pública configurada en `bootstrap_url`.
2. Abrir `source_url` del dataset y verificar CNJ, ID y `Segredo de Justiça` actuales.
   El ID se obtiene del enlace `DadosProcesso` del encabezado, aunque no haya botón de descarga.
3. Si la ficha falta o identifica otro proceso/ID, consultar una sola vez el CNJ mediante
   `ProcessosParte`. Verificar el resultado antes de actualizar el ID operativo.
4. Descargar `DownloadProcesso?numeroProcesso=<ID interno>` con la ficha como `Referer`.
5. Validar MIME, firma, tamaño, estructura, páginas, SHA-256 y el CNJ del encabezado de
   la primera página. Publicar por renombrado atómico solo si todo coincide.

La URL original con CNJ se conserva intacta. La URL efectiva se construye por separado:
el servidor requiere el ID interno para `DownloadProcesso`.

Una página `A sessão expirou.` con HTTP 200 provoca, como máximo, una reconstrucción
completa de sesión y una nueva verificación de ficha. Un corte de conexión durante la
descarga tiene la misma recuperación limitada. Los contadores por fase no se reinician
al reconstruir la sesión: bootstrap y ficha tienen tres intentos como máximo, resolución
uno y PDF dos, con los valores predeterminados.

El `is_secret` histórico, los avisos, las restricciones y la ausencia de enlaces no deciden
la accesibilidad. Se omite la descarga si la ficha actual confirma secreto o no permite
determinarlo. Un PDF cuyo CNJ no coincide nunca se publica.

## Almacenamiento

```text
data/
  pdfs/<año>/<CNJ>/     PDF validados, con fecha y huella en el nombre
  adhesion-scraper/     Extracciones, anonimizaciones, estado y manifiestos por fase
  state/               SQLite, bloqueo y estado global de peticiones
  reports/<batch-id>/   Manifiesto e informes
  logs/                Eventos JSONL sin cookies ni HTML
  tmp/                 Descargas temporales
```

El esquema SQLite 3 separa `datasets` (huellas y metadata global), `dataset_records`
(línea y JSON íntegro), `cases` (identidad y datos verificados en el portal), `case_sources`,
`documents`, `batches`, `batch_items` y `runs`. Importar un registro deja `last_checked_at`
y los campos operativos en `null`; no equivale a verificarlo. Los intentos y errores
conservan su historial. Un índice por lote y posición permite consultar ese historial.

La raíz predeterminada es `data/` **del directorio de ejecución**. Se puede fijar con
`--storage-root /ruta/data` o `DATAJUD_STORAGE_ROOT`. Use la misma raíz para reanudar.
Hay un bloqueo por raíz y un solo trabajador. Los archivos del scraper usan permisos
0750 en directorios / 0640 en archivos; los PDF y manifiestos de las fases usan 0600,
con directorios privados de trabajo 0700.

El reinicio de adhesiones archiva los resultados anteriores bajo `data/backups/`, con
inventario, diario de movimientos y copia consistente de SQLite; conserva los originales.
El scraper no borra datos al arrancar, no migra catálogos históricos y rechaza esquemas
anteriores indicando que necesita un almacenamiento vacío. `external/dataset` y las descargas externas se conservan.
El dataset y su metadata se versionan en `external/dataset/`. Los PDF, informes y la base
SQLite generados permanecen en `data/`, excluido de Git.

## Frecuencia, límites y configuración

El intervalo global predeterminado es de tres segundos más hasta dos de variación aleatoria,
persistido entre clientes y lotes de la misma raíz. No reduzca esos valores al usar el portal.
Solo se aceptan HTTPS y redirecciones dentro del host PROJUDI/TJBA, con un máximo de tres.

`Retry-After` se respeta completo, tanto segundos como fecha HTTP. Si supera 60 segundos,
se guarda la fecha de reanudación y se pausa el lote. Incluso una interrupción durante una
espera corta conserva ese límite en SQLite. CAPTCHA, HTTP 401/403 o inicialización
persistentemente fallida pausan el lote; no se resuelven desafíos automáticamente. Cinco
fallos consecutivos de infraestructura también lo pausan. Un fallo aislado de un registro
no impide continuar con los siguientes seleccionados.

Toda configuración admite CLI y variables `DATAJUD_*`, con prioridad
**CLI > entorno > predeterminado**. No se cargan archivos `.env`. `resume` usa la configuración
de su invocación; el lote conserva la configuración con la que fue creado. Use las mismas
opciones si había personalizado la consulta o los límites.

| Opción | Variable | Predeterminado |
| --- | --- | --- |
| `--bootstrap-url` | `DATAJUD_BOOTSTRAP_URL` | `https://projudi.tjba.jus.br/projudi/AcessoPublico?codigoHash=22f20646` |
| `--storage-root` | `DATAJUD_STORAGE_ROOT` | `data` |
| `--user-agent` | `DATAJUD_USER_AGENT` | `DataJudScraper/0.1 (responsible PROJUDI/TJBA client)` |
| `--connect-timeout-seconds` | `DATAJUD_CONNECT_TIMEOUT_SECONDS` | 10 |
| `--page-timeout-seconds` | `DATAJUD_PAGE_TIMEOUT_SECONDS` | 30 |
| `--pdf-timeout-seconds` | `DATAJUD_PDF_TIMEOUT_SECONDS` | 300 |
| `--min-request-interval-seconds` | `DATAJUD_MIN_REQUEST_INTERVAL_SECONDS` | 3 |
| `--max-request-jitter-seconds` | `DATAJUD_MAX_REQUEST_JITTER_SECONDS` | 2 |
| `--max-html-bytes` | `DATAJUD_MAX_HTML_BYTES` | 20971520 (20 MiB) |
| `--max-pdf-bytes` | `DATAJUD_MAX_PDF_BYTES` | 1073741824 (1 GiB) |
| `--min-free-bytes` | `DATAJUD_MIN_FREE_BYTES` | 1073741824 (1 GiB) |
| `--page-attempts` | `DATAJUD_PAGE_ATTEMPTS` | 3 |
| `--pdf-attempts` | `DATAJUD_PDF_ATTEMPTS` | 2 |
| `--lock-timeout-seconds` | `DATAJUD_LOCK_TIMEOUT_SECONDS` | 10 |
| `--log-max-bytes` | `DATAJUD_LOG_MAX_BYTES` | 52428800 (50 MiB) |
| `--log-retention-days` | `DATAJUD_LOG_RETENTION_DAYS` | 30 |
| `--stale-temp-hours` | `DATAJUD_STALE_TEMP_HOURS` | 24 |
| `--challenge-cooldown-seconds` | `DATAJUD_CHALLENGE_COOLDOWN_SECONDS` | 3600 |
| `--contract-mode` | `DATAJUD_CONTRACT_MODE` | `both` |
| `--contract-retention` | `DATAJUD_CONTRACT_RETENTION` | `purge` |
| `--ocr-workers` | `DATAJUD_OCR_WORKERS` | 0 (automático) |
| `--ocr-memory-mb` | `DATAJUD_OCR_MEMORY_MB` | 2048 |
| Solo variable de entorno (OCR neuronal) | `DATAJUD_NEURAL_OCR_DEVICE` | `cpu` (`cuda` requiere el extra GPU) |
| `--local-model` | `DATAJUD_LOCAL_MODEL` | `qwen3.5:27b` |
| `--local-context-tokens` | `DATAJUD_LOCAL_CONTEXT_TOKENS` | 32768 |
| `--local-output-tokens` | `DATAJUD_LOCAL_OUTPUT_TOKENS` | 2048 |


Los timeouts de lectura limitan la espera entre bloques recibidos. El tamaño de los archivos
y el espacio libre se comprueban antes y durante la descarga. Cookies y HTML permanecen
solo en memoria; la retención de los PDF originales sigue la política descrita arriba.

```bash
.venv/bin/datajud-scraper scrape-dataset --help
.venv/bin/datajud-scraper resume --help
```

## Verificación

```bash
.venv/bin/pytest
.venv/bin/ruff check .
uv sync --locked --extra dev --extra contracts --check
```

Las pruebas incluyen un servidor HTTP/1.1 local que exige la misma conexión y cookie,
comprueba más de cinco segundos de inactividad y fuerza un cambio de conexión. También
cubren importación idempotente, selección sin reemplazos, secreto actual, identidad errónea,
ficha sin botón, sesión expirada, CAPTCHA, 429, PDF inválido o ajeno, reparación, interrupción,
publicación atómica y recuperación de estados abandonados. No hacen peticiones a PROJUDI.
También verifican separación de fases, píxeles y coordenadas, los tres modos, retención,
cuarentena, fallos de anonimización, borrado interrumpido y reanudación sin descarga.
Esas pruebas utilizan respuestas sintéticas del modelo; no miden su precisión en documentos reales.
La prueba del dataset completo se omite cuando el dataset local no está presente.

El piloto real revisa además visualmente la primera y última página de cada PDF entregado.
Sus resultados efectivos y la revisión visual se documentan junto a los informes del lote.
