# Contrato del chatbot para el frontend

Este documento describe los estados que el frontend debe consumir del backend.
Los campos son aditivos y las rutas existentes bajo `/api/v1/chat` no cambian.

## Operaciones HTTP existentes

Todos los paths incluyen el prefijo `/api/v1`. La autenticación es opcional al
crear, leer y enviar mensajes en una conversación; una conversación vinculada
solo puede ser leída o modificada por el cliente propietario. `conversation_id`
se obtiene de la respuesta de creación y se usa en las operaciones siguientes.

| Método y path | Autenticación | Entrada | Respuesta de éxito |
|---|---|---|---|
| `POST /api/v1/chat/conversations` | Opcional | Sin body | `201 ConversationRead` |
| `GET /api/v1/chat/conversations/{conversation_id}` | Opcional | Path `conversation_id` | `200 ConversationRead` |
| `POST /api/v1/chat/conversations/{conversation_id}/messages` | Opcional | Exactamente uno: `{"content": "..."}` o `{"option_id": "..."}` | `200 BotMessageResponse` |
| `POST /api/v1/chat/conversations/{conversation_id}/reset` | Opcional | Sin body | `200 ConversationRead`; `409` si ya fue convertida o cerrada |
| `POST /api/v1/chat/conversations/{conversation_id}/escalate` | Opcional | `ConversationActionRequest` (`reason` opcional) | `200 ConversationRead` |
| `POST /api/v1/chat/conversations/{conversation_id}/feedback` | Opcional | `ChatFeedbackCreate` (`is_helpful` requerido) | `201 ChatFeedbackRead` |
| `POST /api/v1/chat/conversations/{conversation_id}/link-user` | Bearer JWT de `CLIENTE` | Sin body | `200 LinkConversationResponse` |
| `POST /api/v1/chat/conversations/{conversation_id}/convert-to-ticket` | Bearer JWT de `CLIENTE` propietario | `TicketCreate` | `201 TicketRead`; `200` en reintento idéntico; `409` si cambia el payload tras convertir |
| `POST /api/v1/tickets/{ticket_id}/confirm-resolution` | Bearer JWT de `CLIENTE` propietario | Sin body | `200 TicketRead`; confirma el cierre de un ticket resuelto propio |
| `GET /api/v1/chat/admin/metrics` | Bearer JWT de `SUPERVISOR` | Sin body | `200 ChatbotOperationalMetrics`; solo agregados |

`TicketCreate` requiere `category_id`, `subject` (3–200 caracteres) y
`description` (5–10 000 caracteres). `priority` es opcional y, si se omite,
usa `MEDIA`. Obtén los IDs disponibles desde `GET /api/v1/categories`, que lista
las categorías activas. En el flujo conversacional, el frontend presenta el
resumen cuando `ticket_draft.status=READY_FOR_CONFIRMATION`. La respuesta del
cliente `Confirmo` cambia el borrador a `CONFIRMED`; solo entonces se envía el
body al endpoint de conversión. `CONVERT_TO_TICKET` por sí sola nunca crea el
ticket. La primera conversión crea un ticket `NUEVO` con origen `CHATBOT` y
requiere la confirmación explícita; un reintento idéntico devuelve el ticket
original.

`SHOW_MENU` y `END_CONVERSATION` son acciones de interfaz devueltas por el
endpoint de mensajes; no tienen endpoints propios. `CONTACT_ADVISOR` puede
ejecutarse mediante `/escalate` solo cuando el cliente lo solicita o confirma.
Una conversación `CLOSED` no se reinicia: `/reset` responde `409` y el frontend
debe crear otra con `POST /api/v1/chat/conversations`.

`content` acepta texto no vacío. El límite efectivo se configura con
`CHATBOT_MAX_MESSAGE_LENGTH` (2000 caracteres por defecto y máximo configurable
de 10 000). `option_id` solo acepta un ID que el backend ofreció en el estado
actual. Enviar ambos campos, ninguno o un ID mal formado devuelve `422`; un ID
válido en formato pero no ofrecido o ya vencido devuelve `409` sin agregar
mensajes ni ejecutar acciones.

## Reglas de consumo

