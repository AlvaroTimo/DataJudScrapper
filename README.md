# DataJud Scraper

Descargador estático y responsable del PDF consolidado disponible en la consulta pública de
PROJUDI/TJBA. Esta primera versión procesa un único enlace por ejecución y guarda el número
CNJ, las fechas disponibles, `Assunto` cuando exista, el estado del secreto y los datos técnicos
del PDF.

## Instalación

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
```

## Uso

```bash
.venv/bin/datajud-scraper scrape \
  'https://projudi.tjba.jus.br/projudi/AcessoPublico?codigoHash=22f20646'
```

La raíz predeterminada es `/mnt/hdd/datajud-scraper`. Se puede cambiar mediante:

```bash
.venv/bin/datajud-scraper scrape '<URL>' --storage-root /otra/ruta
DATAJUD_STORAGE_ROOT=/otra/ruta .venv/bin/datajud-scraper scrape '<URL>'
```

Una ejecución repetida devuelve `already_exists` sin acceder al servidor. Para solicitar una
nueva captura:

```bash
.venv/bin/datajud-scraper scrape '<URL>' --refresh
```

El resultado final se escribe como JSON en stdout; el progreso breve se escribe en stderr y
los eventos operativos quedan en `logs/*.jsonl`.

El número CNJ normalizado es la identidad canónica. Si aparece una URL o `codigoHash` nuevo
para un proceso conocido, se añade al historial de orígenes y se actualiza la referencia actual
sin crear otro caso ni volver a descargar un PDF válido.

## Comportamiento seguro

- Solo admite enlaces HTTPS de `projudi.tjba.jus.br/projudi/AcessoPublico`.
- Mantiene las cookies únicamente en memoria y no sigue redirecciones externas.
- Serializa las descargas, limita la frecuencia y no intenta resolver CAPTCHA.
- No descarga expedientes marcados con `Segredo de Justiça: SIM`.
- Si el formato no permite determinar el secreto, devuelve `secrecy_unknown` y tampoco descarga.
- `Assunto` es opcional; el ID interno solo se exige para expedientes confirmados como públicos.
- Publica el archivo solamente después de validar tamaño, firma, estructura, páginas y SHA-256.
- No guarda HTML, partes, abogados, movimientos, direcciones ni otros datos personales.

Antes de procesar enlaces de forma masiva deben confirmarse la autorización, la finalidad, la
retención y la frecuencia aceptable para el portal.

## Desarrollo

```bash
.venv/bin/pytest
.venv/bin/ruff check .
```
