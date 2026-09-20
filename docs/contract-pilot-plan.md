# Piloto de extracción y limpieza de contratos

> Alcance actualizado: [automatización de documentos de tarjetas](card-automation-plan.md).
> Validación manual limitada a 100 expedientes. El flujo y los resultados manuales
> descritos abajo son el historial previo, no el sistema automático solicitado.

## Objetivo y alcance autorizado

Objetivo original: `/home/ERJ6553/.codex/attachments/20fbd6dd-7d17-42f8-a53d-e57ebf273f4e/goal-objective.md`.

El usuario confirmó el 19 de septiembre de 2026:

- Incluir **todos los contratos de crédito, incluidos préstamos**; no limitarse al título
  «Termo de adesão», ni solo a tarjetas RMC/RCC.
- Validar primero **1.000 expedientes**; no iniciar automáticamente el dataset completo.
- Encontrar todos los contratos de cada expediente, incluidos varios contratos por PDF.
- Conservar al final solo contratos limpios, eliminando las páginas ajenas al contrato.
- Eliminar datos personales, fotos y firmas sin borrar contenido contractual.
- Registrar en SQLite presencia/ausencia de contratos y de información sensible,
  distinguiendo expedientes y contratos individuales.
- Validar manualmente extracción y limpieza en el total del piloto.

## Requisitos y evidencia de cierre

| Requisito | Evidencia necesaria | Estado |
| --- | --- | --- |
| 1.000 PDF de una muestra reproducible | Manifiesto, cobertura, PDF válidos, fallos y reservas explícitas | 1.000 descargados; todos los fallos recuperados, sin sustituciones |
| Metodología basada en el corpus completo | Inventario por página/anexo, familias y errores documentados | 1.000 inventariados; 286.006 páginas, 96.825 con OCR y cero fallos de OCR; análisis manual en curso |
| Presencia/ausencia y multiplicidad en SQLite | Migración probada, relación expediente-documento-contratos | Esquema implementado; primeros resultados reales registrados |
| Extracción completa y exclusiva | Revisión manual por expediente, rangos aprobados y errores corregidos | 92 fuentes aprobadas; treinta y dos contratos o fragmentos en veinticinco de ellas; tres fuentes revisadas pendientes por ilegibilidad |
| Datos sensibles por contrato | Estado inicial desconocido, detecciones y revisión de cada contrato | Dieciséis contratos con datos sensibles confirmados y dieciséis sin datos sensibles |
| Limpieza irreversible que preserve el contrato | Máscaras, PDF reabiertos, comparación visual y búsqueda residual | Treinta y dos contratos o fragmentos aprobados, 216 páginas de salida revisadas |
| Revisión manual de todos los resultados | Registro real de páginas inspeccionadas y decisiones; sin autoaprobar | En curso; aún no cubre los 1.000 expedientes |
| Desechar originales y derivados sensibles | Solo después de extracción y limpieza aprobadas para ese expediente | 92 fuentes revisadas descartadas; treinta y dos contratos limpios publicados |

## Estrategia inicial que debe contrastarse con los 1.000 documentos

1. Muestreo de cobertura por año, clase, asunto, ausencia de enlaces y avisos. Un mínimo
   por estrato y resto proporcional; diversidad de juzgados dentro de cada estrato.
   Se informa la sobrerrepresentación de grupos raros. Semilla: `20260919`.
2. Aprovechar índices, destinos y marcadores de PROJUDI para delimitar anexos. Analizar
   también el cuerpo de **todas** las páginas; un título no prueba presencia ni ausencia.
3. OCR local para imágenes y páginas con texto insuficiente. Registrar fallos de OCR y
   páginas pendientes; un error nunca equivale a «sin contrato».
4. Separar candidatos automáticos y decisiones manuales. Comparar límites y páginas
   vecinas; detectar contratos incorporados en anexos de nombre genérico y piezas judiciales.
5. Mantener la relación de cada ocurrencia con el original, incluso duplicados del mismo
   contrato. No deduplicar eliminando ocurrencias sin dejar constancia.
6. Eliminar datos identificativos textuales y visuales con máscaras auditables. Conservar
   entidades financieras, cláusulas, importes, tipos, plazos y demás condiciones del crédito.
7. Limpiar metadatos, anotaciones, formularios, vínculos, adjuntos y capas ocultas. Una
   caja superpuesta no constituye una redacción irreversible.
8. Revisar visualmente todos los contratos antes y después, y comprobar los candidatos
   descartados y los expedientes sin hallazgos para evaluar omisiones.
