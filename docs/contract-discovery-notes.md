# Observaciones exploratorias del corpus

Estado: hipótesis y ejemplos iniciales. No es la metodología validada en 1.000 expedientes.
Los identificadores siguientes son IDs locales de documentos, no datos de personas.

## Un anexo no equivale a un contrato

En `002c480c-081a-4482-92ff-b3a3a653fa0e`, el anexo «Contrato DAYCOVAL» ocupa páginas
26-30. La página 26 contiene una adhesión a tarjeta; la 27 es un comprobante de TED.
Las dos fueron inspeccionadas visualmente. Las cabeceras extraídas de 28-30 también
indican comprobantes, pero su clasificación visual todavía está pendiente. Extraer todo
el anexo incluiría documentos ajenos al contrato.

En `1fa9c409-4b01-466a-bd5e-9e70249763c2`, un anexo denominado «Contrato» ocupa
162-179. La lectura inicial de sus cabeceras encuentra autorización IN100 (162-163),
consentimiento (164-165), adhesión (166-170), solicitud de saque (171-175), dossier
de contratación (176-177) e imágenes (178-179). Las páginas 166 y 171 se inspeccionaron
visualmente y contienen instrumentos contractuales diferentes dentro del mismo anexo.
Las demás páginas necesitan revisión, incluyendo continuidad y relación entre instrumentos.

En `898b4d8f-5325-45a5-a902-a2dded866afd`, un anexo «contrato rcc» ocupa 130-155.
Las cabeceras sugieren adhesión, beneficios, consentimiento, una CCB, condiciones generales,
seguros, autorización de datos, imágenes y un laudo de formalización. La página 137 se
inspeccionó visualmente: es la primera hoja de una CCB de ocho páginas. Deben distinguirse
los instrumentos y sus anexos contractuales de comprobantes, identificaciones y auditorías.

## Variantes y falsos positivos

- Los títulos pueden estar mal codificados, ser genéricos o contener solo números.
- Un «regulamento» puede ser de tarjeta o un reglamento societario/de fondo de inversión.
  La palabra no basta para decidir.
- Una pieza judicial puede citar o reproducir un contrato: buscar solo nombres de anexos
  omitiría contratos; copiar toda la pieza retendría páginas ajenas.
- Solicitudes de saque y CCB pueden coexistir con la adhesión a tarjeta. El alcance
  confirmado por el usuario incluye estos instrumentos de crédito y los préstamos.
- Las condiciones generales y anexos referenciados requieren comprobar su relación con
  el contrato; no deben descartarse por carecer de datos del titular ni incluirse solo por
  aparecer junto a él.

## Implicaciones para limpieza

La página Daycoval revisada contiene campos personales intercalados con condiciones,
un número de contrato, datos de un agente, una firma manuscrita y un pie procesal.
Las páginas PAN separan datos personales/funcionales/bancarios de tarifas y CET.
La CCB BMG intercala la identificación del cliente con cuadros de tipos, importes y plazos.

No sirve borrar una mitad fija de cada página: se perderían condiciones contractuales.
Las máscaras deben respetar los campos y sus límites. Datos identificativos y firmas se
eliminan; tipos, importes, plazos, costes, cláusulas y entidades financieras se conservan.
La revisión final debe incluir nombres de agentes/representantes, pies procesales y números
que permitan volver a identificar al titular, además de CPF, teléfono y correo.

## Evidencia y revisión pendiente

Las cinco páginas observadas tienen imágenes y decisiones reales registradas en
`contract_view_artifacts` y `contract_page_reviews`. Se registraron cuatro páginas mixtas
(contrato y pie procesal) y un comprobante no contractual. **Ningún expediente está aprobado
por completo en esos tres ejemplos.** Estas observaciones no sustituyen la revisión de
todas las páginas de los 1.000 expedientes. Los primeros expedientes completos se detallan
a continuación.

## Revisión completa inicial de un expediente sin contratos

Documento `39387191-2207-45c3-90b9-2a9ca774a223`: se renderizaron y se inspeccionaron
visualmente las 23 páginas, con decisiones individuales vinculadas a sus imágenes.
El expediente contiene portada, demanda, representación judicial y declaración,
evidencia de firma de esos documentos, factura mensual, identidad, consulta SPC y
citaciones. No contiene un instrumento de crédito. La factura enumera cargos, tasas y
operaciones anteriores; estas menciones no constituyen el contrato de adhesión ni un
instrumento de préstamo adjunto. Resultado manual: cero contratos; original y derivados
sensibles descartados después de aprobar la revisión completa. Esto no constituye
validación del resto del piloto.

## Primer contrato extraído y limpiado con revisión completa

Documento `7859f6c1-6554-4ea0-9186-e8613484eae9`: sus 113 páginas se inspeccionaron
visualmente y cada decisión quedó vinculada a una imagen. Hay un instrumento de condiciones
generales de adhesión BrasilCard en páginas 59–67: nueve páginas consecutivas, numeradas
1–9, con cláusulas 1–26. No se encontraron otros contratos de crédito.

Las páginas 39–58 contienen estatutos societarios y certificaciones, aunque usan la palabra
«contrato». La 68 es únicamente una certificación registral. Las citas gráficas breves de
cláusula 2 §6 en los escritos de páginas 31 y 104 reproducen contenido de la página 60;
no constituyen instrumentos adicionales. Esta distinción no autoriza a descartar contratos
completos o fragmentos únicos que estén incorporados en escritos de otros expedientes.

Se extrajeron las nueve páginas con un recorte inferior que elimina el pie judicial externo,
sin cortar texto contractual. Una máscara localizada elimina el bloque de firma digital
del representante (nombre, CPF, trazo y sello temporal) en la última página. Se preservaron
razón social, CNPJ, direcciones y contacto corporativos, fecha del modelo, cláusulas, tasas,
importes y plazos. Las páginas 1–8 mantienen exactamente los píxeles del recorte original.

El PDF de nueve páginas se reabrió y se revisó visualmente completo, también con Poppler.
No contiene texto oculto, formularios, anotaciones, enlaces ni adjuntos. Cada página tiene
una comparación manual antes/después aprobada. Contrato local
`4438fb3e-0a3e-5edc-aa78-9773bb3c106f`: `had_sensitive_data=1`; extracción y limpieza
aprobadas. La copia final está publicada con el mismo SHA-256 aprobado. El original y
todos los archivos de trabajo de ese documento fueron descartados después de verificarla.
Estos resultados no validan todavía el piloto de 1.000.

## Dos préstamos y reproducciones parciales dentro de la contestación

Documento `e6decec9-5608-4cab-a8f4-6cba95c97211`: se revisaron visualmente las 127
páginas. Contiene dos CCB Santander, cada una con diez páginas y un anexo CET de dos
páginas: 66–77 y 79–90. Las páginas 57 y 59 reproducen parcialmente la primera hoja de
cada préstamo dentro de la contestación. Se conserva el recorte de cada reproducción
junto al préstamo correspondiente; no se cuenta como un préstamo adicional. Cada salida
tiene 13 páginas. Las consultas operativas de 78 y 91 y los extractos de 92–100 no son
instrumentos de crédito, aunque mencionen operaciones o números de contratos.

La revisión de limpieza cubrió las 26 páginas originales y las 26 salidas. Se eliminaron
identificadores repetidos en cabeceras, datos personales y laborales, cuentas y agencias,
y códigos y horas de autenticación. Se conservaron fechas contractuales, valores, tipos,
cuotas, plazos, condiciones, datos públicos del banco y campos de firma vacíos. Las dos
reproducciones tienen escalas y posiciones distintas: una máscara inicial dejaba visible
el comienzo de una dirección; se corrigió y se volvió a inspeccionar antes de aprobar.

Los dos contratos tienen extracción y limpieza aprobadas y `had_sensitive_data=1`.
Las salidas se verificaron estructuralmente y se comprobó también su representación con
Poppler en las primeras hojas y en las reproducciones. La publicación y el descarte
quedan pendientes del bloqueo global de descarga; el original se conserva hasta entonces.

## Propuestas, avisos y documentos de cobro sin instrumento de crédito

Documento `a04faf8d-e613-40b6-986f-aece352a4639`: se inspeccionaron las 13 páginas.
La 7 contiene una propuesta de fraccionamiento de factura con aceptación mediante pago,
tasas y CET, junto a publicidad de préstamo. La 9 anuncia cambios en la exención de
anuidade junto a publicidad. Ninguna incorpora un contrato de adhesión completo.
Inicialmente ambas quedaron como `uncertain`. Tras revisar su contenido, el criterio de
trabajo adoptado conserva contratos y sus anexos, y excluye ofertas sin aceptación
documentada y avisos comerciales independientes; el usuario no había respondido a esa
pregunta opcional. Las otras 11 páginas son
portada, demanda, identificación, comprobante de domicilio, factura y representación o
actas societarias. Se aprobó la revisión completa con cero contratos. El original sigue
conservado mientras la descarga mantiene el bloqueo global.

Documento `371102a2-dbe3-403c-8440-2ca6d0894322`: sus 15 páginas se revisaron
visualmente. Contiene demanda, procuración, identidad, factura de electricidad, consulta
de deuda, oferta de liquidación, mensaje de cobro, boleto denominado «ACORDO», inscripción
CNPJ y despacho. El boleto exige pago integral y no incorpora un instrumento de préstamo;
el título «ACORDO» por sí solo no lo convierte en contrato de crédito. Resultado: cero
contratos, con revisión completa aprobada y descarte pendiente del bloqueo de descarga.

Documento `6382072c-26c6-4c2f-9626-03cc520dc33d`: se revisaron visualmente las 64
páginas. Contiene demanda por cargos de un servicio odontológico, facturas de tarjeta,
simulaciones y oferta de fraccionamiento, poderes, actas societarias y certificaciones.
Las menciones a intermediación crediticia en el objeto social y al comité de crédito al
consumidor no son instrumentos de crédito. La oferta de la factura requiere aceptación
por pago, que no está documentada como una operación contratada. Resultado: cero
contratos; extracción aprobada, original conservado hasta el descarte controlado.

Documento `51496f63-6cd7-4c2a-93cf-15be6a94740e`: se inspeccionaron las 76 páginas.
La coincidencia automática en la página 30 corresponde a una solicitud de devolución
de una compra pagada con tarjeta, sin instrumento de crédito. Los comprobantes NuPay
de las páginas 38 y 70 y las facturas fiscales de venta y devolución tampoco constituyen
contratos. El resto comprende escritos procesales, identificación, poderes, estatutos
sociales y resoluciones. Resultado: cero contratos; extracción aprobada tras revisar
todas las páginas, con original conservado hasta el descarte controlado.

