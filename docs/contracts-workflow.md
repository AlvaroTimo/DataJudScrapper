# Flujo de trabajo del piloto de contratos

> Alcance actualizado: [automatización de documentos de tarjetas](card-automation-plan.md).
> Validación manual limitada a 100 expedientes. El flujo y los resultados manuales
> descritos abajo son el historial previo, no el sistema automático solicitado.

## Estado de implementación

El muestreo, el inventario con OCR, el registro de revisión visual, los controles de
extracción, los borradores de limpieza y la publicación con descarte reanudable están
implementados y tienen pruebas. Los 1.000 expedientes están descargados e inventariados;
la metodología definitiva y la revisión manual completa todavía están pendientes.
El recuento actualizado de descargas, revisiones, contratos y publicaciones está en
`data/reports/contract-pilot/state.json`. Las observaciones y excepciones de cada fuente
están en [las notas de revisión](contract-discovery-notes.md). Los casos terminados
no validan todavía el piloto de 1.000 expedientes.

El alcance y los criterios de cierre están en [el plan del piloto](contract-pilot-plan.md).

## Instalación reproducible

```bash
uv sync --locked --extra dev --extra contracts
.venv/bin/python -m datajud_scraper.ocr_models
.venv/bin/python -m datajud_scraper.ocr_models --quality best
```

Los modelos de portugués e inglés se guardan en
`~/.local/share/datajud-scraper/tessdata/{fast,best}`. El manifiesto distribuido con el
paquete fija la revisión del repositorio oficial, el tamaño y SHA-256 de cada modelo.
PyMuPDF incorpora el motor OCR; este flujo usa los datos de idioma locales sin instalar
un ejecutable de sistema ni enviar documentos a servicios externos.

## Muestra e inventario

```bash
.venv/bin/datajud-scraper scrape-dataset external/dataset/dataset.jsonl \
  --metadata external/dataset/dataset.metadata.json \
  --limit 1000 --sample stratified --seed 20260919

.venv/bin/python -m datajud_scraper.contract_inventory <batch-id> --watch --workers 8
```

El muestreo cubre las combinaciones de año, clase, asunto, ausencia de enlaces y avisos.
Con estos archivos y semilla cubre 245 estratos, 13 años, 7 clases, 50 asuntos y 89 juzgados.
`sampling.json` muestra cuántos registros se seleccionaron en cada categoría. Los perfiles
raros están sobrerrepresentados para descubrir variantes; las proporciones de la muestra
no estiman directamente prevalencias poblacionales.

Se inventaría cada página, incluidos los anexos sin títulos reconocibles. Los límites
proceden de marcadores y enlaces internos del índice. Los títulos y las expresiones del
cuerpo generan candidatos; ninguna regla automática confirma un contrato o su ausencia.

El OCR se aplica a imágenes grandes sin texto reconocible, páginas de texto insuficiente
y algunos casos de texto simulado con gráficos. Se conserva también el texto nativo para
contrastar discrepancias. Los errores de OCR dejan el expediente pendiente. Los avisos
del motor no equivalen a garantía de lectura: la revisión visual sigue siendo necesaria.

Los textos y coordenadas sin limpiar son temporales y privados, en
`data/contract-work/<document-id>/`. El inventario es reanudable, reutiliza el OCR existente
y registra procesos propietarios para evitar escrituras simultáneas sobre la misma fuente.

## Registro de decisiones manuales

```bash
.venv/bin/python -m datajud_scraper.contract_review render-source <document-id> 1-10
.venv/bin/python -m datajud_scraper.contract_review record-source-review decisiones.json
.venv/bin/python -m datajud_scraper.contract_review approve-extraction extraccion.json
```

Renderizar una imagen crea evidencia, **no** una aprobación. El operador debe verla y
registrar `method: manual_visual`, su identificador de revisor, el `view_id`, la decisión
(`contract`, `noncontract`, `mixed`, `uncertain`) y su justificación. Cada imagen está
vinculada a la huella del original y de la propia imagen.

Un expediente solo puede aprobarse cuando se revisaron todas sus páginas y no quedan
decisiones inciertas. El plan de extracción enumera cada ocurrencia contractual y sus
regiones. No permite omitir una página clasificada como contractual ni añadir páginas
rechazadas. Las regiones usan coordenadas normalizadas `[x0,y0,x1,y1]` sobre la página
mostrada; admiten contratos incluidos dentro de otra pieza. Cada región admite
`rotation: 0|90|180|270` en sentido horario, aplicado después del recorte y antes de
las máscaras. La rotación conserva todos los píxeles sin interpolación. Se registra
en el plan y en la evidencia; una región no puede contarse dos veces por girarla.

En SQLite, `has_contract`, `contract_count` y `contracts_had_sensitive_data` comienzan en
`NULL`: desconocido. Los dos primeros cambian tras aprobar la revisión de extracción.
`contracts` conserva cada ocurrencia individual y su estado de limpieza. El esquema de
contratos se extiende de forma aditiva y conserva las descargas del esquema base 3.
Los campos de `cases` describen la última versión válida del expediente; revisar una
versión antigua no aprueba automáticamente una descarga posterior.

### Reutilización de contenido visual idéntico

Los anexos repetidos pueden reutilizar una decisión negativa previa. Primero se indexan
las huellas de páginas inspeccionadas **completas** y pertenecientes a fuentes aprobadas:

```bash
.venv/bin/python -m datajud_scraper.contract_equivalence index
.venv/bin/python -m datajud_scraper.contract_equivalence match <document-id> vistas.json
```

