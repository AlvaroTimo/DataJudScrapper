# Extracción y anonimización automáticas de contratos de tarjeta

El proceso recibe el identificador de un expediente o un manifiesto de expedientes.
No requiere que un operador indique páginas, recortes o máscaras. Las anotaciones
manuales se usan exclusivamente para evaluar sus resultados en 100 expedientes.
La implementación está en validación; todavía no se ha demostrado su precisión sobre
esos 100 casos. El informe conserva los casos pendientes y los errores.

## Preparación

```bash
uv sync --locked --extra contracts --extra dev
.venv/bin/python -m datajud_scraper.ocr_models
.venv/bin/python -m datajud_scraper.ocr_models --quality best
.venv/bin/python scripts/setup_table_ocr.py
.venv/bin/python -m datajud_scraper.card_ocr
.venv/bin/python scripts/setup_local_model.py
```

El instalador de inferencia es para Linux x86_64. Descarga Ollama 0.34.2, comprueba su
SHA-256, lo instala bajo `~/.local/opt/ollama/` y arranca un servidor exclusivamente en
`127.0.0.1:11434`. Usa `qwen3.5:27b`, aproximadamente 17 GB de descarga. Conviene una GPU
con al menos 24 GB de memoria; el rendimiento real depende del equipo y las páginas.
El runtime y los modelos quedan disponibles para otros proyectos, fuera de `.venv`.
No modifica servicios del sistema ni instala paquetes Python globales. Los documentos
y sus datos personales se procesan localmente.

El instalador de OCR de tablas usa los paquetes APT de Ubuntu disponibles en este
equipo, extraídos bajo `~/.local/opt/datajud-tesseract/`. No instala paquetes del sistema.
En otros entornos puede usarse un `tesseract` existente en `PATH`. Se necesitan los
modelos `por` y `eng` de calidad `best`. El motor usa TSV y segmentación dispersa para
obtener coordenadas de píxeles reales de los campos, incluidas tablas con bordes.

Las fotografías se vuelven a leer con RapidOCR 3.9.2 y ONNX Runtime: detector
PP-OCRv6 y reconocimiento latino PP-OCRv5. El comando `card_ocr` descarga y verifica
los tres modelos por SHA-256 en `~/.local/share/datajud-scraper/rapidocr/`. Durante
la extracción solo se usan los modelos locales ya verificados. Las versiones del
OCR, sus modelos y parámetros forman parte de la configuración de cada ejecución.