Documento `dbaaa496-35c9-4357-9542-9fa24592ac16`: se revisaron las 79 páginas.
El informe denominado «Proposta Abertura de Conta» solo contiene identidad, firma y
fotografías; el informe «Concessão e Limites» es un registro retrospectivo de aceptación
y estado de la tarjeta, sin reproducir el instrumento contractual. Las referencias a
CCB aparecen en facultades de representación del estatuto social. El resto son escritos,
consultas de deuda, historiales de facturas, poderes y actuaciones de ejecución.
Resultado: cero instrumentos de crédito; extracción aprobada, con original conservado
hasta el descarte controlado. La clasificación se basa en las páginas inspeccionadas,
sin pronunciarse sobre la existencia o validez jurídica de la relación discutida.

Documento `22c7cc62-1bc4-4a3e-8211-060794e66901`: se revisaron las 35 páginas.
Contiene demanda, factura telefónica, documentos de identidad, poder y su informe de
firma, consultas SPC/Serasa, comprobantes fiscales y actuaciones judiciales. Las
referencias a préstamos y tarjetas están en la consulta de deudas, sin instrumentos
contractuales adjuntos. Resultado: cero contratos; extracción aprobada y original
conservado hasta el descarte controlado.

## Adhesión Bradesco, condiciones generales y reproducción insertada

Documento `6b28ca59-3e30-4ad1-bc8c-424b4be61078`: se inspeccionaron sus 96 páginas.
La propuesta de emisión de tarjeta de 91–94 y las condiciones generales de 57–90
forman un instrumento. La página 48 reproduce parcialmente la misma propuesta dentro
de la contestación; se conserva su región sin contarla como otro contrato. Las facturas
y ofertas de fraccionamiento de 14–17 y 54–56 no contienen otros instrumentos contratados.

Se extrajeron 39 páginas: cuatro de adhesión, 34 de condiciones y una reproducción
recortada. Se revisaron todas las imágenes originales del contrato a 240 DPI. Se aplicaron
69 máscaras a valores personales, cuentas, identificadores, códigos de barras y firma
electrónica, preservando rótulos, fechas contractuales, banco, límite, anualidades,
consentimientos y cláusulas. Las cinco páginas modificadas se inspeccionaron de nuevo
en su salida limpia. En las otras 34, tanto el SHA-256 de píxeles como el del PNG de la
salida son idénticos a las imágenes ya inspeccionadas; no contienen datos personales
después de recortar el pie judicial. Las 39 comparaciones quedaron aprobadas.

Contrato `9613aeb0-8cff-570a-96b2-b4001458cbb3`: extracción y limpieza aprobadas,
`had_sensitive_data=1`. Se comprobó también con Poppler la salida de las páginas 1, 4 y 39.
Se publicó la versión de SHA-256
`e819caa280c6f6490f206c1501c43d8b5d2621073fe370750179a67a7ff47d98` y se descartaron
su fuente y todos los derivados temporales después de verificar el manifiesto.

## Dos negativos adicionales del piloto

Documento `8614de88-c167-4aba-b173-bd40fdf2ec0f`: las 28 páginas se inspeccionaron.
Contiene una demanda sobre bloqueo de cuenta, documento de identidad, factura eléctrica,
capturas de tarjetas bloqueadas y correos de atención, una sentencia aportada como
precedente y actuaciones de representación y trámite. No contiene instrumentos de crédito.

Documento `bc89c945-4cab-41a0-bc7f-652113352d1a`: las 35 páginas se inspeccionaron.
Contiene demanda, consulta de deudas, identidad, fotografía de un sobre postal, poder,
declaración y certificado de firma, citación, audiencia, sentencia, cálculos y ejecución.
Los números de contrato en la consulta SPC y en la sentencia no son copias de contratos.
Ambas fuentes quedaron aprobadas con `has_contract=0` y `contract_count=0`.

## Menciones a contratos ausentes y cobros de servicios

Documento `68a27d2f-d009-46b8-81ab-f12de62e873d`: se inspeccionaron sus 84 páginas.
Contiene demanda y recurso sobre una anotación de deuda, consultas de crédito,
identificación, poderes, actas y estatutos corporativos, contestación y actuaciones
judiciales. La contestación argumenta que no necesita presentar el contrato original
y solicita su presentación al cedente, pero no incorpora dicho instrumento. Las
coincidencias en alegaciones, estatutos y consultas de deuda no corresponden a contratos.
Resultado aprobado: cero contratos de crédito en el PDF.

Documento `4a417c25-e922-4a18-a1bb-b1a90601a00d`: se inspeccionaron sus 56 páginas.
Contiene una demanda por renovación y cobro de un curso, movimientos de tarjeta,
comprobante de cancelación y reintegro, conversaciones de soporte, identificación,
poderes, citaciones, sentencias, cálculos y ejecución. No incorpora contratos de crédito.
Las páginas 28–30 son notas técnicas que sustituyen audios del curso no convertidos
a PDF; se revisaron las notas, no los audios ausentes. Resultado aprobado: cero
contratos de crédito en el PDF. Ambas fuentes se conservan hasta el descarte controlado.

## Compraventa a plazo con dos vías y reproducción en la contestación

Documento `32f3cb14-fc18-44ec-bf76-3c521784a88d`: se inspeccionaron sus 86 páginas.
Las páginas 69–70 contienen las dos vías del mismo contrato de venta y compra a plazo
de Magazine Luiza, con entrada, cuotas, tasas, seguro, CET y declaraciones de aceptación.
Se conservaron ambas vías y el cuadro contractual reproducido dentro de la contestación
en la página 59, recortando la prosa procesal. La comparación de firmas de la página 61
y las fotografías biométricas son pruebas aisladas, sin condiciones adicionales.
Los términos generales mencionados por número registral no se reproducen en este PDF;
la extracción conserva todo el contenido contractual efectivamente presente.

El contrato `e9b6cc57-9e56-5426-8654-eb5ff177f48b` se cuenta una sola vez y tiene tres
páginas de salida. Se aplicaron 33 máscaras a datos de identificación y contacto,
identificadores de cliente/pedido, nombres de personas, lugar y hora de firma y firma.
Se compararon visualmente las tres páginas originales con las tres limpias, y se
comprobó la representación final de las tres con Poppler. Se preservaron producto,
empresa, importes, tasas, seguro, CET, fechas contractuales y todas las cláusulas tal
como aparecen, sin corregir inconsistencias del documento. Extracción y limpieza
aprobadas; `had_sensitive_data=1`. La publicación y el descarte de la fuente están
pendientes de que el bloqueo de descarga quede libre.

## Excepción de legibilidad en capturas de una aplicación

Documento `88b5b6f8-eb1a-40c6-80eb-c4a39d48ee9b`: sus 72 páginas se inspeccionaron.
La página 42 contiene capturas de propuesta/CET y un probable fragmento de condiciones
de préstamo en un visor de PDF. La imagen embebida que reúne la segunda fila completa
mide solo 485 × 183 píxeles. Se comprobó la resolución nativa y se amplió la página
a 288 DPI; la ampliación no permite verificar el texto ni sus posibles datos personales.
No se encontró una copia legible en las otras páginas. Se registró `uncertain` en esa
página; el expediente no se aprueba, no cuenta como negativo y su original se conserva.
Las consultas operativas y de deuda, simulaciones, alegaciones y documentos de
representación del resto del expediente no son instrumentos de crédito.

## Fragmento aislado de condiciones sin datos personales

Documento `9cba5aae-416e-43e8-bd8b-473fad0a1192`: se inspeccionaron sus 75 páginas.
La página 39 reproduce gráficamente las cláusulas 11 y 11.1 a–c de las condiciones
generales de tarjeta Itaú. Se extrajo solo ese recorte; la lista de anexos en la página
50 menciona un contrato y una propuesta, pero esas copias completas no aparecen.
Las facturas, ofertas no aceptadas, instrucciones de uso y consultas de deuda no
se contabilizaron como instrumentos. La nota de la página 73 representa un archivo
no convertido; la revisión cubre el contenido disponible en el PDF.

Contrato parcial `b62e5cd7-b90f-55ca-9a02-d8a6e37a8468`, una página, extracción y
limpieza aprobadas. Es el primer resultado con `had_sensitive_data=0`: el recorte
no tiene datos de personas ni requiere máscaras. Original y salida se inspeccionaron
visualmente y tienen identidad de píxeles; la salida también se comprobó con Poppler.
Se conserva como fragmento, sin afirmar que el contrato esté completo.

Documento `b5c209f8-35df-4ad7-ae39-f02e39ada84f`: se inspeccionaron sus 33 páginas.
Demanda por bloqueo de cuenta de procesamiento de pagos, soporte y comprobante de
pago, identificación, poder, domicilio, sentencia y ejecución. No hay instrumentos
de crédito. Extracción aprobada con cero contratos.

Documento `9cee74a6-1b34-4f58-b23c-2919156a3761`: se inspeccionaron sus 54 páginas.
Demanda por anotación crediticia y prueba personal, seguida de estatutos y poderes
corporativos de Bradesco. La petición solicita exhibir el contrato, pero no contiene
su copia. Extracción aprobada con cero contratos.

Documento `1282fd96-a02d-410b-beea-0b81ed63017e`: se inspeccionaron sus 55 páginas.
Contiene peticiones por pagos rechazados, fotos de tarjeta y aplicación, conversaciones
de soporte, publicidad de seguro/FGTS, poderes con condiciones de firma DocuSign,
contestación, acto de liquidación y audiencia. La guía de apertura de cuenta menciona
aceptar otro contrato, pero no reproduce sus cláusulas. Cero contratos de crédito.

Documento `4a6cba3c-638b-4e57-b530-44e6aaaa5a62`: se inspeccionaron sus 79 páginas.
Contiene demanda, consultas de deuda, poderes, actos y estatutos societarios Neon,
condiciones DocuSign anexas a un poder y defensa judicial. La defensa menciona una
propuesta de adhesión que no está reproducida; la manifestación, audiencia y sentencia
también señalan ausencia de prueba contractual. La página 75 es una nota de archivo
CTPS no convertido: sólo se inspeccionó la nota. Cero contratos de crédito en el PDF.

Documento `9bb749ff-1119-4cc7-aec2-549656c04dbe`: se inspeccionaron sus 81 páginas.
Contiene demanda por deuda no reconocida, consultas SPC/Serasa, identificación y
domicilio, poderes y documentación societaria de Banco do Brasil. Las menciones de
crédito dentro de competencias de directores y los contratos de indemnidad del
estatuto no son instrumentos de crédito. Extracción aprobada con cero contratos.

Documento `2ae108c6-3272-4f45-9fab-13353b26512e`: se inspeccionaron sus 82 páginas.
Demanda sobre descuentos y uso de tarjeta, facturas Ourocard con ofertas de pago no
aceptadas, extractos de cuenta, poderes, actos societarios y estatutos de Banco do
Brasil. La petición pide presentar el contrato, sin reproducirlo. Cero contratos.

