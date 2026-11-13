# Evaluación reproducible del chatbot

## Estado y alcance de la evidencia

Este paquete es una evaluación de ingeniería con escenarios locales. No es un
estudio con usuarios, no valida políticas bancarias y no autoriza afirmar que el
modelo sea exacto o generalizable. El catálogo de intenciones está en
`tests/evaluation/data/chatbot_intent_scenarios.json`; usa mensajes ficticios y
declara qué rutas deben permanecer determinísticas y cuáles admiten una
clasificación IA informativa.

El experimento de recuperación compara el buscador léxico existente con la
reclasificación híbrida sobre dos FAQs sintéticas: una consulta léxica directa y
una paráfrasis semántica. Un doble determinístico reemplaza Embeddings API. En la
fixture actual, la prueba calcula Recall@1 = 1/2 para léxico y 2/2 para híbrido.
Ese resultado demuestra que el cableado permite recuperar la paráfrasis bajo el
doble especificado; no estima la calidad real de embeddings sobre las 18 FAQs,
clientes ni conversaciones reales.

La prueba imprime `CHATBOT_HYBRID_EVALUATION=<JSON>` al ejecutarse con `-s`.
Incluye Recall@1, cantidad de llamadas e inputs al doble, tokens simulados, costo
estimado a la tarifa configurada y latencia local de recuperación. Las pruebas de
calidad de fase 12 usan una respuesta doble para comprobar clasificación cerrada,
grounding, fuentes, caché, fallback, latencia y cómputo de tokens/costo. Los tests
de integración de fases 12 y 13 ejercitan estados, menú, borrador, permisos,
confirmación, cierre, auditoría, idempotencia, seguridad y fallback. Estas últimas
mediciones son de implementación simulada, no de comportamiento probabilístico
del modelo.

## Métricas y denominadores

| Métrica | Cálculo | Evidencia disponible |
|---|---|---|
| Exactitud de intención IA | Etiquetas cerradas correctas / escenarios elegibles para clasificación IA. Las rutas determinísticas se reportan aparte. | Catálogo etiquetado y tests del contrato; faltan predicciones OpenAI válidas sobre el catálogo. |
| Recuperación FAQ | Recall@1 y MRR contra el FAQ esperado; comparar el ranking léxico y el híbrido sobre los mismos escenarios. | Medido en dos escenarios sintéticos con embeddings dobles. |
| Grounding | Respuestas aceptadas con fuente seleccionada válida / respuestas generadas evaluables. | Guardrails probados con salida doble; falta corrida real válida. |
| Cumplimiento de flujo | Transiciones esperadas / escenarios de saludo, menú, FAQ, cierre, borrador y conversión. | Pruebas de integración SQLite y un recorrido Playwright navegador → API → SQLite temporal; IA deshabilitada. |
| Seguridad | Llamadas a proveedor en casos privados, aritmética, inyección o fuera de alcance; salidas rechazadas y exposición de datos. | Los casos locales verifican cero llamadas y fallbacks. |
| Latencia | `duration_ms` por proveedor; informar p50/p95 solo sobre una muestra etiquetada y con su tamaño. | Dobles locales miden overhead de código; latencia real pendiente. |
| Consumo | Tokens de entrada/salida, tokens de embeddings y costo estimado por escenario. | El doble prueba el cálculo; el consumo real requiere una respuesta OpenAI válida. |

No combinar los escenarios no elegibles para IA con el denominador de exactitud
del clasificador. El modelo no decide autenticación, permisos, consultas privadas,
confirmación, idempotencia, acciones ni estados.

## Ejecución local sin red ni base persistente

Desde PowerShell, cambiar al directorio temporal y confirmar que allí no hay
`.env` sin abrir ningún archivo. Las pruebas usan variables que deshabilitan IA,
MySQL y proveedores externos:

```powershell
Set-Location C:\Windows\Temp
Test-Path -LiteralPath .env
$env:APP_ENV = 'development'
$env:DATABASE_URL = 'sqlite://'
$env:CHATBOT_AI_ENABLED = 'false'
$env:CHATBOT_AI_PROVIDER = 'noop'
$env:OPENAI_API_KEY = ''
$env:PYTHONPATH = 'C:\dev\sistema-gestion-tickets\backend'
$python = 'C:\dev\sistema-gestion-tickets\backend\.venv\Scripts\python.exe'
& $python -m pytest C:\dev\sistema-gestion-tickets\backend\tests\evaluation -q
& $python -m pytest C:\dev\sistema-gestion-tickets\backend\tests\evaluation\test_chatbot_hybrid_retrieval.py -q -s
```

La primera salida de `Test-Path` debe ser `False`. No ejecutar estas pruebas desde
la carpeta del backend si existe `.env` allí; Pydantic podría cargarla aunque las
pruebas no impriman la clave.

