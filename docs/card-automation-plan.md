# Automatización de documentos contractuales de tarjetas

## Alcance vigente (20/09/2026)

La corrección del usuario sustituye el alcance anterior de todos los créditos:

- Automatizar la identificación, delimitación, extracción y anonimización. Las
  decisiones y máscaras por expediente no son entradas del proceso de producción.
- Usar la muestra descargada de 1.000 expedientes para estudiar las variantes.
- Validar manualmente 100 expedientes, con selección reproducible y resultados
  separados de los ejemplos utilizados para ajustar el sistema.
- Incluir documentos contractuales que regulen la contratación, adhesión, emisión,
  uso o condiciones de tarjetas de crédito. Incluir tarjetas consignadas RMC/RCC y
  documentos con otro título cuando su contenido corresponda a esas tarjetas.
- Excluir préstamos independientes, financiación de compras ajena al contrato de
  tarjeta, adquirencia/anticipación de recibibles, cuentas de pago, facturas,
  ofertas no aceptadas y escritos judiciales que solamente mencionen contratos.
- Conservar cláusulas de tarjeta efectivamente reproducidas como fragmentos,
  identificándolas como tales; no presentarlas como contratos completos.

## Implementación y validación

1. Inventario OCR y segmentación inicial por anexos del expediente.
2. Clasificación semántica automática con contexto de páginas vecinas; selección
   visual de regiones cuando un contrato aparece dentro de otra pieza.
3. Detección automática de identificadores, datos personales, fotos y firmas;
   máscaras vinculadas a posiciones originales, conservando texto contractual.
4. Reconstrucción irreversible del PDF y controles automáticos de contenido,
   metadatos y datos residuales. Incertidumbres y fallos se registran expresamente.
5. Comparación con anotaciones manuales de los 100 expedientes: falsos positivos,
   omisiones, límites de extracción, datos residuales y contenido indebidamente
   borrado. Una salida del modelo no cuenta como revisión humana.

El motor de inferencia se ejecutará localmente. Las versiones de modelo y reglas,
huellas de entradas, decisiones y métricas permitirán reproducir las ejecuciones.
Los originales necesarios para validar se mantienen hasta superar los controles.

## Trabajo anterior

Los 92 expedientes aprobados y los 32 resultados publicados pertenecen al alcance
anterior y fueron procesados con intervención manual. **No demuestran el rendimiento
del sistema automático ni son todos documentos de tarjeta.** Sus etiquetas y casos
difíciles sirven como material de desarrollo. Los originales de esos 92 expedientes
ya habían sido descartados por el flujo anterior; esa limitación se debe reflejar
en la selección y los informes, sin atribuirles una nueva validación automática.

El estado de esta implementación y las métricas se conservarán en
`data/reports/card-automation/`; el historial anterior permanece en
`data/reports/contract-pilot/`.