Documento `5777245e-51fc-4b12-94fd-8842912352ec`: se inspeccionaron sus 82 páginas.
Peticiones por pagos rechazados, correos de soporte, capturas de límites y operaciones,
recibo de compra, poderes y estatutos Capital Consig, factura de tarjeta consignada,
audiencia, sentencia y embargos. La mención genérica a crédito consignado en la defensa
no contiene un instrumento; la suscripción de acciones es societaria. Cero contratos.

Documento `bb6ad460-a0c1-4747-99c4-867f46f4febc`: se inspeccionaron sus 23 páginas.
Demanda por deuda no reconocida, consulta Serasa, identificación, poder y su certificado,
boleto AliExpress/EBANX usado como comprobante de domicilio y actuaciones judiciales.
El boleto aislado no es un contrato de crédito. Extracción aprobada con cero contratos.

Documento `97499b8f-5087-4b88-a566-61805f4cdb95`: se inspeccionaron sus 24 páginas.
Aunque la carátula está clasificada como tarjeta de crédito, contiene un exhorto de
ejecución por mercancías, DANFE, boleto, recibos de transporte, protesto de duplicata
sin aceptación y cálculos judiciales. No adjunta un instrumento de financiación.
Extracción aprobada con cero contratos.

Documento `0ff1dda3-1a52-4897-8fbd-07ea52cb637c`: se inspeccionaron sus 69 páginas.
Demanda por deuda no reconocida, prueba personal, consultas de deuda, poderes, estatutos
y contestación de Bradesco. La página 34 contiene una cesión corporativa de bienes,
derechos y pasivos referida a un balance de 1998: no concede crédito ni reproduce las
condiciones de préstamos o tarjetas. El boleto de propuesta de compra tampoco acredita
una contratación de crédito. Extracción aprobada con cero contratos.

Documento `f55d52ec-14ac-4988-ac21-521550620c86`: se inspeccionaron sus 82 páginas.
Demanda por deuda no reconocida, poderes, certificado de firma, identificación,
factura de telecomunicaciones, consulta Serasa y documentación societaria de Banco
do Brasil. Las referencias a crédito en competencias de directores y el contrato
de indemnidad del estatuto no constituyen instrumentos de crédito. Cero contratos.

Documento `72097fdd-ae0a-4c27-bb9b-27e4cc5df558`: se inspeccionaron sus 83 páginas.
Peticiones por compras rechazadas, factura Bradescard con ofertas no aceptadas,
capturas de operaciones y soporte, poderes, estatutos Will, defensa y actuaciones
judiciales. El infográfico de apertura de cuenta menciona aceptar otro contrato sin
reproducirlo. La página 24 es una nota de vídeo no convertido: sólo se inspeccionó
la nota. Extracción aprobada con cero contratos en el PDF.

Documento `efd6a218-18bc-4757-8253-66699f8d51ef`: se inspeccionaron sus 84 páginas.
Demanda por entrega de tarjeta y seguros, identidad, comprobantes, mensajes de soporte,
seguimiento postal, noticias, extractos y documentos societarios de Itaú. Las capturas
de límites y entregas no reproducen contratos; los movimientos de pagos de préstamos
no son los instrumentos. Las páginas 32 y 47 son notas de audio/video no convertidos;
solo se revisaron esas notas presentes en el PDF. Cero contratos en el PDF.

Documento `9843ec7a-1b1d-42ee-bd28-e4895cc256ed`: se inspeccionaron sus 73 páginas.
Petición por deuda no reconocida, documentos personales y de representación, protocolos
de incorporación de Banco Olé y Bosan a Santander, balances y auditorías. Los protocolos
son actos societarios y no instrumentos de crédito. El boleto de propuesta de compra
opcional y las consultas de deuda tampoco contienen contratos. Cero contratos en el PDF.

Documento `5d6809a1-6241-42c5-90c2-c7c03cff99c7`: se inspeccionaron sus 76 páginas.
Demanda sobre sobregiro y pagos de tarjeta Bradesco, identificación, poder, fotografías
de tarjeta y extractos bancarios. Los estatutos, poderes y cesión de activos/pasivos
Bradesco/BACC de 1998 son documentación corporativa, no contratos de crédito. Cero contratos.

Documento `9b1109f7-6b2a-4649-b4ae-e163582b435f`: se inspeccionaron sus 58 páginas.
Demanda por reducción de límite Santander, facturas con ofertas no aceptadas,
comprobantes, poderes, nombramientos de directores y publicaciones societarias.
Los avisos de tasas y CET de futuras operaciones no son instrumentos de crédito.
Extracción aprobada con cero contratos.

Documento `2ef584b6-84d9-4975-8292-a75b48f9be4d`: se validaron sus 85 páginas.
63 se inspeccionaron completas. En 22 se verificó que el 94% superior coincide
píxel por píxel con páginas previamente revisadas completas y se inspeccionó
visualmente el 6% inferior restante. Las referencias y hashes se registraron en SQLite.
Contiene litigio por pagos Will rechazados, fotos de identidad, alquiler inmobiliario
(no crédito), capturas de compras/soporte, poderes, estatutos y actuaciones judiciales.
El infográfico de apertura remite a aceptar otro contrato, sin reproducirlo.
La página 26 es una nota de vídeo no convertido; solo se revisó la nota.
Extracción aprobada con cero contratos de crédito en el PDF.

Documento `cf6565b3-2cb7-4dcb-8686-e7f95fa523d6`: se validaron sus 86 páginas.
68 se inspeccionaron completas y 18 mediante cuerpo idéntico a una referencia aprobada
más inspección visual completa del resto inferior. Contiene demanda por reducción de
límite Itaú, facturas con simulaciones/ofertas no aceptadas, documentos personales,
poderes, publicaciones societarias, defensa y actuaciones judiciales. Las menciones a
condiciones generales en jurisprudencia no reproducen el instrumento. Las páginas 33,
34 y 81 son notas de vídeos no convertidos; solo se revisaron las notas del PDF.
Extracción aprobada con cero contratos de crédito en el PDF.

Documento `0412a735-b8c0-427a-b002-616d24d6fb54`: se inspeccionaron sus 41 páginas.
Contiene una reclamación por televisión defectuosa, alquiler residencial como prueba
de domicilio, documentos personales, PROCON, órdenes de reparación, recibos de compra,
poderes y un acuerdo judicial de devolución del precio e indemnización. El alquiler
y el acuerdo de pago único por daños no constituyen contratos de crédito. Cero contratos.

Documento `8de697a9-efb8-432a-94c9-28e981f3c3c5`: se inspeccionaron sus 62 páginas.
Se localizaron dos ventas financiadas distintas: Banco do Brasil en cuatro paneles
reproducidos dentro de la contestación (página 42) y Safra con dos caras del contrato,
ficha de aprobación y CET (páginas 47–50, ordenadas 50, 49, 48, 47). Se extrajeron
cinco páginas sin el texto judicial circundante. Ambos contratos contenían datos
personales, códigos y firmas; se revisaron originales y resultados limpios, también
con Poppler. Las máscaras preservan importes, cuotas, tasas, fechas, CET y declaraciones.
Se corrigió una máscara que alcanzaba parte de la declaración del CET antes de aprobar.
Los trazos aislados superpuestos a texto contractual se delimitaron sin reconstruirlo
ni borrar las condiciones. El control OCR complementario no confirmó identificadores
residuales; un aviso era una subcadena de la palabra «ficarão». Dos contratos aprobados.

Documento `1fb29c78-e44f-4604-ad38-2efafd5ab49f`: se inspeccionaron sus 64 páginas.
Las páginas 45–46 contienen las dos caras de una adhesión Sorocred bajo el título
«Ficha cadastral – pessoa física». La cara 46 está invertida; se ordenó primero,
rotada 180 grados sin interpolar. Se preservan los bordes de ambas fotografías,
la declaración, el vencimiento, SCR, aceptación de adhesión y seguro opcional.
La comparación de firma en la defensa no añade cláusulas a estas caras completas.
La cesión de cartera institucional Sorocred–Itapeva no concede crédito al consumidor.
Un contrato extraído, 37 máscaras y dos páginas limpias revisadas a 240 DPI y con
Poppler. La búsqueda complementaria de 11 tokens personales reconocidos en el anverso
no encontró coincidencias en ninguna cara limpia; el OCR del reverso no reconoció
de forma fiable sus identificadores originales. La decisión se apoya en la comparación
visual de ambas caras, no en la ausencia de coincidencias OCR por sí sola.

Documento `5e094175-1850-4ac4-802b-409c607909b5`: se inspeccionaron sus 43 páginas.
Contiene demanda por deuda desconocida con Banco do Brasil, consulta Serasa, documentos
personales, poder y certificado de firma, un boleto opcional de cosméticos, citación,
acta de audiencia, actos societarios, poderes del banco y extinción por abandono.
No contiene un instrumento de crédito. Extracción aprobada con cero contratos.

Documento `50206260-d0e3-40ab-ba60-7a30cd7e706d`: se inspeccionaron sus 69 páginas.
Contiene reclamación por seguro cobrado en tarjeta C&A, facturas y comprobantes,
documentos personales, actos societarios, poderes, contestación, sentencia y pago
de la condena. El panel de adhesión a seguro y los anuncios de la página 49 no
reproducen un instrumento de crédito. Cero contratos de crédito en este PDF.

Documento `d47cc2b5-d6de-4fcf-bfa6-5993d68f0349`: se inspeccionaron sus 32 páginas.
Es un cumplimiento provisional de sentencia sobre cambio de tarjeta Itaú. Contiene
escritos judiciales, decisiones, capturas del estado de tarjeta y de facturas,
comprobantes de pago, poder y certificado de firma. Las decisiones mencionan
el contrato del proceso principal, pero no lo reproducen. Cero contratos en este PDF.

Documento `34772da4-552f-47da-ac3c-34a838eb00c8`: se inspeccionaron sus 69 páginas.
Contiene una reclamación por imputación de pagos de Mercado Pago, documentos personales,
facturas, comprobantes, capturas de la aplicación y conversaciones con asistencia.
Las ofertas de cuotas en las facturas son condicionales y no muestran aceptación;
las notificaciones y la pantalla para simular un préstamo tampoco reproducen un
instrumento. Las resoluciones judiciales solo relatan la relación y los cargos.
Cero contratos de crédito reproducidos en este PDF.

