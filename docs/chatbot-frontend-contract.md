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
| `POST /api/v1/chat/conversations/{conversation_id}/messages` | Opcional | `{"content": "..."}`; texto no vacío, hasta 10 000 caracteres | `200 BotMessageResponse` |
| `POST /api/v1/chat/conversations/{conversation_id}/reset` | Opcional | Sin body | `200 ConversationRead`; `409` si ya fue convertida o cerrada |
| `POST /api/v1/chat/conversations/{conversation_id}/escalate` | Opcional | `ConversationActionRequest` (`reason` opcional) | `200 ConversationRead` |
| `POST /api/v1/chat/conversations/{conversation_id}/feedback` | Opcional | `ChatFeedbackCreate` (`is_helpful` requerido) | `201 ChatFeedbackRead` |
| `POST /api/v1/chat/conversations/{conversation_id}/link-user` | Bearer JWT de `CLIENTE` | Sin body | `200 LinkConversationResponse` |
| `POST /api/v1/chat/conversations/{conversation_id}/convert-to-ticket` | Bearer JWT de `CLIENTE` propietario | `TicketCreate` | `201 TicketRead`; `200` en reintento idéntico; `409` si cambia el payload tras convertir |

`TicketCreate` requiere `category_id`, `subject` (3–200 caracteres) y
`description` (5–10 000 caracteres). `priority` es opcional y, si se omite,
usa `MEDIA`. Obtén los IDs disponibles desde `GET /api/v1/categories`, que lista
las categorías activas. En el flujo de borrador, el frontend solicita
confirmación explícita cuando `ticket_draft.status=READY_FOR_CONFIRMATION` y solo
entonces envía la conversión. El envío del body al endpoint constituye la
confirmación; `CONVERT_TO_TICKET` por sí sola nunca crea el ticket.

`SHOW_MENU` y `END_CONVERSATION` son acciones de interfaz devueltas por el
endpoint de mensajes; no tienen endpoints propios. `CONTACT_ADVISOR` puede
ejecutarse mediante `/escalate` solo cuando el cliente lo solicita o confirma.
Una conversación `CLOSED` no se reinicia: `/reset` responde `409` y el frontend
debe crear otra con `POST /api/v1/chat/conversations`.

## Reglas de consumo

| Caso | `bot_message.intent` | `suggested_action` | Resultado esperado |
|---|---|---|---|
| Saludo | `null` | `NONE` | Mostrar la respuesta y mantener la conversación activa. |
| Descubrir preguntas | `FAQ_DISCOVERY` | `ASK_CLARIFICATION` | Renderizar `clarification_options`; no abrir el menú global. |
| Volver o mostrar el menú | `CONVERSATION_MENU` | `SHOW_MENU` | Conservar mensajes y usar `menu_open=true`. |
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

Los consumidores deben tratar los enums como extensibles. Los valores por
defecto de `menu_open` y `paused` son `false`, de modo que un consumidor anterior
puede ignorarlos.

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
