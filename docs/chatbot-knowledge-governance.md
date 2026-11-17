# Gobierno del conocimiento y calidad del chatbot

## Alcance y límites

Este chatbot pertenece a un prototipo académico de gestión de tickets. No es un
canal oficial de Banco GNB Perú, no autentica clientes bancarios y no consulta
saldos, cuentas, tarjetas, movimientos ni operaciones. El asistente orienta desde
preguntas frecuentes aprobadas y ofrece los flujos del prototipo cuando no puede
resolver una consulta.

El catálogo inicial contiene 18 preguntas demo sobre tarjetas, cuentas, banca
digital, créditos, reclamos/requerimientos y canales de atención. Es una base
curada inicial, no una cobertura completa de productos, tarifarios ni políticas
del Banco. No se deben presentar montos, tasas, elegibilidad, aprobaciones o
plazos como universales cuando dependan de evaluación o cambien con el tiempo.

## Contrato conversacional

- Los saludos simples obtienen una respuesta cordial determinística; no se
  clasifican como baja confianza ni ofrecen un ticket por sí solos. La respuesta
  aclara que se trata de un prototipo académico y no de un canal bancario oficial.
- El menú principal expone IDs estables para acciones reales. La navegación FAQ
  presenta solo categorías con preguntas publicadas y activas que tienen una
  fuente oficial verificada o una respuesta exacta incluida en la política
  explícita de contenido seguro; la selección ofrece las FAQ elegibles.
- `FAQ_DISCOVERY` es una intención distinta de `SHOW_MENU`. Las categorías FAQ y
  las preguntas usan `ui` tipado con IDs; el servidor solo acepta IDs ofrecidos
  en el estado vigente y los vuelve a validar antes de procesarlos.
- Las respuestas sobre productos y procesos solo usan FAQs publicadas, activas y
  vigentes con URL HTTPS de `bancognb.com.pe` o `www.bancognb.com.pe` y fecha de
  verificación. La fuente usada se devuelve en `sources[]`.
- Las coincidencias medias o ambiguas piden aclaración sin abrir un ticket ni
  escalar automáticamente. La conversación conserva hasta dos mensajes breves
  de aclaración y, ante una referencia clara, usa el último mensaje del cliente
  y la respuesta anterior del asistente para recuperar el tema.
- La búsqueda de FAQs descarta tokens numéricos y no resuelve una consulta con
  una sola palabra compartida. Una expresión aritmética tampoco se envía al
  proveedor aunque aparezca junto a una consulta bancaria.
- El proveedor no genera respuestas bancarias sin contexto oficial suficiente
  ni con coincidencias ambiguas. Los turnos conversacionales sin contexto son
  posibles, pero la política y validación solo permiten reconocer el mensaje o
  pedir aclaración sin afirmar datos bancarios. Cada oración de una respuesta
  bancaria generada debe tener soporte léxico en las fuentes seleccionadas y
  cada número debe aparecer en ese contexto; estas comprobaciones reducen
  invenciones, pero no equivalen a una verificación semántica formal.
- La salida OpenAI incluye una clasificación de intención de catálogo cerrado.
  El backend valida el valor y rechaza intenciones que intenten autorizar
  consultas privadas, cierre, creación o cambios de estado. No se solicita ni
  se interpreta una probabilidad de confianza del modelo.
- La recuperación híbrida conserva el buscador léxico, sinónimos y fuzzy como
  baseline y reordena el catálogo público con embeddings de
  `text-embedding-3-small` (256 dimensiones). Usa únicamente FAQs activas,
  publicadas, vigentes y con fuente oficial verificada; excluye contenido con
  patrones de inyección. La puntuación combinada no reduce una coincidencia
  léxica previa; si no hay coincidencia léxica, la semántica debe superar el
  umbral de grounding. La relevancia semántica no concede permisos ni ejecuta
  operaciones.