Documento `dca4eb84-4257-4dff-a4e4-fe050b74b83b`: se inspeccionaron sus 75 páginas.
En el anexo «Atos Constitutivos», después de poderes y estatutos, aparecen las 29
páginas del contrato SafraPay de credenciamiento de comercios (37–65). Se conserva
completo por sus condiciones de anticipación de cobros ARV (cláusula 11), con las
cláusulas de restitución, remuneración, mora, SCR y anexos. Se clasifica como
`acquiring_and_receivables_advance_conditions`: formulario general para comercios,
sin acreditar una operación individual ni una adhesión de tarjeta de consumidor.
El recorte elimina exclusivamente el pie judicial ajeno al instrumento. Las 29
regiones se revisaron visualmente y no contienen datos personales ni firmas; los
datos institucionales y fechas del modelo se conservan. Se verificó identidad exacta
de píxeles entre cada página final y su vista inspeccionada, sin máscaras, y se
inspeccionaron además con Poppler las páginas 1, 8 y 29. Un contrato aprobado.
El acuerdo judicial indemnizatorio y las consultas de cancelación de deuda no son
contratos de crédito adicionales.

Documento `a3a8d30a-f64d-4300-bf0b-6b051a0bf054`: se inspeccionaron sus 70 páginas.
La contestación PAN afirma aportar adhesión y autorización de saque, pero en este
PDF solo aparecen sus alegaciones, infografías comerciales y ejemplos de la firma
biométrica. No reproduce el instrumento ni sus cláusulas. Las facturas, recibos,
poderes y actuaciones finales tampoco lo contienen. La nota de vídeo no convertido
se registra como tal, sin atribuirle una revisión del vídeo. Cero contratos en el PDF.

Documento `f0575607-4792-43df-aaaa-d836b49a2304`: 77 páginas inspeccionadas,
67 completas y diez mediante cuerpo idéntico a referencias aprobadas más inspección
del pie restante. La página 52 contiene las mismas tres imágenes del flujo de
préstamo de la página 42 de `88b5b6f8-eb1a-40c6-80eb-c4a39d48ee9b`.
Se extrajeron los bitmaps originales y se inspeccionaron ampliados sin interpolación:
la vista del PDF contractual tiene demasiado pocos píxeles para leer sus cláusulas.
La repetición indica material ilustrativo, pero no basta para excluir el fragmento
contractual ni para validar su limpieza. Ambos expedientes permanecen pendientes,
con originales retenidos; no se computan como ausencia de contrato aprobada.

Documento `db7fb378-6c44-4a65-b122-bdba53337f5e`: se inspeccionaron completas sus
86 páginas. Contiene demanda por negativación, poderes, estatutos y actas del banco,
contestación, consultas SPC, recursos y pagos de Bolsa Família. El anexo «BANCO
BRADESCO.pdf» solo aporta sustitución y preposición. Los escritos mencionan una
deuda, pero no reproducen el contrato ni sus condiciones. Cero contratos en el PDF.

Documento `5ff04ae9-328a-4b2d-a819-062895e46bb5`: 29 páginas inspeccionadas
completas. Demanda contra Consult/Estácio por registro de deuda, consulta SPC,
documentos personales, factura eléctrica, poder, declaraciones y actos judiciales.
No contiene un instrumento de crédito ni sus condiciones. Cero contratos en el PDF.

Documento `989fb123-5dde-4e3a-a8c4-de342570d4ed`: 44 páginas revisadas, 34
completas y diez con cuerpo idéntico a referencias aprobadas y pie inspeccionado.
Contiene demanda contra Bradescard, consulta SPC, identificaciones, boleto de factura
DM, poderes, acta societaria y actos de citación. La consulta enumera contratos,
pero no los reproduce; el boleto tampoco aporta un instrumento. Cero contratos.

Documento `02605f1b-26e7-4bef-aca4-869194503c6c`: 82 páginas revisadas, 23
completas y 59 con cuerpo idéntico a referencias aprobadas y pie inspeccionado.
Contiene demanda contra Banco do Brasil, consulta Serasa, identificación, poder,
boleto opcional de cosméticos, representación, estatutos y actos judiciales. Los
cuerpos repetidos son documentación societaria y poderes; todos los pies cambiantes
son certificaciones judiciales. No contiene el contrato alegado. Cero contratos.

Documento `e46cf24e-3d4b-43a1-89a8-256b38a32f66`: 76 páginas revisadas, 58
completas y 18 mediante cuerpos idénticos a referencias aprobadas más pies inspeccionados.
Demanda por compras rechazadas, capturas de compras y soporte, contestación, poderes,
estatutos y actos de liquidación. La infografía de la página 45 describe registro y
uso de la aplicación y remite a un contrato distinto; no reproduce el instrumento.
Las condiciones DocuSign corresponden a poderes corporativos. Cero contratos de crédito.

Documento `b1d60856-4db6-401a-8685-ecc65e88ae2e`: 84 páginas revisadas, 76
completas y ocho mediante cuerpos idénticos de poderes ya aprobados más pies
inspeccionados. Demanda por tarjeta entregada bloqueada, facturas con opciones de
pago no aceptadas, comprobantes de pago, fotografía de sobres, poderes, estatutos,
contestación y consulta Serasa. No reproduce adhesión ni condiciones contractuales.
Cero contratos de crédito en el PDF.

Documento `b0571aff-3f20-460b-a41e-c6cd9edd415a`: 90 páginas inspeccionadas
completas. La contestación Nubank incluye dos fragmentos de condiciones de tarjeta,
cláusula 4.8.2 en la página 39 y sección 4.11 con sus tres subcláusulas en la página 34.
Se extraen como un instrumento parcial de dos páginas, en orden de cláusulas.
Los DDC posteriores son resúmenes informativos de saldos y evolución de deuda;
las capturas de alta contienen enlaces a contratos, sin reproducirlos. No aparece
el instrumento completo en los anexos. Ambos recortes se inspeccionaron a 240 DPI,
antes y después de reconstruir la salida: texto íntegro, sin datos personales,
sin máscaras y con identidad exacta de píxeles. Limpieza aprobada y fragmento publicado con hash verificado; original y derivados
sensibles descartados después de la validación.

Documento `2c490bf3-af12-47a6-afc1-68bfd88d14d0`: 89 páginas revisadas,
49 completas y 40 con cuerpo idéntico por píxeles a referencias aprobadas más pie
inspeccionado. Contiene reclamación contra Will por compras rechazadas, capturas
de límites, bloqueos y transacciones, poderes, estatutos, contestación, audiencia,
sentencia y ejecución vinculada a liquidación. La infografía de registro remite
a otro contrato, sin reproducirlo. Las condiciones DocuSign son del poder judicial.
La página 18 es una nota de vídeo no convertible; no se atribuye revisión al vídeo
ausente del PDF. Cero contratos de crédito en las páginas del PDF.

Documento `09c180bb-175f-47f3-911d-965a668b71a8`: 79 páginas inspeccionadas
completas. La demanda y contestación mencionan una deuda, pero aportan consultas
Serasa/Boa Vista y documentación de representación, sin reproducir el instrumento.
Los anexos contienen estatutos, actas corporativas y poderes. La petición final
incluye consultas internas de bloqueo de deuda por cumplimiento de sentencia;
no son adhesión ni condiciones de crédito. Cero contratos en el PDF.

Documento `fca612cf-9a86-42b4-ae91-d78fa514a144`: 88 páginas inspeccionadas
completas. Queja sobre facturas Bradescard, documentos personales, facturas con
ofertas de parcelamiento no aceptadas, fotografías de tarjeta, comprobantes de pago,
actos judiciales, estatutos y poderes. Los movimientos incluyen cuotas de un
parcelamiento automático anterior, pero no el instrumento ni sus condiciones.
Los recibos de septiembre y diciembre acreditan pago íntegro de las facturas.
Cero contratos de crédito en el PDF.

Documento `4a4b72f8-4565-48eb-a1a7-5b4be3439d06`: 88 páginas revisadas,
79 completas y nueve con cuerpo idéntico a referencia aprobada más pie inspeccionado.
La defensa menciona un préstamo y aporta un DDC de tres páginas emitido en 2026,
con saldos y amortizaciones de una operación de 2023. El cierre aclara expresamente
su carácter informativo a la fecha de emisión; la referencia a una firma móvil
anterior no reproduce el instrumento ni una aceptación. Los otros anexos son
extractos bancarios, consultas, documentos personales y representación judicial.
Cero contratos de crédito en el PDF.

Documento `050da2fb-f80b-433f-b23f-05d9f9366f36`: 90 páginas inspeccionadas
completas. La demanda contra Banco Inter solicita exhibir una contratación digital,
pero no incorpora el instrumento. El extenso anexo contiene actas, estatutos,
política de dividendos, aprobaciones del Banco Central y certificados registrales;
las referencias a CCB y límites de crédito describen facultades de administración.
Los otros anexos son documentos personales, consultas de deuda, un boleto de
propuesta sin pago acreditado, poderes y resoluciones judiciales. Cero contratos
de crédito en el PDF.

Documento `e72ff81a-dbae-4c0e-bbf0-981cfce58d7a`: 90 páginas revisadas,
78 completas y 12 con cuerpo idéntico a referencia aprobada más pie inspeccionado.
La defensa Bradesco alega préstamo consignado, tarjeta y límite de crédito, pero no
reproduce instrumento ni condiciones. Los anexos son estatutos, publicaciones
societarias, poderes, documentos personales y fiscales, consultas de deuda y
actos judiciales. Las dos páginas de diario se comprobaron también en ampliaciones
a 300 DPI; su escaneo es degradado. Cero contratos de crédito en el PDF.

Documento `84ee7aa0-3e24-478d-90a3-e02ca9431311`: 91 páginas inspeccionadas
completas. Las páginas 66–74 contienen las nueve páginas de condiciones BrasilCard
de 30/06/2025, cláusulas 1–26. Se conserva un contrato genérico completo, con
recorte inferior del pie judicial. Sus nueve regiones se inspeccionaron a 240 DPI:
no contienen datos personales del titular y no requieren máscaras. La salida
reconstruida conserva exactamente todos sus píxeles; limpieza aprobada, publicación
completada y fuente original descartada tras verificar el manifiesto y el hash.
El «Termo de adesão» de la página 32 pertenece al sistema judicial PROJUDI.
El contrato Guardian de las páginas 75–84 regula asistencia funeraria independiente,
sin otorgar crédito; el pago mediante tarjeta no modifica esa clasificación.
La autenticación Unico corresponde a documentos de identidad y no reproduce una
adhesión crediticia. Las páginas 88–89 son avisos de audios no convertibles; los
audios no están contenidos en el PDF y no se atribuye revisión a su contenido.

Documento `a8a6bfa3-0b81-4399-b9ae-282fecae6c35`: 92 páginas revisadas,
62 completas y 30 mediante identidad exacta del cuerpo con una referencia aprobada
más inspección visual del pie residual. El proceso Will sobre compras rechazadas
contiene facturas, movimientos, consultas, poderes, estatutos, escritos y decisiones.
La factura de septiembre muestra ofertas opcionales de parcelamiento, pero el
historial acredita el pago íntegro de 374,83; no reproduce un acuerdo aceptado.
La tabla informativa de intereses de la factura y la infografía de alta tampoco
constituyen un instrumento de crédito. La ejecución final se refiere a la
indemnización judicial y a la liquidación del banco, no a un préstamo. La página 19
es una nota de vídeo no convertible; el vídeo no está contenido en el PDF y no se
atribuye revisión a su contenido. Cero contratos en las páginas del PDF; aprobación
registrada y fuente descartada después de verificar su manifiesto de publicación.