9. Publicar solo resultados aprobados. Borrar originales y derivados temporales sensibles
   cuando la cobertura de extracción y la limpieza estén confirmadas. No borrar para
   aparentar cumplimiento antes de verificarlo.

## Continuidad

Este archivo conserva el alcance completo. Ningún resultado automático, prueba sintética,
revisión parcial ni lote todavía activo permite marcar el objetivo completo.

El piloto anterior de cinco primeras filas completó 5/5 descargas, 1.840 páginas y
143.821.737 bytes en 59,72 s. Sus PDF aún sirven para explorar las estructuras; no
constituyen la validación de los nuevos 1.000 expedientes.

## Estado operativo de la ejecución

- Lote activo de 1.000: `77248dcf-90a5-427a-bbea-4ba6c14e502c`.
- Estado y handles de ejecución: `data/reports/contract-pilot/state.json`.
- La descarga inicial (`51637`, PID `77289`) terminó por SIGTERM (salida 143) tras
  completar 159 registros. Se reanudó el mismo lote con `resume --retry-failed` en un
  proceso separado con logs persistentes; PID vigente en `state.json` y `download.pid`.
- El descargador se reanudó otra vez tras un bloqueo de SQLite en la posición 323.
  La generación de borradores ahora renderiza fuera de la transacción de escritura
  y vuelve a verificar el estado antes de guardar. El plazo de espera de SQLite pasó
  de 5 a 30 segundos; la fecha de inicio de petición se toma después de obtener el
  bloqueo, para conservar el intervalo entre solicitudes. PID vigente en `state.json`.
- Inventario de la sesión `28590`, PID inicial `82726`: finalizado.
- Inventario de la sesión `30930`, PID inicial `90364`, ocho procesos de trabajo:
  los 1.000 documentos están inventariados. Las reservas en SQLite evitan procesar
  simultáneamente el mismo documento.
- El inventario anterior (`36157`, PID `78586`) terminó. Su caché puede ser reutilizada
  por la versión actual, incluido el PDF de 2.715 páginas.
- Antes de reanudar, comprobar sesiones/PID y sus tiempos de inicio; un timeout de
  observación no prueba que hayan terminado. No lanzar otra descarga de 1.000 por pérdida
  de un mensaje de progreso.
- Última verificación completa del código: 158 pruebas aprobadas, Ruff sin errores y
  paquete wheel construido, incluidas escrituras concurrentes, rechazo de borradores
  cuya revisión cambió y rotación sin interpolación de caras invertidas.
- Extracción, borradores, revisión visual vinculada a hashes y publicación/descarte
  reanudables están implementados. La publicación exige las aprobaciones completas y
  exclusión mutua con el descargador. Se publicaron treinta y dos contratos o fragmentos
  (dos BrasilCard, dos préstamos Santander, Bradesco, cuatro compras financiadas,
  dos fragmentos Itaú, uno Bradescard y tres de Nubank, una adhesión Sorocred y condiciones SafraPay con anticipación
  de recibibles, una CCB de Capital Consig, tres fragmentos Credcesta y condiciones fragmentarias
  de tarjeta Santander y Riachuelo, dos instrumentos de Pefisa y dos apariciones de otra
  adhesión Bradesco con sus condiciones, y un fragmento de condiciones consignadas
  Bradesco reproducido en respuesta al PROCON, y una CCB Neon con dos fragmentos adicionales) y se descartaron 92 fuentes
  completamente revisadas, con sus derivados sensibles.
- La primera pasada terminó con 993 expedientes resueltos y siete errores de descarga
  por timeout, todos de la unidad judicial 0004. Se reintentan esos mismos siete con
  un plazo de 600 segundos por PDF, sin sustituir expedientes de la muestra.
  Esa pasada recuperó cuatro de los siete; los tres restantes se reintentan con
  900 segundos por PDF. Los fallos siguen registrados y no se cuentan como descargas.
  El reintento de 900 segundos recuperó otros dos expedientes: 999 resueltos.
  El reintento de 1.200 segundos recuperó la posición 199 y terminó el lote el
  20/09/2026 a las 06:18 UTC: los 1.000 expedientes originales descargados.
- Primeras observaciones reales y páginas inspeccionadas: [notas exploratorias](contract-discovery-notes.md).

Trabajo todavía necesario: analizar todas las variantes; resolver las fuentes ilegibles
y documentar los adjuntos no incluidos en los PDF; validar manualmente cada fuente y
todas las páginas de los contratos
antes/después; publicar resultados aprobados y descartar los originales correspondientes.
El motor de píxeles y sus pruebas no bastan para dar por satisfecha la anonimización real.