La implementación usa la API local de [Ollama](https://docs.ollama.com/api/chat) con
[salidas estructuradas](https://docs.ollama.com/capabilities/structured-outputs) y el modelo
[Qwen3.5 27B](https://ollama.com/library/qwen3.5:27b). Las respuestas se verifican antes
de usarse; un JSON válido no garantiza una clasificación correcta.

## Ejecución

```bash
# Perfil del corpus y selección fija de 100 expedientes
.venv/bin/python -m datajud_scraper.card_corpus <batch-id>

# Un expediente: clasificación, delimitación, extracción y anonimización
.venv/bin/python -m datajud_scraper.card_automation --document <document-id>

# Los 100 expedientes reservados para validar
.venv/bin/python -m datajud_scraper.card_automation \
  --manifest data/reports/card-automation/validation-set.json

# Métricas de la validación, incluidas las revisiones aún pendientes
.venv/bin/python -m datajud_scraper.card_validation metrics
```

El corpus contiene 1.000 expedientes. De ellos, 92 originales se habían descartado
durante el procedimiento manual anterior. Los 100 casos de validación se seleccionan
entre originales conservados y sin anotaciones previas; los usados expresamente para
desarrollo se excluyen. Se estratifica por fecha, longitud, predominio de OCR y títulos
contractuales. La selección fija no se reemplaza por casos más fáciles si hay errores.
Los 92 casos históricos y sus 32 salidas no cuentan como validación del sistema nuevo.

El módulo OCR existente crea el inventario por páginas. El proceso automático analiza
el texto y el contexto de los anexos, inspecciona imágenes en las páginas contractuales
o dudosas y separa documentos de tarjeta, fragmentos reproducidos, otros créditos y
documentos ajenos. Los títulos ayudan a buscar, pero no deciden por sí solos.
Las autorizaciones genéricas de consulta INSS/Dataprev y los informes probatorios sin
cláusulas contractuales quedan fuera. Los anexos integrantes de un contrato de tarjeta
se conservan. El usuario confirmó que los seguros solo entran cuando son anexos
integrantes de ese contrato: las propuestas independientes de seguro quedan fuera,
aunque cubran la deuda de la tarjeta o se cobren en su factura. La evaluación aplica
este criterio al contenido del documento reproducido, incluso si el escrito judicial
lo describe de otra manera. El lote actual debe comprobarse contra este criterio;
su ejecución no equivale a una aprobación del alcance.
La numeración impresa une las continuaciones del mismo instrumento;
los fragmentos de contratos distintos presentes en una página se separan.
Si un fragmento está repartido entre varias imágenes incrustadas, el sistema inspecciona
los objetos restantes y los agrupa por instrumento. Sus límites se obtienen del PDF;
no provienen de recortes indicados manualmente.

La anonimización local reconoce valores personales, los alinea con las posiciones del
OCR y detecta visualmente firmas, fotos y otros datos gráficos. Los recortes completos
conservan el cuerpo contractual; las cláusulas dentro de escritos se delimitan aparte.
Se reconstruye cada PDF con los píxeles anonimizados, sin objetos ni capas ocultas del
original. Una segunda lectura busca datos residuales; otra comparación verifica que
las máscaras no hayan borrado condiciones. Estas comprobaciones son automáticas.
Las máscaras de texto siguen cada palabra para evitar que una línea inclinada alcance
la fila contigua. Las relecturas por bandas usan la imagen original y su contexto de
campos; una tabla ya parcialmente borrada puede inducir clasificaciones incorrectas.

## Resultados y reanudación

`data/card-work/<document-id>/<run-id>/` contiene la segmentación, las máscaras, los
PDF limpios y un manifiesto con huellas. Los temporales de texto y respuestas del modelo
pueden contener datos personales y tienen permisos restringidos. No son entregables.

SQLite añade `has_card_contract`, `card_contract_count`,
`card_contracts_had_sensitive_data` y `card_automation_status` en documentos y casos.
`card_automatic_contracts` conserva cada ocurrencia y su indicador de datos sensibles.
`card_automation_runs` identifica la versión de código, prompts y modelo. Los campos y
resultados del alcance anterior se conservan como historial y no se mezclan con estos.

- `automatic_checks_passed`: ejecución terminada y controles automáticos superados.
- `needs_review`: hay incertidumbre de segmentación, datos residuales o posible daño.
- `error`: ejecución incompleta; no se interpreta como «sin contrato».

Repetir el comando reutiliza inferencias y salidas compatibles y verifica sus huellas.
Cambiar código, modelo o resolución genera otra ejecución. Los originales se conservan
mientras el método y sus resultados estén pendientes de validación.
Cada proceso fija la huella del código al arrancar, para que una edición posterior
del proyecto no cambie la identidad de un lote que sigue en ejecución. La anonimización
guarda también un resultado por página; una reanudación compatible puede recuperar
esas páginas sin esperar a que hubiera terminado el contrato completo.

## Evaluación manual de los 100 casos

`card_validation render <document-id>` genera las vistas fuente y limpias, vinculadas
a la ejecución y sus huellas. Renderizarlas no constituye una revisión.
`card_validation record revision.json` registra las páginas realmente inspeccionadas,
los contratos de referencia y los errores observados. No modifica las predicciones ni
las máscaras del proceso automático.

Las métricas distinguen contratos omitidos, documentos ajenos extraídos, límites
incorrectos, páginas con datos personales residuales y páginas con contenido contractual
dañado. La validación solo puede aprobarse con los 100 expedientes revisados bajo la
misma configuración y sin esos errores. Su resultado mide esos casos; no demuestra una
garantía universal de acierto sobre documentos futuros.
Cuando los errores encontrados motivan ajustes del método, estos 100 casos sirven como
validación de regresión: no se presentan como una prueba independiente nunca usada
durante el desarrollo. Cada ajuste exige volver a evaluar la configuración resultante.

## Publicación y descarte automático

Tras superar la validación de los 100 expedientes y los controles de la salida concreta:

```bash
.venv/bin/python -m datajud_scraper.card_publish <run-id>
.venv/bin/python -m datajud_scraper.card_retention <run-id>
```

La publicación copia los PDF verificados a `data/card-contracts/`. El segundo comando
publica si es necesario y elimina el original, OCR, imágenes de revisión, respuestas
del modelo y versiones provisionales del expediente. Conserva las salidas aprobadas y
el manifiesto de auditoría sin texto personal. Registra una intención duradera antes de
borrar; si se interrumpe, el mismo comando retoma el descarte y verifica los contratos.
El scraper reconoce estos resultados conservados sin volver a descargar el original.
Ambos comandos rechazan configuraciones sin la validación completa o con errores.