Documento `73b38a9d-c71c-4d53-aa13-fad13d06a312`: 92 páginas inspeccionadas
completas. La página 42 reproduce como imagen la sección 4.11 y las cláusulas
4.11.1–4.11.3 de las condiciones de tarjeta Nubank. Se conserva ese fragmento
completo, excluyendo el rótulo explicativo y las alegaciones de la contestación.
La inspección del recorte y la salida a 240 DPI confirma que no hay datos personales
ni firmas y que se mantienen mora del 1%, multa del 2% y las demás condiciones.
Es un fragmento, no el contrato completo. Los anexos 70–76 son DDC retrospectivos
de saldos vencidos y no contienen el instrumento de contratación. Las pantallas
genéricas remiten mediante enlaces al contrato sin reproducirlo; la biometría
corresponde a recuperación de cuenta. Los demás anexos son extractos, poderes,
actas societarias y actuaciones judiciales. Un fragmento publicado, limpieza
aprobada y fuente descartada después de verificar el manifiesto y el hash.

Documento `0b321d24-5e7d-4eac-a98d-85df4db5ec8c`: revisión de las 93 páginas,
62 completas y 31 mediante identidad exacta con referencias aprobadas más
inspección del pie residual. Las páginas 60–61 reproducen el mismo tipo de flujo
móvil diminuto de Bradesco observado en otras fuentes pendientes: CET, miniatura
de condiciones y cierre. Sus bitmaps originales, de 477×178, 485×183 y 383×226,
siguen siendo ilegibles al ampliarlos sin interpolación. Ambas páginas quedan
marcadas como inciertas, sin aprobar extracción ni descartar la fuente. El DDC
72–73, emitido en julio de 2026 sobre una renegociación de 2022, es expresamente
informativo y retrospectivo, no el instrumento original. El resto son escritos,
extractos, consultas, identificación, poderes y actuaciones judiciales.

Documento `04403333-7c73-4181-a8cf-4bf651c9d058`: 93 páginas inspeccionadas
completas. Pese al asunto «Cartão de Crédito» del dataset, el proceso trata de
deudas de electricidad de Coelba. Sus anexos contienen consultas SPC, facturas
mensuales de internet y alquiler de equipo, identificación, poderes y extensas
listas de números de procesos. No contienen financiación ni contratos de crédito.
Cero contratos; aprobación registrada y fuente descartada tras verificar el
manifiesto de publicación.

Documento `d95cb79e-c4a6-484a-9eb6-a8dcbfa87857`: 94 páginas inspeccionadas
completas. La página 55 contiene una CCB Luizacred de compra financiada de notebook;
las páginas 61 y 66 reproducen fragmentos del mismo instrumento dentro del informe
de fraude. Se conservan como un solo contrato de tres páginas, excluyendo el texto
ajeno de ese informe y los pies judiciales. La CCB menciona condiciones registradas
en 2010 que no aparecen anexadas. La imagen de la página 49 solo repite una firma
para cotejo; la firma y su contexto contractual ya están en la CCB completa.
La consulta de garantía extendida no reproduce un contrato de crédito; el acuerdo
final de diez pagos corresponde a costas judiciales. Revisión antes y después
a 240 DPI y comprobación Poppler: 37 máscaras eliminan datos personales,
identificadores y firmas, conservando principal 838,50, diez cuotas de 115,00,
tasas 5,69% mensual y 94,27% anual, IOF 2,48%, CET 94,81%, fechas y cláusulas.
La búsqueda OCR residual complementaria no encontró los identificadores comprobados.
Un contrato limpio publicado y fuente descartada tras verificar el manifiesto y
el hash. El vídeo de la audiencia no está contenido en el PDF y no se atribuye
revisión a su contenido.


### Fuente fb286767-7c0a-4b76-bfb5-eacb6d443d25 (95 páginas)

Revisión completa: 59 páginas inspeccionadas íntegramente y 36 con cuerpo de píxeles
exactamente idéntico a referencias negativas ya revisadas, más inspección visual de
las 36 franjas judiciales restantes. La página 74 reproduce la cláusula I de adhesión
al reglamento de tarjeta de Bradesco/Bradescard. Se conserva solo el recuadro
[0.244, 0.563, 0.773, 0.68]; el reglamento completo no está en este PDF. Las pantallas
de las páginas 64–66 son ejemplos de cancelación, sin instrumento crediticio.

Fragmento a26b2791-9bf1-5aff-9cc6-6e0d61db7e8c: una página sin datos personales ni
máscaras, inspeccionada antes y después a 240 DPI y con Poppler. Igualdad exacta de
píxeles; salida sin capas textuales, enlaces, anotaciones, adjuntos ni metadatos
personales. SHA-256 publicado: 12dc61806b207cee4465f31165fa1c1d676aa4a9b3d8a53306a41be988263504.
El acta remite a un vídeo externo no incluido en el PDF; no se declara revisado.
Original y derivados temporales descartados tras verificar el manifiesto.

### Fuente 3c45595e-a29e-4b0e-8955-d74bf0fa11ad (96 páginas)

88 páginas revisadas completas y ocho por identidad exacta del cuerpo con páginas
negativas previamente inspeccionadas, más revisión visual de sus franjas restantes.
La página 77 reproduce los items 3 y 3.2 de las condiciones de tarjeta Itaú. Se
conservan como una sola ocurrencia de dos recortes; la frase final del item 3 ya
termina en «con-» en la imagen original. El PDF no contiene su continuación ni el
reglamento completo. Las facturas pagadas, consultas de saldo y ventana informativa
de tasas no constituyen otros instrumentos de crédito.

Fragmento 7d5988b1-ec9c-5fcb-baa9-60bdcd741b45: dos páginas inspeccionadas antes y
después a 240 DPI, sin datos personales ni máscaras. Igualdad exacta de píxeles y
PDF reabierto sin texto oculto, enlaces, anotaciones, adjuntos ni metadatos personales.
SHA-256: b142705c9faf6a9936b186140bbe1e4b0e3e67d4a4a22f860f44d7cc2b39767c.
El vídeo externo citado en el acta no está en el PDF y no se declara revisado.
Fuente y derivados sensibles descartados tras verificar la publicación.

### Fuente ed006a64-66a5-4eca-bee0-0db6f63c0844 (96 páginas)

73 páginas revisadas completas y 23 mediante identidad exacta del cuerpo respecto a
referencias negativas independientes, con las 23 franjas restantes inspeccionadas.
Cero contratos. Las imágenes de la contestación son alta genérica con enlaces,
funcionamiento de pagos, historial de transacciones, estado activo de tarjeta,
canales de soporte y jurisprudencia. Las condiciones DocuSign pertenecen al poder
corporativo. No aparecen instrumentos de adhesión ni financiación.

La página 20 certifica un archivo ausente del PDF bajo el título de comprobante de
residencia; el acta también refiere una grabación externa. No se declara revisión
de esos contenidos. Fuente descartada tras aprobar la revisión y verificar el
manifiesto sin contratos.

### Fuente 1a8400f7-5a23-47fa-b48a-2bda2e958c9f (97 páginas)

Las 97 páginas se inspeccionaron completas. Un contrato: CCB de Capital Consig,
páginas 66–74, con cuadros de calificación/liberación, cuadro financiero, cláusulas
1–24, cierre de aceptación y certificado de firma integrado. Se conserva el 94%
superior de cada página para excluir el pie judicial. Los reglamentos de tarjeta
y cuenta citados en sus cláusulas no están adjuntos. El extracto de consignaciones,
TED y registro técnico Unico separado no añaden otro instrumento ni cláusulas.
El vídeo externo de la audiencia no está en el PDF y no se declara revisado.

Contrato 7c449306-b01b-5f73-8d6b-fa72ab77ddc3: nueve páginas revisadas antes y después
a 240 DPI. 45 máscaras eliminan datos del titular, firma, respuesta personal PEP,
identificadores y QR; se corrigió un pequeño resto de firma detectado al ampliar.
Permanecen total 8.352,00, liberado 1.981,77, 96 cuotas de 87,00, fechas,
tasas 4,00% mensual/60,10% anual, CET 4,19%/63,64% y todos sus componentes,
además de las autorizaciones y condiciones de la operación. Comprobación Poppler
de portada, cuadro económico y certificado. OCR residual en nueve páginas sin
coincidencias para 15 identificadores; PDF sin texto oculto, enlaces, anotaciones,
adjuntos ni metadatos personales. SHA-256 publicado:
9085fb792c35856e2712a8cb904c29e46bb666ae34a5857783965772384c9efb.
Fuente y temporales descartados después de verificar la publicación.

### Fuente 0522564a-19ef-4fdc-a3cb-cd1dfec4df29 (98 páginas)

Las 98 páginas se inspeccionaron completas. Dos ocurrencias fragmentarias: dos
párrafos entrecomillados del Reglamento de utilización Credcesta en la respuesta
de ouvidoria (página 17), y un recuadro de autorización de margen/descuento en
nómina y formalización del título de una operación de saque (página 41).
Se conservan por separado: el reglamento general de tarjeta y la autorización de
la operación de saque no acreditan ser el mismo instrumento. No constan sus
documentos completos ni identificadores que permitan reconstruirlos. Las consultas
retrospectivas de préstamos, facturas y nóminas no se cuentan como instrumentos.
La sentencia paradigma menciona contratos y audios de otro proceso sin reproducirlos.

Reglamento 49bbb046-507e-5246-adb8-3915654c1381: tres recortes conservan ambos
párrafos completos, separando la primera línea del segundo para excluir el conector
editorial «Ainda». Autorización e372b797-2154-5ea6-83f8-b343af366170: un recorte
con el recuadro íntegro. Las cuatro salidas se inspeccionaron antes y después a
240 DPI, sin datos personales ni máscaras, con igualdad exacta de píxeles. Ambos
PDF se reabrieron sin texto oculto, enlaces, anotaciones, adjuntos ni metadatos
personales. SHA-256 publicados, respectivamente:
f6d97dc5c35adaf6824b21902487f25ceeccc6749003d9c0cb9e75477e1e1e9a y
f253b8b8a74cda7081f3e3ff66c38c659c5f086e4eef51b621c354d4fbdc08a5.
Se indexaron 96 páginas negativas y se verificó el manifiesto tras la publicación
y el descarte de la fuente y sus temporales.


### Fuente 628232c6-3fd9-4317-93f4-a1019236b2f0 (98 páginas)