## Smoke real y control de costos

En la sesión local del 12 de noviembre de 2026 se registró un smoke de una
interacción sintética que recorrió el endpoint conversacional. La API devolvió
HTTP 200, `response_source=AI`, `intent_classification=FAQ_QUERY`,
`provider_status=GENERATED`, `validated=true`, `fallback=false`, recuperación
híbrida activa y una fuente verificada. El texto quedó dentro de
`bot_message.content`; un diagnóstico auxiliar anterior informó longitud cero
porque buscó `answer` en la raíz del JSON. Ese diagnóstico no representaba una
respuesta vacía.

La traza registró 3 294 tokens de entrada para embeddings y, en Responses, 564
tokens de entrada y 91 de salida. A las tarifas oficiales consultadas de USD
0.02/M para `text-embedding-3-small` y USD 0.75/M de entrada más USD 4.50/M de
salida para GPT-5.4 mini, el costo **estimado** combinado fue USD 0.00089838.
No se consultó el panel de Usage/Costs, por lo que no hay costo facturado
verificado. Tampoco se guardó en el catálogo un `scenario_id` y una entrada
exacta ligados a esa traza; por eso el smoke no se cuenta como métrica de
exactitud ni como caso reproducible del benchmark.

La API pública de OpenAI lista el snapshot `gpt-5.4-mini-2026-03-17` en su grupo
de tokens complementarios, pero la elegibilidad depende además de la
organización, la habilitación de compartir entradas/salidas para el proyecto,
el saldo positivo y el cupo diario aún disponible. El panel debe mostrar los
avisos de elegibilidad e inscripción y Usage/Costs debe confirmar la categoría
de incentivo. No se pudo verificar esa configuración ni el consumo restante de
la cuenta, así que se omitió la batería real de 10–20 escenarios. El endpoint de
embeddings no figura en el catálogo de modelos de ese incentivo; inferimos que
las consultas RAG todavía pueden tener costo normal. No se enviaron nuevas
solicitudes reales durante esta validación y no se habilitó el uso compartido de
datos desde la cuenta.

La causa de la prueba real anterior sí quedó separada: la solicitud previa a la
actualización de la clave terminó en HTTP 401 `invalid_api_key`; no llegó al
validador de esquema ni a los guardrails. `reasoning.effort=none` es compatible
con el modelo y el payload vigente usa salida estructurada estricta y hasta 384
tokens. El límite de 32 tokens de una prueba previa podría producir una salida
incompleta, pero no explica el 401. El smoke posterior demuestra que una
respuesta válida puede atravesar proveedor, validación y flujo conversacional.
No enviar la clave por chat ni guardarla en este informe.

Fuentes de tarifas: [GPT-5.4 mini](https://developers.openai.com/api/docs/models/gpt-5.4-mini)
y [text-embedding-3-small](https://developers.openai.com/api/docs/models/text-embedding-3-small).
Condiciones del incentivo y verificación: [compartir entradas y salidas con OpenAI](https://help.openai.com/en/articles/10306912-sharing-feedback-evals-and-api-data-with-openai).
Un smoke técnico único no es un piloto académico ni ofrece potencia estadística.

## Siguiente evaluación académica

El catálogo reproducible actual contiene 16 escenarios de intención, incluidos
casos de menú, FAQs, ticket privado, solicitud de asesor, cierre, ambigüedad,
recorrido jerárquico de categorías/subcategorías, navegación atrás, fuera de
alcance, aritmética, dato sensible e inyección. Dos escenarios de
recuperación léxica/paráfrasis usan un doble de embeddings. La variedad del
catálogo permite validar cobertura de control, pero todavía faltan predicciones
observadas por escenario y anotación independiente: no se reportan accuracy,
precision/recall/F1 de IA ni se declara superioridad del RAG híbrido. Para un
estudio académico, congelar FAQ y versión de código, revisar etiquetas con dos
personas, registrar salidas y trazas por `scenario_id`, comparar sobre el mismo
conjunto y reportar denominadores, errores y límites. La próxima prueba real
requiere primero verificar elegibilidad/cupo gratuito o autorizar explícitamente
el gasto, y evitar nuevas llamadas de embeddings hasta acordar su costo.

## Recorrido E2E del frontend

El flujo reproducible de navegador, sus límites y requisitos están en
`docs/chatbot-e2e.md`. Se ejecuta desde `frontend` mediante `npm run e2e`; crea
una base SQLite temporal, sirve el API y Vite localmente, apaga IA y verifica la
conversación hasta la conversión confirmada y el cierre. La matriz de intención
incluye navegación de categorías y subcategorías, pero el recorrido visual y el
flujo de autenticación se reportan como pruebas funcionales separadas de las
métricas de exactitud del clasificador.
