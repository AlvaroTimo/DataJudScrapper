# Piloto de adhesiones — 20/21 de septiembre de 2026

La primera versión está implementada y evaluada, pero **no alcanza el objetivo del 80 %**.
No se ejecutó sobre el lote completo de 908 originales.

| Medida | Resultado |
|---|---:|
| Procesos evaluados | 25/25 |
| Instrumentos en referencia congelada | 12 en 12 procesos |
| Cobertura útil | 0/12 (0.0 %) |
| Fiabilidad de aceptados automáticamente | 0/6 (0.0 %) |
| Recuperación con límites exactos | 7/12 (58.3 %) |
| Precisión con límites exactos | 7/12 (58.3 %) |
| PDF producidos / páginas revisadas | 12 / 48 |
| PDF en cuarentena | 6 |
| Errores de ejecución / revisiones pendientes | 0 / 0 |

El fallo combina omisiones de solicitudes Renner/Realize, rechazo textual de evidencia
visual correcta, delimitación incorrecta de anexos BMG, residuos de firmas/biometría y
borrado excesivo. Cuatro termos PAN de seis páginas conservaron cláusulas y fechas y no
mostraron datos personales residuales, pero perdieron información corporativa del
originador. Otro PAN escaneado perdió fecha, selección y parte de la aceptación.
Por el criterio acordado de conservación, esos resultados no son útiles.

La selección fue uniforme sin reemplazo, semilla 20260920, excluyendo exposición previa
registrada; 30 procesos independientes y tres ejemplos conocidos se usaron en desarrollo.
La referencia observó las 7.340 páginas originales mediante un modelo visual local antes
de las predicciones. Codex adjudicó descripciones y examinó candidatos/ambigüedades.
Todas las páginas de todas las salidas se compararon directamente mediante Poppler.
Fue revisión asistida por IA, no humana; los modelos pueden compartir errores.

La referencia congelada agrupó dos versiones BMG no idénticas como un instrumento.
Esa limitación se detectó comparando salidas y se registró sin cambiar la referencia.
Contar las variantes por separado elevaría el denominador a 13 y daría una cobertura útil
de 0.0 %; no cambiaría la conclusión de fallo. Los pocos positivos y los
diseños repetidos limitan cualquier generalización por emisor.

El backup verificado conserva 10.838 archivos anteriores y una copia consistente de
SQLite en `data/backups/20260920T205604Z`. No se eliminaron originales: 908 disponibles y
92 ausentes desde el flujo anterior, pendientes de recuperación.

La auditoría comprobó que el extractor y la referencia permanecieron sin cambios, los
originales del piloto conservaron su hash y cada PDF contiene solo imágenes a 300 dpi,
sin capas ni estructuras ocultas del original. Eso no subsana los defectos visibles.
Se aprobaron 160 pruebas; Ruff y la comprobación de dependencias bloqueadas pasaron.

Los artefactos reales permanecen fuera de Git:

- [Informe por proceso](../data/adhesion-v1/report.md).
- [Diagnóstico y limitaciones](../data/adhesion-v1/pilot-analysis.md).
- [Índice de todos los PDF y defectos](../data/adhesion-v1/pilot-artifacts.md).
- [Índice de salidas útiles según la revisión](../data/adhesion-v1/usable-pilot-outputs.json).
- [Métricas estructuradas](../data/adhesion-v1/evaluation.json).
- [Auditoría final](../data/adhesion-v1/final-evidence-integrity.json).
- [Decisión de no ampliar al lote](../data/adhesion-v1/full-corpus-decision.json).

Los PDF aceptados automáticamente que fallaron la revisión siguen conservados como
evidencia. No se repararon manualmente ni se sustituyeron casos. Una versión posterior
debe conservar este resultado como regresión y usar otra muestra independiente.

Configuración congelada: `c23a6cdf33a1bee4f65bb2529b587b00b7fee1f5a37c533960f3561b7c6234ba`.