Se revisaron 70 páginas completas y 28 mediante cuerpo exactamente idéntico a una
referencia negativa independiente más inspección visual del pie actual: 22–47 y 71–72.
Son estatutos, publicaciones corporativas y poderes, sin instrumentos de crédito.
La demanda, contestación y recurso discuten negativación y cargos de cheque especial,
pero no reproducen contrato ni cláusulas. Las cajas resaltadas de la contestación
son extractos de un dictamen doctrinal. La nota de la página 59 enlaza un reglamento
externo que no está incorporado al PDF; no se cuenta como contrato contenido.
La sentencia de las páginas 77–79 señala expresamente la falta de contrato, adhesión,
facturas o demostrativos aportados. Se excluyen consultas de mora, boleto comercial
no obligatorio, identidades, poderes, Cadastro Único y cálculos/depósito judicial.
El acta 74–75 remite a un video externo no incorporado; no se declara revisado.
Resultado: cero contratos. Se indexaron las 70 páginas negativas independientes
antes del descarte. Revisión aprobada y publicación/descarte verificados, con fuente
y derivados eliminados. SHA-256 de origen:
90411142c670a6a06cc6c3bc5bf1d44192e24de63d293fbe855fad2d725d6915.


### Fuente 34acf460-0797-427c-ade4-ce677ed1e5fd (98 páginas)

Las 98 páginas se revisaron completas. Una ocurrencia fragmentaria de condiciones
de tarjeta Santander: en la página 46 aparecen la cláusula 16 de Comunicación y
las cláusulas 3, 3.1, 3.2, 3.3 y el comienzo de 3.3.1 sobre emisión y límites.
Dos recortes excluyen el membrete, los argumentos y el pie judicial. El segundo
fragmento termina en «mediante a sua» en la propia imagen; no se inventa su continuación.
No está el contrato completo en los anexos. Se excluyen las capturas de límites,
las facturas mensuales y sus ofertas no aceptadas, así como poderes y actas societarias.
El vídeo externo citado en la audiencia no está incorporado y no se declara revisado.

Contrato 234751e4-76b4-5edf-9a32-a32305c5b0ca: dos salidas inspeccionadas antes y
después a 240 DPI, sin datos sensibles ni máscaras y con igualdad exacta de píxeles.
PDF reabierto sin texto oculto, enlaces, anotaciones, adjuntos ni metadatos personales.
SHA-256 publicado: 25a5140e88eec31dce18813a73e5bcd554c5f955856120de550b6d1c72542620.
Se indexaron 97 páginas negativas independientes antes del descarte; publicación
y manifiesto verificados, fuente y temporales eliminados.

### Fuente 45083eb7-23c9-4df7-a2ee-1c828349a9b7 (99 páginas)

Se revisaron 64 páginas completas y 35 mediante cuerpo exactamente idéntico a una
referencia negativa independiente más inspección visual del pie actual. Cero
contratos de crédito: el documento trata del rechazo de compras con tarjeta Will;
las capturas son límites, movimientos, rechazos y soporte. La infografía de alta
remite a condiciones sin reproducirlas. Los anexos son poderes, certificados de
firma, estatutos y actos de liquidación. La «Procuração/Contrato» de la página 11
regula representación y honorarios jurídicos, no crédito. Las cajas de la defensa
son precedentes judiciales. Los vídeos mencionados en las páginas 5, 34, 70 y 74
no están incorporados al PDF y no se declaran inspeccionados.
Se indexaron las 64 páginas negativas independientes antes del descarte. Revisión
aprobada, manifiesto verificado, fuente y temporales eliminados. SHA-256 de origen:
7cfae27327ee13cfa62b7c39524385b85e107dc098cd2bb7a93d85d2024bd044.


### Fuente dc190e60-4cc4-4562-94b0-4177c939d980 (99 páginas)

Las 99 páginas se revisaron completas. Una ocurrencia fragmentaria de condiciones
de tarjeta Riachuelo/Midway, reproducida en la contestación: cláusulas 7.3 y 7.3.1
en la página 84, y una variante del párrafo sobre contraseñas en la página 89 que
dice CREDENCIADO en lugar de ADICIONAL. Se conservan ambas reproducciones sin
inferir un segundo contrato. El documento completo enlazado no está incorporado.
Las facturas, opciones de parcelación condicionadas a pago exacto sin aceptación,
registros de transacciones y posición de cuenta no son instrumentos adicionales.
El anexo societario contiene únicamente estatutos, actas y poderes.

Contrato 7228e845-1c67-5e91-b78c-118cec2be9f9: dos recortes inspeccionados antes y
después a 240 DPI. La primera revisión detectó un píxel azul de membrete en el
borde superior del segundo recorte; se rectificó de 74 a 75 puntos mediante
revisión explícita auditada, antes de limpiar, y se volvieron a inspeccionar los
originales. Salidas de 1092 × 722 y 1362 × 231 píxeles, sin datos sensibles ni
máscaras, con igualdad exacta de píxeles. PDF reabierto sin texto oculto, enlaces,
anotaciones, adjuntos ni metadatos personales. SHA-256 publicado:
28b46f800486d87e931642089157c4763a75dd2a8b550ff9994f8cf0df595dca.
Se indexaron 97 páginas negativas independientes antes del descarte; publicación
y manifiesto verificados, fuente y temporales eliminados. SHA-256 de origen:
b24532ba03e9e6b40839fafa904967dc81557994fd9ed1f9b7b301762ce8b52d.


### Fuente f68cc7a6-c949-4420-8e32-ee1563953285 (100 páginas)

Sin contratos de crédito incorporados. Revisión de 82 páginas completas y 18 por
identidad exacta del cuerpo con referencias independientes previamente revisadas,
más inspección visual de todos sus pies: páginas 22–37 (poderes, DocuSign y estatutos
Will) y 95–96 (Diario Oficial y acto del Banco Central). Las capturas son compras
rechazadas, historial de uso, estado activo y guía general de alta con referencia
al contrato externo; no reproducen el instrumento. El acuerdo de las páginas
68–69 regula una indemnización de R$1.275 por el litigio y no concede crédito.
Las actuaciones posteriores se refieren a su pago, a la sentencia de daños y a
la controversia de ejecución. El vídeo de audiencia enlazado en página 56 no
está incorporado al PDF y no se afirma revisado.

Se registraron las 100 decisiones y 82 nuevas referencias negativas antes de
verificar el manifiesto vacío y eliminar fuente y temporales. SHA-256 de origen:
d86425f4c9552a12bec2654b53de2994824bb1f7903f48a8d2434942735999c0.


### Fuente 7b0f519c-b7ae-4605-a836-a645e00f0c69 (100 páginas)

Cero contratos de crédito incorporados. Se revisaron 95 páginas completas y cinco
mediante identidad exacta del cuerpo con referencia negativa independiente, más
inspección visual de sus pies actuales: 79–83 (sustitución y representantes). Las
ofertas de parcelación en facturas no acreditan aceptación y exigen pago exacto
para contratar. El informe de tarjeta de 2026 contiene datos retrospectivos,
historial de plásticos y saldo cero; no es instrumento de adhesión. La defensa
parafrasea facultades de cancelación, sin reproducir cláusulas ni contrato. Los
anexos de representación contienen poderes, estatutos y actas, incluidos los
reglamentos internos del comité Customer Experience. El acta de la página 100
enlaza dos vídeos externos no incorporados al PDF y no declarados revisados.

Se registraron 100 decisiones y 95 referencias negativas independientes antes de
verificar el manifiesto vacío y eliminar fuente y temporales. SHA-256 de origen:
f6c820dd32fa3f6f4a9823c4186a84a9def5718ed056c0900f77279ee8dcda3b.


### Fuente 728e5f00-413f-46b9-9819-20754615c6c7 (100 páginas)

Cero instrumentos de crédito incorporados. Se revisaron 74 páginas completas y
26 mediante identidad exacta del cuerpo con referencia negativa independiente,
más inspección visual de todos sus pies: páginas 26–51, actas, estatutos y
autenticaciones societarias de Neon. Las capturas de la defensa muestran
identificación, firmas y selfies, historiales de facturas y un artículo de ayuda
sobre desbloqueo. No reproducen adhesión ni condiciones del contrato. Las
menciones a contratos en sentencias y precedentes tampoco incorporan el
instrumento. El boleto de cosméticos es una propuesta condicionada al pago,
sin aceptación acreditada. La grabación de audiencia enlazada en página 76 y
el archivo CTPS .pdf.p7s anunciado en página 88 no están incorporados al PDF
y no se consideran revisados.

Se registraron 100 decisiones y 74 nuevas referencias negativas antes de
verificar el manifiesto vacío y eliminar fuente y temporales. SHA-256 de origen:
fb73b0e358bc4b62ab32c5b8e5f93a4925c2d8ee3ea51691b6cc09ff0c84730b.


### Fuente 7eb94e61-37e1-4e3b-8afb-c5fd47ac8daa (102 páginas)

Dos operaciones de crédito, cuatro páginas de salida. Se inspeccionaron las
102 páginas completas y los anuncios societarios girados a mayor resolución.
El correo de la página 17 y SMS de la 21 conservan condiciones de una misma
renegociación: 10 cuotas de R$ 211,68, primera con vencimiento 11/06 y canales
de pago. La primera cuota pagada y la defensa reconocen el acuerdo; por eso
se distingue de ofertas no aceptadas de facturas. Se clasificó expresamente
como fragmentos contractuales, sin afirmar que sea un instrumento completo.
La adhesión Elo del 19/04/2024 está bajo el título Cadastro en la página 79;
la 80 aporta el término firmado de entrega/aceptación. Se conserva la referencia
pública 1.425.136, responsabilidad por tarjetas adicionales y las dos elecciones
marcadas. La página 79 requirió rotación de 90 grados.

Se excluyeron recibos, boletos reemitidos, cobros, estados retrospectivos de la
renegociación, facturas, documentos de identidad, selfies y una adhesión de
cuenta digital/prepaga sin crédito de 2022. Actas empresariales que autorizan
futuras CCB o debentures no incorporan esos instrumentos. El vídeo de audiencia
referenciado en página 86 y CTPS .pdf.p7s de la 100 no forman parte del PDF
y no se declararon revisados.

Ambos contratos tenían datos sensibles: dos máscaras para correo personal y
identificador de boleto; veintitrés para nombres, documentos, nacimiento, sexo,
contactos, dirección, cuenta, final de tarjeta y firmas de la adhesión. La revisión
visual del primer borrador encontró colas de letras, corregidas antes de aprobar.
Las cuatro páginas finales se inspeccionaron completas a 240 dpi y con Poppler,
verificando conservación de fechas, importes, cuotas, cláusulas y casillas.
Se registraron 98 referencias negativas independientes antes de publicar, verificar
los hashes y purgar fuente y temporales. SHA-256 de origen:
f322becccb714dadbd9348f9e541dc41987c96e885f9b7019f5da72b9b087596.