- Los vectores se calculan bajo demanda para un máximo configurable de 200 FAQs
  y se cachean solo en memoria por proceso, versión y contenido. No se agrega una
  tabla, migración ni base vectorial. El texto de consulta se minimiza antes de
  enviarse. Si embeddings falla o excede el presupuesto, el flujo conserva el
  resultado léxico y aplica los guardrails RAG de siempre.
- Las listas de opciones guardan IDs de FAQ activos. El usuario puede seleccionar
  el título o decir “la primera”, “opción 2”, etc. Solo se resuelve contra las
  opciones pendientes de esa conversación; no se interpreta una opción fuera de
  contexto.
- Una pregunta nueva se evalúa por sí misma salvo que contenga una referencia
  clara al intercambio anterior. No se concatena todo el historial. La selección
  de una opción pendiente requiere su título o un ordinal; una frase libre no se
  fuerza a coincidir con una FAQ.
- Un saludo, una coincidencia fallida o una baja confianza no cambia por sí solo
  la conversación a `ESCALATED`. La escalación requiere acción explícita. Cuando
  se agotan las aclaraciones se ofrece reformular, pedir atención humana o crear
  ticket, sin crear ninguno automáticamente.
- “Eso no me ayudó” conserva la FAQ y pide una aclaración breve. Una solicitud
  explícita de asesor usa la solicitud de atención humana existente; no abre un
  chat en vivo ni asegura disponibilidad inmediata. Una solicitud explícita de
  ticket inicia un borrador; solo el endpoint autenticado y el payload completo
  confirman su creación.
- `BotMessageResponse.suggested_action` y `conversation_status` son campos
  tipados. `requires_clarification`, `clarification_options`, `sources` y los
  estados existentes siguen disponibles para clientes anteriores.
- Las opciones interactivas estructuradas se sirven por `ui.options`; cuando
  existen, `clarification_options` queda vacío para evitar duplicados. El LLM
  redacta la respuesta conversacional, mientras los IDs proceden del backend.
- La conversión se confirma con el endpoint existente
  `POST /api/v1/chat/conversations/{conversation_id}/convert-to-ticket`: requiere
  autenticación de cliente, pertenencia de la conversación y el cuerpo completo
  `TicketCreate`. Una respuesta útil o de alta confianza no impide la conversión.
  La llamada no crea duplicados y conserva el límite diario y la auditoría.
- `POST /api/v1/chat/conversations` crea una conversación nueva. El endpoint
  `/reset` reinicia el estado operativo sin borrar mensajes; el frontend puede
  iniciar una conversación nueva y cambiar su ID activo.
- Si falla la recuperación, se devuelve un fallback controlado sin escalar ni
  sugerir la creación de un ticket. Si falla el proveedor de IA, se conserva la
  respuesta determinista; el proveedor no puede crear tickets ni decidir permisos.
- El contenido de usuario y las FAQs se tratan como datos no confiables. Las
  defensas existentes de inyección, solicitud sensible, grounding y validación
  de salida siguen siendo obligatorias si se usa un proveedor de IA configurado.
  OpenAI sigue desactivado por defecto y solo se activa mediante configuración
  local explícita; ninguna prueba automatizada llama al servicio externo.
- Cuando OpenAI devuelve contadores de uso, el backend guarda input, output y
  total de tokens en la traza técnica del mensaje y en metadatos de auditoría;
  no guarda el contenido del prompt del proveedor dentro de esas métricas.
- Para `gpt-5.4-mini`, la aplicación estima y reserva costo antes de cada llamada
  con un límite configurable `CHATBOT_AI_DAILY_BUDGET_USD` (0.50 USD por defecto).
  El presupuesto vive en memoria por proceso y día UTC; tras reinicio o con varias
  réplicas se requiere además un límite en la cuenta del proveedor para controlar
  el gasto agregado. El costo estimado de embeddings comparte el mismo límite.