| Caso | `bot_message.intent` | `suggested_action` | Resultado esperado |
|---|---|---|---|
| Saludo | `null` | `NONE` | Mostrar la respuesta y mantener la conversación activa. |
| Descubrir preguntas por texto | `FAQ_DISCOVERY` | `ASK_CLARIFICATION` | Renderizar `ui.type=FAQ_CHOICES` y `clarification_options`; no abrir el menú global. |
| Navegar FAQ desde el menú | `FAQ_CATEGORY_DISCOVERY` | `ASK_CLARIFICATION` | Renderizar `ui.type=FAQ_CATEGORY_CHOICES`; cada categoría proviene de FAQs publicadas con fuente verificada. |
| Volver o mostrar el menú | `CONVERSATION_MENU` | `SHOW_MENU` | Conservar mensajes y usar `menu_open=true` y `ui.type=MAIN_MENU`. |
| Finalizar | `CONVERSATION_ENDED` | `END_CONVERSATION` | Exigir `conversation_status=CLOSED` y deshabilitar nuevos envíos. |
| Borrador pausado | `CONVERSATION_MENU` | `SHOW_MENU` | Conservar campos y mostrar una opción para retomarlo cuando `ticket_draft.paused=true`. |
| FAQ resuelta | intent de la FAQ o `null` | `NONE` | Mostrar la respuesta y sus fuentes reales. |
| Aclaración | intent actual o `null` | `ASK_CLARIFICATION` | Mostrar opciones si existen o permitir texto libre. |
| Consulta privada anónima | intent privado | `AUTHENTICATION_REQUIRED` | Solicitar autenticación sin mostrar datos. |
| Conversión disponible | intent de borrador | `CONVERT_TO_TICKET` | Mostrar el resumen; crear solo mediante el endpoint de conversión. |

## Campos nuevos compatibles

- `BotMessageResponse.menu_open: bool`: `true` solo cuando la respuesta abrió
  el menú. Una consulta posterior lo devuelve a `false`.
- `ConversationRead.menu_open: bool`: permite restaurar el estado después de
  recargar la página.
- `ChatbotTicketDraft.paused: bool`: indica que el borrador se conserva pero no
  controla el turno actual. No es una confirmación de creación.
- `ChatbotSuggestedAction.SHOW_MENU`: muestra opciones sin borrar historial ni
  cerrar la conversación.
- `ChatbotSuggestedAction.END_CONVERSATION`: acompaña una finalización explícita.
- `BotMessageResponse.quick_replies`: acciones de texto sugeridas para el turno;
  el cliente puede enviarlas como contenido normal y el backend las vuelve a
  clasificar.
- `ConversationRead.proactive_notices` y `BotMessageResponse.proactive_notices`:
  avisos de tickets propios en estado `PENDIENTE_CLIENTE` o `RESUELTO`. El primer
  caso enlaza a los tickets propios; el segundo permite confirmar el cierre con
  `POST /api/v1/tickets/{ticket_id}/confirm-resolution`.
- `BotMessageResponse.ticket_draft`: conserva campos, estado, errores y pausa.
  Los estados `READY_FOR_CONFIRMATION`, `CONFIRMED` y `CANCELLED` son explícitos;
  el modelo no autoriza una conversión.
- `response_source`, `fallback_reason` y `sources`: permiten mostrar si la
  respuesta vino de IA o fallback y qué fuentes públicas se usaron.
- `BotMessageResponse.ui` y `ConversationRead.ui`: describen las opciones
  seleccionables del estado actual y permiten restaurarlas tras una recarga.
- `intent_classification`: enum cerrado adicional para respuestas generadas por
  IA. Es informativo; nunca autoriza consultas ni acciones.

Los consumidores deben tratar los enums como extensibles. Los valores por
defecto de `menu_open` y `paused` son `false`, de modo que un consumidor anterior
puede ignorarlos.

## Estado de integración de interfaz

La integración local del frontend consume `ConversationRead.ui` al restaurar
una conversación y los estados `ui` devueltos por cada turno. Renderiza
`MAIN_MENU`, `FAQ_CATEGORY_CHOICES` y `FAQ_CHOICES` con los botones que entrega
el backend, y envía exactamente el `option.id` seleccionado como `option_id`.
No deriva acciones desde etiquetas; el backend vuelve a validar cada ID.
Mostrar el menú conserva el transcript y una opción de borrador pausado solo se
puede seleccionar si el backend la incluyó en el estado actual.

Cuando el backend devuelve `CLOSED`, la interfaz oculta las opciones, bloquea
el compositor y permite crear una conversación nueva sin intentar reabrir la
cerrada. La conversión en ticket sigue usando el formulario autenticado del
portal de cliente, que pide confirmación antes de llamar al endpoint existente.

El backend ofrece categorías FAQ planas. `tags` y `synonyms` mejoran búsqueda,
pero no forman una jerarquía de subcategorías navegable. No existe un campo ni
una ruta de subcategoría en el contrato actual. Las pruebas del frontend validan
API y componentes con respuestas simuladas; no equivalen a una ejecución E2E
contra un navegador y backend levantados juntos.

Cuando el cliente pide un asesor, el borrador incluye un resumen determinístico
de la conversación. La interfaz debe indicar que no existe transferencia en vivo
y pedir confirmación antes de crear el ticket. La conversión lo deja sin asignar
en la cola operativa. Un asesor puede ver el ticket en la cola sin asignar, pero
el detalle queda restringido hasta que un supervisor lo asigne o el asesor lo
tome; esto evita exponer el contenido completo antes de reclamar la atención.
La ruta de cola es `GET /api/v1/tickets/operations?queue=unassigned`, para roles
internos autorizados.