## Expediente Neon de 103 páginas: sin instrumento de crédito

Se revisaron 68 páginas completas y 35 cuerpos idénticos píxel a píxel a páginas
negativas independientes, inspeccionando visualmente los 35 pies actuales. Las
alegaciones de adhesión, la guía pública de desbloqueo, las capturas de perfil con
firma/selfie/documentos y los historiales retrospectivos de facturas no reproducen
un contrato. La sentencia señala ausencia del instrumento; esa mención se contrastó
con todos los anexos, incluida la apelación de Neon. Se excluyeron documentos
procesales, societarios, facturas y tasas judiciales. El vídeo externo de audiencia
de la página 80 no está incorporado y no se declaró revisado.

Las 103 decisiones negativas se registraron antes de aprobar la extracción vacía.
Se añadieron 68 referencias visuales independientes, se verificó la publicación sin
contratos y se purgaron fuente y temporales. Documento: 2aa698e1-1543-4bab-b180-87ef28da53a8.
SHA-256: 5378981eaaaff60f0e851c33ebea721ee7bec2e5ba2fc5d0969611378a16f75f.

## Expediente Bradesco de 104 páginas: adhesión, condiciones y copia parcial

Documento b7fc0c1b-c5be-40b5-9e85-ca9ad782d83e. Se inspeccionaron 100 páginas
completas y cuatro mediante cuerpo idéntico a referencias negativas independientes,
con examen visual de los cuatro pies actuales. Se conservó la copia parcial del
formulario reproducida en la defensa, página 47, y el instrumento de las páginas
55–94: adhesión de cuatro páginas, sumario de nueve y reglamento de veintisiete.
Las dos ocurrencias permanecen diferenciadas. La adhesión de 2020 y reglamento
de 2023 se preservan tal como están incorporados, sin afirmar su aplicabilidad
temporal. La frase SCR truncada de la página 58 ya está incompleta en el original.

Se excluyeron demanda, poderes, documentos civiles, facturas y consultas de deuda.
Las menciones a renegociaciones en facturas no aportan otro instrumento. El vídeo
externo de audiencia anunciado en página 104 no está incorporado ni se declara
revisado. El boleto de cosméticos es una oferta condicionada al pago sin aceptación.

Ambas ocurrencias contienen datos sensibles: trece máscaras en el fragmento y
veintidós en la primera página de la adhesión completa. Se eliminaron identificador,
nombres, documentos, datos civiles, domicilio y contactos. Se conservaron fecha
11/06/2020, producto Neo Visa Internacional, límite R$ 1.300, vencimiento día 15,
condiciones de anualidad y casillas de elección. Las 41 páginas originales y limpias
se inspeccionaron a 240 dpi; las dos modificadas también con Poppler. Las otras
39 páginas conservan exactamente sus píxeles. Se indexaron 59 referencias negativas,
se verificaron ambas publicaciones y se purgaron fuente y derivados sensibles.
SHA-256 de origen: f2b1b17c28e3bc44c87f7ac4337298cc4bbc94d0e1d6b5b15d85cfe70c68e7be.

## Expediente Travessia XIII de 104 páginas sin instrumento de crédito

Documento 91ebfc15-0cca-4199-bab2-38026ff2a324. Las 104 páginas se revisaron
visualmente completas, incluidas las copias de los actos societarios. Contiene
demanda, consultas de deuda, documentos civiles, poderes, actos constitutivos,
estatutos, elecciones de directores y certificados de firma. Los poderes autorizan
cobros, quitas y futuras negociaciones; los estatutos permiten futuras operaciones
crediticias. Ninguno incorpora el instrumento de un crédito. Los boletines de
suscripción y depósitos de R$500 corresponden a capital social, no a préstamos.

Se registraron las 104 decisiones negativas y se aprobó la extracción vacía.
Se indexaron 104 referencias visuales independientes, se verificó la liberación
y se purgaron fuente y temporales sensibles.
SHA-256: cac172ca0bd1f6c92aa9142c1d7148e9e3532f37174cba469e30e9ffb5a9addd.

## Nubank de 104 páginas: cláusulas de límite dentro de la defensa

Documento a89d330d-1893-46f3-bb26-ae0b0a330ff3. Se revisaron 78 páginas completas
y 26 cuerpos idénticos a referencias visuales independientes de la fuente b0571aff;
los 26 pies actuales se inspeccionaron. Facturas, chats, avisos de reducción de
límite y extractos retrospectivos de Pix financiados no contienen el instrumento
de contratación. Las ofertas de fraccionamiento de diciembre, enero y febrero
no se aceptaron: las facturas posteriores registran sus pagos integrales.

La página 80 reproduce las cláusulas 4.7.4 y 4.7.6 sobre reducción y seguimiento
del límite. Se preservó exclusivamente esa captura como fragmento de condiciones;
el salto de numeración ya está en el original. Contrato
3f2568bd-94ec-5bcc-99ef-c95be1e3d3bb, una página sin datos sensibles ni máscaras.
Se revisaron original y salida a 240 dpi y el PDF con Poppler; los píxeles se
conservaron íntegramente. El vídeo externo de la audiencia de página 98 no está
incorporado y no se declara revisado. Se indexaron 77 referencias negativas,
se verificó la publicación y se purgaron fuente y temporales.
SHA-256 de origen: 02b287fc01795891937eaa3c0eef1bcd6b627ea510e50afaece70c1a288799a5.
SHA-256 de salida: 37a551c57476214a6fbc947d4f6e35cb4b8250df1f36657df479428c59957e8a.

### Fuente 06fb9651-c70e-4cc0-b12d-782657f2bfd5 (104 páginas)

Se revisaron 72 páginas completas y 32 mediante cuerpo idéntico a referencias
negativas independientes más inspección visual de cada pie actual (55–86,
poderes y actos corporativos de Itaú). No contiene un contrato de crédito.
Las facturas Hipercard de mayo, junio y julio y los comprobantes de pago son
pruebas de cobro. Las ofertas de cuotas no tienen aceptación acreditada: mayo
se pagó íntegramente en dos operaciones y el posterior pago de R$ 4.000 no
coincide con las opciones impresas en las facturas de junio o julio.
El anexo titulado «Boleto do acordo» (36) es un boleto avulso con instrucciones
genéricas de pago total/parcial, sin condiciones de renegociación. Su comprobante
de página 38 tampoco las incorpora. El extracto de página 32 solo registra
débitos históricos de otros préstamos. Las consultas de transacciones de
98 y 101–102 documentan procesamiento de pagos, sin instrumento crediticio.
La decisión final menciona el supuesto acuerdo y requiere prueba contractual,
pero no reproduce cláusulas. Se aprobaron las 104 decisiones negativas,
se indexaron 72 nuevas referencias y se verificó la purga de fuente y temporales.
SHA-256 de origen: a88528ffe6937b7a4c87aefba51f99e3862cf7d7bc126ca74ce01129c10684c2.

## Bradesco de 105 páginas: contrato solicitado pero no incorporado

Documento 54d18321-8266-4018-b371-d03eec4325b2. Se revisaron 79 páginas
completas y 26 mediante cuerpos idénticos a referencias negativas independientes;
se inspeccionó cada pie actual. La demanda solicita contrato, adhesión y prueba
de entrega del cartão. La documentación posterior contiene actos societarios,
poderes, IRPF, Cadastro Único y recursos sobre competencia territorial, sin
instrumento de crédito ni cláusulas reproducidas. Los anuncios de préstamos
Creditas en resultados de búsqueda son publicidad genérica. Las anotaciones
Serasa solo identifican deudas, sin aportar sus contratos.

Se registraron 105 decisiones negativas, se aprobó la extracción vacía y se
indexaron 79 referencias independientes. Se verificó la liberación y se purgaron
fuente y temporales sensibles. SHA-256 de origen:
498d96794cd75263f5501c3cec124b25b0cda2a05245e9e6e7c67cabbd2d06ea.

## Magazine Luiza de 105 páginas: comprobantes y plan de pago retrospectivo

Documento 3ad9bc84-9c45-4ad5-8d1c-a46323a19784. Se revisaron 69 páginas
completas y 36 mediante cuerpos idénticos a referencias negativas independientes
más inspección de cada pie actual. La compra de electrodomésticos pagada con
una tarjeta existente no incorpora un instrumento autónomo de financiación.
El anexo «Plano de pagamento» (91–92) es una consulta histórica Sandiego con
productos, garantía estendida y registro de pago Visa en seis cuotas sin interés.
Las capturas de 3, 28–34 y 98–99 documentan la misma compra y su seguimiento.
Las facturas Credicard contienen ofertas opcionales: febrero y marzo se pagaron
íntegramente, y no se aportó aceptación de las opciones de abril. Se excluyeron
las ofertas no aceptadas, notas fiscales, actos societarios, poderes y escritos
procesales. Los cuatro vídeos no convertidos al PDF (36–39) y la grabación
externa de audiencia (105) no están incorporados y no se declaran revisados.

Se registraron 105 decisiones negativas, se aprobó la extracción vacía y se
indexaron 69 nuevas referencias independientes. Se verificó la liberación y se
purgaron fuente y temporales. SHA-256 de origen:
0896957db88b4daf96b2675777a6ba3efdcc51945c0af9d55f4b604302e88128.

## Nu de 107 páginas: cadastro ilustrativo y DDC retrospectivo

Documento 105bde5a-4eed-4543-86f8-a6ebfdd443bd. Se revisaron 95 páginas
completas y 12 mediante cuerpos idénticos a referencias negativas independientes,
con inspección de cada pie actual. Las publicaciones contables de letra pequeña
se inspeccionaron también a 300 dpi. Sus menciones a créditos y CCB describen
carteras agregadas, sin reproducir instrumentos. La contestación y el anexo de
alta contienen pantallas de ejemplo, enlaces a contratos, identificación y una
factura parcial, pero no cláusulas ni una adhesión individual. El DDC (97–98)
es un informe retrospectivo a 08/06/2026 de una operación anterior, con saldos,
cuotas vencidas y cargos acumulados. Los avisos de cobro carecen de términos
específicos de renegociación. La grabación externa de audiencia enlazada en 102
no está incorporada al PDF y no se declara revisada.

Se registraron 107 decisiones negativas, se aprobó la extracción vacía y se
indexaron 95 nuevas referencias independientes. Se verificó la liberación y se
purgaron fuente y temporales. SHA-256 de origen:
bffcc66ac30d0bb72cd248859944430da5eee275f37e24073fbaae40f87fc6d4.


### Fuente b34b812c: Neon, 107 páginas, sin instrumento de crédito