- El adaptador usa Responses API sin estado (`store=false`) y salida JSON Schema
  estricta. Para `gpt-5.4-mini` fija `reasoning.effort=none`; el límite de salida
  predeterminado es 384 tokens. Un estado `incomplete` identifica de forma segura
  si se agotó `max_output_tokens`; no se reintenta automáticamente. HTTP errors,
  timeouts, red y salida inválida mantienen fallback controlado sin cuerpo del
  proveedor ni secretos en la respuesta.
- Las llamadas reales al proveedor tienen límites por minuto, por conversación y
  por actor en una ventana móvil de una hora (`CHATBOT_AI_MAX_PROVIDER_CALLS_PER_ACTOR_PER_HOUR`,
  20 por defecto). Para usuarios autenticados se agrupan todas sus conversaciones;
  para uso anónimo se agrupan por la clave HMAC derivada de la IP de conexión. Los
  rechazos por esos límites usan la respuesta determinística y no llaman al proveedor.
  Las respuestas de caché no consumen cuota de proveedor.

## Fuente y vigencia de cada FAQ

`faqs` y `faq_versions` guardan `source_url` y `source_verified_at`. La
validación mantiene una lista explícita de hosts HTTPS oficiales y rechaza
credenciales, puertos, HTTP y dominios imitadores. Cuando una respuesta
generada usa una FAQ con fuente verificada, el contrato actual devuelve
`sources[]` con título, categoría, URL y fecha de verificación.

El catálogo demo tiene 18 respuestas revisadas. Solo la FAQ sobre la diferencia
general entre reclamo y requerimiento usa fuente externa; apunta al texto
consolidado del Reglamento de Gestión de Reclamos y Requerimientos de la SBS:

<https://intranet2.sbs.gob.pe/dv_int_cn/2230/v6.0/Adjuntos/4036-2022.R%20.pdf>

El catálogo registra la verificación de esa fuente el 17 de noviembre de 2026.
En el contrato público existente, el registro de esa FAQ expone `source_url` y
`source_verified_at`; una respuesta generada y validada puede incluir la fuente
SBS en el arreglo `sources[]`. La forma de estos campos no cambia y no requiere
cambios de interfaz.

Las demás respuestas demo sin fuente solo están disponibles si coinciden
exactamente con una respuesta aprobada en `faq_content_policy.py`. Esa lista no
autoriza respuestas arbitrarias ni modifica el fallback para preguntas no
reconocidas. Los alias de búsqueda de acceso digital, contraseñas y teléfono
viven en el servicio del chatbot, no como títulos ni sinónimos públicos del FAQ.
La antigua pregunta de afiliación se conserva archivada y deja de mostrarse en
el catálogo público.

Las FAQs creadas o editadas por el flujo administrativo deben citar una fuente
oficial que respalde la respuesta y registrar su fecha de verificación en UTC.
Los cambios administrativos publicados pasan a revisión; la migración de
catálogo crea una nueva versión de cada FAQ afectada y conserva las versiones
históricas previas. La fecha guardada es una señal de procedencia, no una garantía
de vigencia futura; el responsable debe volver a verificar la fuente antes de
mantener la respuesta publicada.

## Feedback y medición

El endpoint `POST /api/v1/chat/conversations/{conversation_id}/feedback` registra
una calificación para un mensaje del bot, tanto si se basó en una FAQ como si fue
un saludo, aclaración o respuesta general. `message_id` es opcional para clientes
anteriores; si se omite, se evalúa la respuesta más reciente del bot. En
integraciones nuevas se recomienda enviar el ID exacto de la respuesta mostrada.

El voto por mensaje es único: una nueva calificación del mismo mensaje actualiza
la existente, no infla el total. Las respuestas basadas en FAQ también mantienen
la fila de `faq_feedback` existente para que los reportes de utilidad anteriores
sigan funcionando. La tabla `chatbot_feedback` registra la evaluación genérica,
la conversación, mensaje, FAQ relacionada (si existe), usuario autenticado (si
existe) y fechas; no registra el texto del chat en el agregado de métricas.