## Navegación y opciones seleccionables

El menú principal solo presenta operaciones que existen en el prototipo: FAQ
publicadas, estado o lista de tickets propios, inicio de borrador de solicitud,
solicitud de atención humana y cierre. La opción FAQ abre categorías que tienen
contenido activo, publicado y con fuente oficial verificada. La selección de una
categoría devuelve `FAQ_DISCOVERY` con preguntas y fuentes de esa categoría.
Si no hay FAQs elegibles, la opción FAQ se omite.

Cada opción incluye `id`, `label` y `action`. Algunos IDs estables son
`menu:faq`, `menu:ticket_status`, `menu:tickets`, `menu:create_ticket`,
`menu:advisor`, `menu:end` y, cuando existe un borrador pausado,
`menu:resume_ticket_draft`. Las preguntas y categorías usan `faq:<uuid>` y
`category:<uuid>`. El frontend debe devolver solo el `id` entregado por el
backend; no debe construir acciones ni inferirlas desde la etiqueta. El backend
vuelve a comprobar estado, pertenencia a la conversación, vigencia de FAQ,
autenticación y permisos. Seleccionar `menu:create_ticket` solo inicia o reanuda
el borrador: no crea el ticket ni confirma su conversión.

Una conversación conserva `ui` en `GET /conversations/{id}` para restaurar el
menú, las categorías FAQ, las preguntas pendientes y la opción de reanudar un
borrador pausado. Al completar la selección, cambiar de tema o cerrar el chat,
los IDs pendientes dejan de ser válidos.

## Estado de conversación

El fin de una respuesta no implica cierre. Solo `conversation_status=CLOSED`
confirma la finalización persistida. Una conversación cerrada no se reactiva: el
frontend debe usar el endpoint existente de creación para iniciar otra.

`SHOW_MENU` mantiene `conversation_status=ACTIVE`. `FAQ_DISCOVERY` es un valor
de `intent`, no una acción estructurada ni un cierre.

## Fuentes y respuestas IA

Una respuesta sobre productos o procesos puede incluir `sources` con `title`,
`category`, `url` y `verified_at`. El frontend no debe etiquetar una fuente como
oficial por su cuenta. Las respuestas sociales o de aclaración pueden tener
`response_source=AI` y `sources=[]`; no deben contener afirmaciones bancarias.

Los fallos, timeouts, HTTP 429 o respuestas inválidas del proveedor conservan
una respuesta segura del backend. El frontend debe guiarse por
`suggested_action`, `available_actions`, `fallback_reason` y
`conversation_status`, sin interpretar mensajes de texto mediante frases
exactas.

Con `CHATBOT_AI_ENABLED=true`, una FAQ elegida explícitamente por el usuario,
publicada y con fuente oficial verificada pasa a generación IA. Para coincidencias
automáticas, el puntaje debe ser al menos `CHATBOT_AI_MIN_CONFIDENCE` (0,70 por
defecto), y el contexto no puede tener resultados demasiado cercanos entre sí.
Debajo de ese corte, el backend mantiene la aclaración o respuesta determinística;
el proveedor no recibe una FAQ de baja confianza. `CHATBOT_AI_ENABLED=false`, una
falla del proveedor o una salida rechazada por validación conservan el fallback
determinístico. Las consultas privadas de tickets, creación de tickets, saldo y
otras operaciones bancarias permanecen en ramas de backend sin proveedor.

El retest real del 2026-11-15 confirmó esta condición: 1 de 60 consultas
legítimas alcanzó IA con fuente verificada; 59 quedaron bajo el corte de contexto
0,70 y no llegaron al proveedor. Este resultado no cumple el objetivo de 85%;
los puntajes y motivos por caso están en
[`chatbot-evaluation.md`](chatbot-evaluation.md). Una consulta por un canal
público no se convierte en seguimiento privado solo por mencionar una
“solicitud”; la solicitud debe estar identificada como propia para consultar su
estado.

La traza `ai_trace_json` es interna y no forma parte del contrato HTTP. Registra
`routing_reason` cuando una intención determinística evita IA; cuando se evalúa
RAG, registra `candidate_match_count`, `candidate_top_score`,
`minimum_context_score`, `candidates_below_context_threshold`,
`context_source_count`, `fallback_reason` y `provider_called`. El registro de
evaluación copia esta traza para permitir auditar por qué una consulta alcanzó o no
el proveedor sin enviar datos privados.

Las trazas IA internas pueden incluir tokens y `estimated_cost_usd`. Para
`gpt-5.4-mini`, la aplicación reserva una estimación conservadora antes de llamar
al proveedor y aplica `CHATBOT_AI_DAILY_BUDGET_USD` (0.50 USD por defecto) por
proceso y día UTC. La reserva se conserva ante errores para evitar repetir
llamadas costosas. Es un límite local por proceso; no reemplaza el límite de
gasto de la cuenta del proveedor ni coordina varias réplicas.