Se revisaron las 107 páginas: 103 completas y cuatro cuerpos idénticos a páginas negativas previamente inspeccionadas, con inspección actual de sus cuatro pies. Las p.29 y 35 muestran una consulta interna histórica de préstamo (parcelas pagadas, saldo actualizado y botón de generar documento), no el instrumento original. Las capturas de p.25 son términos introductorios de cuenta de pago; p.27 solo muestra cabecera de informe de apertura y selfie. Las FAQ de p.30–32 y 39 describen procedimientos y consecuencias genéricas; no reproducen condiciones pactadas. La referencia al contrato digital en sentencia p.85 y contrarrazones tampoco aporta el documento. Los anexos societarios, poderes y certificados de firma se excluyeron. El vídeo externo de audiencia enlazado en p.83 no está incorporado y no se revisó. Extracción negativa aprobada, 103 referencias visuales independientes indexadas y fuente descartada mediante publicación verificada.


### Fuente 5325231a: Santander, 107 páginas, facturas y ejecución provisional

Se inspeccionaron 106 páginas completas y una página con cuerpo vacío exactamente idéntico a una referencia negativa independiente; su pie actual se revisó manualmente. No se encontró instrumento de crédito. Las ofertas de parcelamento de agosto y octubre exigen pago exacto para contratar y las facturas posteriores acreditan pago íntegro; la oferta de noviembre no tiene aceptación documentada. El anuncio SuperCrédito es publicidad con contratación futura. Los avisos de cambios de contrato remiten al sitio web sin reproducir cláusulas. Las consultas internas de estorno, aunque incluyen la etiqueta Contrato Cartão, muestran movimientos históricos, no el instrumento. El resto son escritos, resoluciones, identificación, procurações, certificaciones y resultados SISBAJUD/RENAJUD. Extracción vacía aprobada; 106 referencias visuales independientes indexadas, liberación verificada y fuente/temporales purgados. SHA-256 de origen: 7a410aa2b2a1b31fc105932ce326b7af1862d9a078aedc3f783ada7faa989407.

### Fuente 28426bd5: condiciones consignadas Bradesco dentro de respuesta PROCON

Se revisaron las 107 páginas: 79 completas y 28 mediante identidad exacta del cuerpo con referencias negativas independientes, inspeccionando cada pie actual. La respuesta al PROCON reproduce en 13–14 el Sumário (sección 3, autorización al INSS y requisitos de emisión) y el Regulamento (capítulo 1, definición 12 de margen consignable). Se conservaron exclusivamente esas regiones, sin la explicación narrativa de la respuesta, membretes ni autenticación judicial. Los históricos INSS y la planilla de descuentos son registros retrospectivos; la contestación sólo relata una contratación, sin adjuntar la adhesión ni reproducir otras cláusulas. La grabación externa enlazada en 100 no está incorporada al PDF y no se declara revisada.

Contrato fragmentario 9f62d2b9-4ada-5ee8-bbca-d5f55c4344e0, dos páginas, sin datos sensibles ni máscaras. Originales y salida inspeccionados a 240 dpi, sin variación de píxeles, y PDF final comprobado también con Poppler. SHA-256 publicado: 6bac38fa196edccc8776913cfe3156d995430a4d9b8bbd1210c1b68fbf2d25d8. Se indexaron 77 referencias negativas independientes y se verificó la publicación antes de purgar fuente y temporales. SHA-256 de origen: 6577a4a0c9b7a7bb0b070e232afab14e5388260d81da2ec617f38531bc0d5c2c.

### Fuente d00d546c: cláusula de CCB Credcesta transcrita en defensa

Se revisaron las 108 páginas: 106 completas y dos por identidad exacta del cuerpo con referencias negativas independientes, inspeccionando sus pies actuales. Se encontró únicamente el ítem 21 de una CCB Banco Master, transcrito en p.51 sobre cancelación, plazo de siete días útiles y devolución de capital y tributos. Se extrajo exclusivamente ese bloque. El contrato de alquiler de p.11–12 no es crédito; las nóminas, cálculos judiciales, poderes y certificados electrónicos tampoco. La sentencia señala la falta de instrumento firmado y los escritos no lo aportan. El vídeo Lifesize enlazado en p.38 no está incorporado al PDF ni se declara revisado.

Fragmento d7da181b-aadc-5f7d-bae7-b3820146a0ff, una página sin datos personales ni máscaras. Original y salida inspeccionados a 240 dpi y comprobación adicional Poppler a 140 dpi; píxeles idénticos. SHA-256 publicado: 9119422575b506734a38063bcc9566f28ba3069686d3bf5681b646ee4e881581. Se indexaron 105 referencias negativas independientes y se verificó publicación antes de purgar fuente y temporales. SHA-256 de origen: f80094ea7fc4cd04cb8af040baa091933e53c60b6c5186136a3358a0a8e4ad8e.

### Fuente 0f1bd3bc: Mercado Pago, consumos y ejecución sin contrato

Se inspeccionaron completas sus 108 páginas. Las capturas de compra, facturas y estados de cuenta acreditan una operación Amazon disputada y su posterior reembolso; no incorporan una adhesión ni condiciones de crédito. El crédito provisional durante la disputa no constituye un préstamo. La defensa solo menciona términos y condiciones, sin reproducirlos. Los actos societarios titulados contrato social y sus certificados DocuSign son ajenos al crédito. Las referencias a cláusulas abusivas pertenecen a precedentes judiciales, no a un instrumento de este expediente. La audiencia no alcanzó acuerdo; el vídeo externo enlazado en p.80 no está incorporado al PDF y no se declara revisado. Extracción vacía aprobada, 108 referencias negativas independientes indexadas y liberación verificada antes de purgar la fuente y temporales. SHA-256 de origen: 3ce57463817463cc117354c455d53dd7367a7b1ece5d52cc1361933ee0cae072.
### Fuente cf20c01b: CCB consignada Neon y dos reproducciones parciales

Se inspeccionaron completas las 108 páginas. Se conservaron tres ocurrencias: el cuadro de identificación de la CCB reproducido en p.22, la cláusula 6 de condiciones de crédito transcrita en p.27 y la CCB completa de quince páginas en 82–96. El archivo titulado ccb.pdf de p.99 es en realidad un extracto retrospectivo; las consultas de préstamos de p.22–23 y 97–98, apertura de cuenta, historial mercantil y límites tampoco constituyen instrumentos adicionales. Las condiciones genéricas de alta de cuenta de p.21 no son crédito. La audiencia de p.103 no alcanzó acuerdo; su enlace externo no está incorporado al PDF ni se declara revisado.

Se inspeccionaron las 17 páginas extraídas originales y limpias a 240 dpi. Se aplicaron 12 máscaras al cuadro parcial y 32 a la CCB completa para retirar identificación, empleador particular, cuenta receptora y datos de firmas digitales; las cláusulas y valores permanecen íntegros. El fragmento de cláusula 6 no contiene datos sensibles y conserva píxeles idénticos. Se comprobó también el PDF guardado con Poppler. Se publicaron los contratos 6410532c-ad24-528d-9e52-d7b606d26378, 123073b6-3fa0-5d49-994c-849944e258f3 y 41542184-8bde-5e75-9d85-3e501f1f3911, se indexaron 91 referencias negativas independientes y se verificó la liberación antes de purgar fuente y temporales. SHA-256 de origen: 5202417541f63cd62ae62a2d92c1a0288fb388e66f91be32b218dc02e5c7d7a6.


### Fuente 94f1971e: Will, facturación discutida sin instrumento

Se revisaron las 109 páginas: 86 completas y 23 por identidad exacta de su cuerpo con referencias negativas independientes, inspeccionando visualmente cada pie actual. Las facturas de marzo y mayo incluyen ofertas de pago en cuotas sujetas a contratación futura, sin aceptación documentada. Las capturas de alta y ayuda muestran instrucciones genéricas, no cláusulas del contrato. La réplica niega haber contratado parcelamiento y aporta historial de reembolso de compra con tarjeta preexistente; estas pantallas no constituyen un préstamo autónomo. La respuesta al PROCON contiene explicaciones de la liquidación, FGC e instrucciones de pago. El acta de audiencia solamente presenta el título y asistentes; su cuerpo está vacío. Se excluyen poderes, certificados, actos del Banco Central y escritos procesales.

Extracción vacía aprobada y liberación verificada antes del descarte de fuente y temporales. Se indexaron 84 referencias negativas independientes. SHA-256 de origen: ad1f4187d29403b354dc7ac2821d209c88c2ba6689550c3dfbcef13055c69daf.


### Fuente c50f6a56: autorización de descuento Credcesta reproducida en defensa

Se revisaron las 109 páginas: 54 completas y 55 por identidad exacta del cuerpo con referencias negativas independientes, inspeccionando cada pie actual. La página 76 reproduce dos líneas de autorización de reserva de margen consignable, descuento en remuneración/beneficio y formalización de título de crédito. Se preservó exclusivamente ese recuadro. Las nóminas documentan descuentos y no instrumentos; el TCE citado en la demanda es un modelo normativo del INSS con campos genéricos. Las resoluciones ajenas aportadas como precedentes y la sentencia de este caso no reproducen contratos adicionales. La grabación externa enlazada en p.96 no está incorporada ni se declara revisada.

Fragmento 16737449-5cb2-5d80-bd4f-399451bc5c96, una página sin datos sensibles ni máscaras. Original y salida inspeccionados a 240 dpi, con identidad de píxeles; PDF guardado comprobado también con Poppler. SHA-256 publicado: 72f3221f80fb798bc2fd408cb8d0075d5a966b246b2fa6184cc92c66dc3d9992. Se indexaron 53 referencias negativas independientes antes de verificar publicación y purgar fuente y temporales. SHA-256 de origen: 3ad2c57cad4f5340dc152b96446a3682ef91e13b62d07b164727b6e697b20a88.


### Fuente 3f8f5358: Bradesco, adhesión mencionada y no incorporada

Se revisaron las 109 páginas: 84 completas y 25 por identidad exacta del cuerpo con referencias negativas independientes, inspeccionando cada pie actual. La defensa afirma la existencia de una adhesión firmada y un reglamento, pero no reproduce ni aporta esos instrumentos. Las imágenes muestran ejemplos de cancelación de tarjeta y diagramas de funcionamiento. Se excluyen consultas retrospectivas SCPC, identificación, poderes, escritos y documentación societaria. Se ampliaron a 300 dpi los periódicos societarios: la autorización de garantías de Bresco para una futura emisión de CRI no contiene el instrumento de financiación; los balances de otra sociedad tampoco son un contrato. La audiencia de p.80 terminó sin acuerdo; el vídeo externo enlazado no está incorporado al PDF y no se declara revisado.

Extracción vacía aprobada, 84 referencias negativas independientes indexadas antes del descarte y liberación verificada. SHA-256 de origen: 4ff465fc545a37695152339b02acbbc82b384f98578eb7232fa5d7f20d62c6c5.