`GET /api/v1/chat/admin/feedback/metrics` está reservado a Supervisores y entrega
totales, votos útiles/no útiles, porcentaje útil y separación entre respuestas
con FAQ y generales. Son indicadores de revisión, no una medida de exactitud
clínica, financiera o de satisfacción representativa. Un voto nunca modifica el
contenido, umbrales ni modelo y nunca se usa como entrenamiento automático.

## Carga segura del catálogo

En un ambiente ya migrado y con categorías y Supervisor activos, el comando
`python -m app.seed.chatbot_knowledge` inserta/actualiza únicamente las FAQs del
catálogo demo y sus versiones. No crea usuarios, tickets, conversaciones, SLA ni
comentarios. Es idempotente respecto a contenido y evita incrementar la versión
si los valores ya coinciden.

`python -m app.seed.demo_data` tiene un alcance más amplio y puede escribir datos
operativos demo. No lo uses para refrescar solo el chatbot en una base local que
contenga trabajo que quieras conservar.

La migración `0019_chatbot_quality_sources` agrega procedencia y feedback de forma
aditiva. Antes de migrar un entorno compartido o de producción se requiere
backup, revisión de plan de despliegue y validación de la aplicación; esta
documentación no autoriza un despliegue.

## Pruebas y evolución

La suite debe cubrir saludos, saludo más consulta, descubrimiento, selección por
título y ordinal, aclaraciones sucesivas, cambio de tema, fuentes, FAQs inactivas,
fallback sin escalación, feedback de respuestas FAQ/no FAQ, idempotencia,
autorización Supervisor y seed sin mutar tickets. Los cambios de conocimiento
deben probar también URL maliciosa, fecha sin zona horaria y conservación de
versiones.

La incorporación de más preguntas debe priorizar cobertura real observada y
fuentes oficiales verificables. No se debe aumentar artificialmente la
confianza, responder por similitud de palabras comunes ni degradar a un flujo de
ticket para compensar una FAQ insuficiente.

## Evaluación reproducible

`tests/evaluation/test_chatbot_hybrid_retrieval.py` compara las mismas dos
consultas de FAQ contra el baseline léxico y el recuperador híbrido, con vectores
determinísticos y sin red. En esa fixture sintética, Recall@1 del baseline es
1/2 y Recall@1 híbrido es 2/2; no es una estimación de calidad sobre clientes ni
sobre el catálogo productivo. La prueba además calcula el número de
llamadas y entradas de embedding (incluida la reutilización del caché) y tiempo
de recuperación.

`tests/evaluation/test_chatbot_phase12_quality.py` verifica respuesta con fuente,
clasificación cerrada, grounding, repetición en caché, fallback de inyección,
latencia y cálculo de tokens/costo con un proveedor doble. Las pruebas de fases
12 y 13 cubren navegación, cierre, borrador, autenticación, confirmación,
idempotencia, consultas privadas y auditoría. Ejecutar `pytest tests/evaluation`
desde un directorio sin `.env`, con SQLite y proveedor deshabilitado; estas
pruebas no llaman OpenAI. El resultado de mocks es evidencia de implementación,
no evidencia de exactitud del modelo.

Para un estudio académico, versionar un conjunto de consultas etiquetado por
personas revisoras, separar ajuste de evaluación, fijar baseline y versión del
catálogo, y calcular al menos: exactitud de intención, Recall@1/MRR de FAQ,
proporción de respuestas aceptadas con fuente válida, cumplimiento de estados y
acciones, violaciones de seguridad, latencia p50/p95, tokens de entrada/salida y
costo estimado por escenario. Una prueba real única de integración no constituye
un piloto ni permite inferencia estadística; esos resultados y su costo deben
registrarse aparte de los fixtures sintéticos.