`vistas.json` es la salida de `render-source`. La comparación exige idénticas dimensiones
y todos los píxeles RGB del 94% superior de la página. Genera vistas del 6% inferior
restante, que deben inspeccionarse visualmente; no genera decisiones ni aprobaciones.
No se acepta semejanza de OCR, texto ni hashes perceptuales. El operador comprueba también
que la clasificación anterior se aplica al contexto del nuevo anexo.

La entrada manual de esa página conserva `view_id`, `decision: noncontract` y `rationale`,
y añade `review_basis: exact_body_match`, `reference_id` y `remainder_view_id`.
El registro vuelve a comprobar la igualdad exacta y que la franja revisada cubre todas
las filas restantes. SQLite conserva la procedencia y las huellas en
`contract_reference_bodies` y `contract_page_review_evidence` (esquema de contratos 5).
Una página revisada por referencia no puede servir de nueva referencia. Modificar la
decisión original bloquea la extracción y el descarte de fuentes dependientes.

El descarte autorizado elimina las imágenes, pero conserva esas huellas y la decisión
manual original. Los informes distinguen páginas inspeccionadas completas de páginas
con cuerpo idéntico y franja restante inspeccionada. Los contratos siguen requiriendo
revisión completa de cada salida antes y después de limpiar datos sensibles.

## Redacción irreversible y comprobaciones

El motor `contract_redaction.write_cleaned_contract` crea un borrador independiente a partir
de las regiones aprobadas. Las máscaras, también normalizadas, requieren categoría y
confirmación manual. Las propuestas de CPF, teléfono, correo y número de proceso permanecen
sin confirmar; no cubren por sí solas nombres, fotos, firmas, identificadores ni todos los
casos de OCR. La validación visual de cada contrato es obligatoria.

La salida se reconstruye con imágenes sin pérdida después de eliminar los píxeles
aprobados. Conserva los píxeles exteriores a esas zonas y no arrastra objetos del PDF
original. Se reabre y se comprueba el número de páginas, la huella de cada imagen, la
ausencia de texto oculto, formularios, enlaces, anotaciones, adjuntos y metadatos. Esta
salida conserva la apariencia de las condiciones contractuales, pero no texto seleccionable.

Las revisiones finales se vinculan a las huellas del contenido original, el limpio y las
máscaras. Cambiar una revisión fuente invalida aprobaciones posteriores. Un control de
publicación comprueba además que esos archivos siguen correspondiendo a las huellas
registradas antes de descartar originales.

```bash
.venv/bin/python -m datajud_scraper.contract_outputs render-original <contract-id> 1-3
.venv/bin/python -m datajud_scraper.contract_outputs create-draft mascaras.json
.venv/bin/python -m datajud_scraper.contract_outputs render-output <contract-id> 1-3
.venv/bin/python -m datajud_scraper.contract_outputs record-output-review revision.json
.venv/bin/python -m datajud_scraper.contract_outputs approve-redaction aprobacion.json
```

`mascaras.json` declara `method: manual_visual`, `reviewer`, `contract_id`, `dpi` (240
por defecto), `had_sensitive_data` y una entrada en `pages` por cada página contractual.
Cada entrada contiene `page`, `original_view_id`, `rationale` y `masks`; cada máscara
contiene `rect`, `category` y `confirmed: true`. Los rectángulos se refieren al recorte
contractual mostrado después de girarlo, no a la página judicial completa. Se rechazan vistas de otro
contrato, de otra resolución o que hayan cambiado.

`revision.json` identifica el revisor, método y contrato y contiene decisiones por página:
`page`, `original_view_id`, `cleaned_view_id`, `personal_data_removed`,
`contract_content_preserved` y `rationale`. Ambos indicadores deben ser verdaderos en
todas las páginas para aprobar. `aprobacion.json` contiene método, revisor e identificador
del contrato. Crear imágenes o un borrador nunca completa la revisión manual.

## Publicación y descarte

```bash
.venv/bin/python -m datajud_scraper.contract_release <document-id>
```

Este comando aplica la política de conservación solicitada: verifica todas las revisiones
de origen y salida, publica copias verificadas bajo `data/contracts/<document-id>/` y
registra una intención durable antes de borrar el PDF judicial y su carpeta
`data/contract-work/<document-id>/`. Esta carpeta incluye OCR, vistas personales y
revisiones antiguas. También elimina los títulos de anexos en el inventario de SQLite.
Conserva las huellas, decisiones, coordenadas y procedencia del catálogo. No realiza un
borrado forense del dispositivo ni de copias ajenas al almacenamiento administrado.

Una interrupción durante el descarte deja `source_disposition: purging`; repetir el comando
lo reanuda después de verificar nuevamente todos los contratos publicados. La operación
exige el bloqueo del scraper libre y no puede ejecutarse durante una descarga activa.
Los expedientes con ausencia verificada de contratos también admiten descarte, con
`has_contract: false`, `contract_count: 0` y ningún PDF de salida.

Al reanudar el scraper, una fuente descartada de manera verificada devuelve
`contracts_preserved` con el recuento y las rutas de los contratos conservados. Comprueba
sus archivos antes de responder y no vuelve a descargar el original. `--refresh` permite
consultar una nueva versión; si el contenido es idéntico, conserva el resultado limpio.

## Fuentes técnicas

- [OCR integrado de PyMuPDF](https://pymupdf.readthedocs.io/en/latest/installation.html#enabling-integrated-ocr-support).
- [Lectura de páginas y OCR](https://pymupdf.readthedocs.io/en/latest/page.html).
- [Modelos oficiales Tesseract](https://github.com/tesseract-ocr/tessdata_fast).
