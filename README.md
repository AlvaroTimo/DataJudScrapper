# DataJud Scraper

La extracción se limita a **termos y documentos equivalentes de adhesión a tarjetas**,
completos y separados del expediente. El nuevo flujo detecta instrumentos, aplica máscaras
y comprueba limpieza y conservación. Tiene estado independiente, backup verificable,
30 casos de desarrollo y una muestra aleatoria reservada de 25 procesos.
Consulte la [metodología, comandos y evaluación](docs/adhesion.md).
Los controles automáticos y la revisión visual asistida por IA se registran por separado.

**El primer piloto no alcanzó el 80 % requerido.** La ejecución completa del lote quedó
deshabilitada. Consulte los [resultados y limitaciones](docs/adhesion-pilot-20260920.md).

Importa el dataset JSONL de PROJUDI/TJBA y descarga sus PDF consolidados mediante lotes
reanudables. Conserva todos los registros y la metadata original, incluidos campos extra.
La entrada principal es `url_download` de tipo `DownloadProcesso`; esta versión no descarga
documentos individuales de `DownloadArquivo`.

## Instalación

Compatible con macOS y Linux. Desde la raíz del proyecto:

```bash
uv sync --locked --extra dev
```

`.python-version` fija Python 3.14.0; `uv.lock` fija las dependencias y sus hashes.
No es necesario activar el entorno virtual.

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
`already_exists`, sin acceso a la red ni duplicados. Los ausentes o corruptos se reparan.
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
Hay un bloqueo por raíz, un solo trabajador y permisos 0750 en directorios / 0640 en archivos.

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


Los timeouts de lectura limitan la espera entre bloques recibidos. El tamaño de los archivos
y el espacio libre se comprueban antes y durante la descarga. Cookies y HTML permanecen
solo en memoria; los PDF originales se guardan completos.

```bash
.venv/bin/datajud-scraper scrape-dataset --help
.venv/bin/datajud-scraper resume --help
```

## Verificación

```bash
.venv/bin/pytest
.venv/bin/ruff check .
uv sync --locked --extra dev --check
```

Las pruebas incluyen un servidor HTTP/1.1 local que exige la misma conexión y cookie,
comprueba más de cinco segundos de inactividad y fuerza un cambio de conexión. También
cubren importación idempotente, selección sin reemplazos, secreto actual, identidad errónea,
ficha sin botón, sesión expirada, CAPTCHA, 429, PDF inválido o ajeno, reparación, interrupción,
publicación atómica y recuperación de estados abandonados. No hacen peticiones a PROJUDI.
La prueba del dataset completo se omite cuando el dataset local no está presente.

El piloto real revisa además visualmente la primera y última página de cada PDF entregado.
Sus resultados efectivos y la revisión visual se documentan junto a los informes del lote.
