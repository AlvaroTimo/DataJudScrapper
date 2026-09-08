# DataJud Scraper

Descargador estático y responsable del PDF consolidado disponible en la consulta pública de
PROJUDI/TJBA. Esta primera versión procesa un único enlace por ejecución y guarda el número
CNJ, las fechas disponibles, `Assunto` cuando exista, el estado del secreto y los datos técnicos
del PDF.

## Instalación

Compatible con macOS y Linux. Ejecutar los comandos desde la raíz del proyecto.
Se utiliza [uv](https://docs.astral.sh/uv/getting-started/installation/) para crear `.venv`
e instalar el proyecto y las herramientas de desarrollo con las versiones de `uv.lock`:

```bash
uv sync --locked --extra dev
```

`.python-version` fija Python 3.14.0, la versión validada en este Mac. `uv.lock` registra
las versiones y hashes de las dependencias directas y transitivas; el backend de construcción
también tiene una versión exacta en `pyproject.toml`. Con `--locked`, una discrepancia entre
el proyecto y el lock produce un error en vez de actualizar las versiones silenciosamente.
No se necesita activar el entorno para usar los comandos siguientes.

## Uso

```bash
.venv/bin/datajud-scraper scrape \
  'https://projudi.tjba.jus.br/projudi/AcessoPublico?codigoHash=22f20646'
```

La raíz predeterminada es `data/` dentro del directorio desde el que se ejecuta el comando.
Al ejecutarlo desde este repositorio, los datos quedan en `DataJudScrapper/data/`:

```text
data/
  pdfs/<año>/<CNJ>/     PDF validados
  state/               SQLite, bloqueo y estado de acceso
  logs/                Eventos JSONL
  tmp/                 Descargas temporales
```

La carpeta se crea automáticamente y está excluida de Git. Si se ejecuta desde otro directorio,
usar `--storage-root` con la ruta absoluta al `data/` del proyecto para conservar el mismo catálogo.
Se puede cambiar la raíz mediante:

```bash
.venv/bin/datajud-scraper scrape '<URL>' --storage-root /otra/ruta
DATAJUD_STORAGE_ROOT=/otra/ruta .venv/bin/datajud-scraper scrape '<URL>'
```

Una ejecución repetida devuelve `already_exists` sin acceder al servidor. Para solicitar una
nueva captura:

```bash
.venv/bin/datajud-scraper scrape '<URL>' --refresh
```

Si falta un PDF catalogado o está corrupto, se vuelve a descargar. Si el contenido coincide
con la versión anterior, se repara su registro manteniendo su identidad y sin crear duplicados.

El resultado final se escribe como JSON en stdout; el progreso breve se escribe en stderr y
los eventos operativos quedan en `logs/*.jsonl`.

El número CNJ normalizado es la identidad canónica. Si aparece una URL o `codigoHash` nuevo
para un proceso conocido, se añade al historial de orígenes y se actualiza la referencia actual
sin crear otro caso ni volver a descargar un PDF válido.

## Configuración desde terminal

Todos los campos de configuración admiten una opción CLI y una variable `DATAJUD_*`.
La prioridad es **opción CLI > variable de entorno > valor predeterminado**. No se cargan
archivos `.env` automáticamente. Las unidades forman parte del nombre de cada opción:

| Opción | Variable | Predeterminado |
| --- | --- | --- |
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

```bash
.venv/bin/datajud-scraper scrape '<URL>' \
  --pdf-timeout-seconds 600 --pdf-attempts 3

DATAJUD_PAGE_TIMEOUT_SECONDS=60 .venv/bin/datajud-scraper scrape '<URL>'
.venv/bin/datajud-scraper scrape --help
```

Los timeouts de lectura limitan la espera entre bloques recibidos, no la duración total de una
descarga. Los valores inválidos se rechazan antes de crear almacenamiento o hacer peticiones.
Los límites de tamaño, timeouts de red, intentos y tamaño del log deben ser positivos;
las demás cantidades admiten cero. Los PDF históricos no se eliminan automáticamente.

## Comportamiento seguro

- Solo admite enlaces HTTPS de `projudi.tjba.jus.br/projudi/AcessoPublico`.
- Mantiene las cookies únicamente en memoria y no sigue redirecciones externas.
- Serializa las descargas, limita la frecuencia y no intenta resolver CAPTCHA.
- No descarga expedientes marcados con `Segredo de Justiça: SIM`.
- Si el formato no permite determinar el secreto, devuelve `secrecy_unknown` y tampoco descarga.
- `Assunto` es opcional; el ID interno solo se exige para expedientes confirmados como públicos.
- Publica el archivo solamente después de validar tamaño, firma, estructura, páginas y SHA-256.
- No guarda el HTML ni extrae partes, abogados, movimientos o direcciones a la base de datos.
  Los PDF consolidados se guardan completos y pueden contener esos datos.

Antes de procesar enlaces de forma masiva deben confirmarse la autorización, la finalidad, la
retención y la frecuencia aceptable para el portal.

## Desarrollo

```bash
.venv/bin/pytest
.venv/bin/ruff check .
uv sync --locked --extra dev --check
```

Para actualizar dependencias deliberadamente: `uv lock --upgrade`, después
`uv sync --locked --extra dev` y las pruebas. Versionar `uv.lock`, `.python-version`
y `pyproject.toml`; no versionar `.venv/` ni `data/`.
