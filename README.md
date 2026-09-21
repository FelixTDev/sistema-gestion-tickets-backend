# Backend de gestión de tickets

Prototipo académico independiente para gestión de tickets. No se conecta a sistemas del Banco GNB Perú ni procesa operaciones bancarias reales.

## Desarrollo local

1. Copiar `.env.example` a `.env` y ajustar solo valores locales.
2. Crear un entorno virtual e instalar dependencias: `pip install -e ".[dev]"`.
3. Iniciar MySQL: `docker compose up -d db`.
4. Aplicar migraciones: `alembic upgrade head`.
5. Cargar datos locales: `python -m app.seed.demo_data`.
6. Iniciar API: `fastapi dev app/main.py`.

Swagger queda disponible en `http://localhost:8000/docs` y el health check en `/api/v1/health`.

### CORS para el frontend

Los orígenes permitidos se configuran mediante `CORS_ORIGINS` como una lista JSON.
El valor local incluido en `.env.example` permite el frontend Vite en
`http://localhost:5173` y `http://127.0.0.1:5173`:

```env
CORS_ORIGINS=["http://localhost:5173","http://127.0.0.1:5173"]
```

Para varios orígenes utiliza, por ejemplo,
`["http://localhost:5173","https://frontend.example"]`. La API permite los métodos
`GET`, `POST`, `PATCH`, `DELETE` y `OPTIONS`, y los encabezados `Content-Type` y
`Authorization`. CORS no habilita credenciales basadas en cookies; la autenticación
continúa usando tokens Bearer en el encabezado `Authorization`.

## Autenticación

El registro crea únicamente usuarios con rol `CLIENTE`. El login devuelve un access token JWT para enviarlo como `Authorization: Bearer <access_token>` en `/api/v1/auth/me` y en futuras rutas protegidas. Cada token nuevo se vincula a una sesión revocable; el logout invalida la sesión actual y el cambio o recuperación de contraseña invalida las sesiones anteriores. Los tokens expiran según `ACCESS_TOKEN_EXPIRE_MINUTES`.

La recuperación de contraseña responde siempre con el mismo mensaje, exista o no la cuenta, para evitar enumeración de correos. Los tokens de recuperación y verificación se almacenan únicamente como hashes, tienen expiración y solo pueden utilizarse una vez. El registro prepara la verificación de correo sin bloquear el login existente. En desarrollo se utiliza un adaptador de correo `NoopEmailProvider`: no envía mensajes, no guarda tokens y no los escribe en logs; un proveedor real debe inyectarse fuera de este prototipo.

Los JWT emitidos por versiones anteriores pueden no incluir `jti` y se aceptan por compatibilidad. El logout no puede revocar individualmente esos tokens legacy: permanecen válidos hasta su expiración, salvo que un cambio o recuperación de contraseña active la invalidación global del usuario. Los tokens nuevos sí quedan vinculados a una sesión persistente y son revocables individualmente.

Endpoints de identidad y seguridad:

- `POST /api/v1/auth/forgot-password`: solicitar recuperación sin revelar si el correo existe.
- `POST /api/v1/auth/reset-password`: establecer una contraseña usando un token válido de un solo uso.
- `POST /api/v1/auth/change-password`: cambiar la contraseña autenticado; revoca las sesiones anteriores.
- `POST /api/v1/auth/verify-email`: consumir un token de verificación de un solo uso.

La política de contraseña y el rate limiting conceptual se configuran mediante `PASSWORD_*` y `AUTH_RATE_LIMIT_*` en `.env`. Los hosts aceptados se controlan con `TRUSTED_HOSTS`; fuera de desarrollo también se deshabilitan los endpoints públicos de documentación.

Las cuentas de acceso local usan las variables `DEMO_CLIENT_PASSWORD`,
`DEMO_ADVISOR_PASSWORD` y `DEMO_SUPERVISOR_PASSWORD`. El archivo
`.env.example` incluye `demo-password-local` como valor de desarrollo para las
tres cuentas; el seed guarda únicamente hashes Argon2.

## Perfil y preferencias de usuario

Cada usuario autenticado solo puede consultar y modificar su propio perfil:

- `GET /api/v1/users/me/profile`
- `PATCH /api/v1/users/me/profile`
- `GET /api/v1/users/me/preferences`
- `PATCH /api/v1/users/me/preferences`

El perfil permite actualizar `full_name` y `phone` con normalización y validación
estricta. La respuesta no incluye `password_hash`, sesiones, permisos, secretos ni
relaciones internas. Los campos `id`, `email`, `role`, `is_active` y los timestamps
no son editables desde estos endpoints. El correo permanece inmutable en esta fase:
no se expone un cambio parcial; será necesario un flujo posterior con correo
pendiente, reautenticación, token de un solo uso, proveedor `EmailProvider`,
anti-enumeración y activación posterior a la verificación.

Las preferencias persistentes controlan notificaciones in-app y por correo,
asignaciones, cambios de estado, comentarios, alertas SLA, idioma y zona horaria.
Por defecto todas las notificaciones ordinarias están habilitadas, el idioma es
`es` y la zona horaria es `UTC`. Los eventos `security_event` y
`password_changed` no se pueden desactivar. La zona horaria se valida contra
identificadores IANA soportados y todos los timestamps internos se guardan en UTC.

Los cambios de perfil y preferencias generan una notificación de seguridad y un
registro en `user_profile_audits`. La auditoría conserva solo valores redactados;
nunca guarda contraseñas, tokens, secretos ni teléfonos completos. La migración
`0008_user_profile_preferences` crea las tablas `user_preferences` y
`user_profile_audits` sin modificar usuarios existentes.

## Notificaciones in-app

Las notificaciones se almacenan de forma persistente y cada usuario solo puede
consultar o modificar las suyas. El listado usa el contrato paginado común:
`page`, `page_size`, `total`, `total_pages` e `items`, ordenado por `created_at`
descendente e `id` descendente. Los endpoints son:

- `GET /api/v1/notifications`: listar notificaciones propias.
- `GET /api/v1/notifications/unread-count`: contar las no leídas propias.
- `PATCH /api/v1/notifications/{notification_id}/read`: marcar una notificación propia.
- `POST /api/v1/notifications/read-all`: marcar todas las notificaciones propias.

Los eventos actuales cubren creación, asignación, comentarios, cambios de
estado, reapertura, cierre y cambio de contraseña. Se admiten también tipos
`security_event`, `sla_warning` y `sla_breached` para futuras integraciones,
pero la lógica SLA queda fuera de esta fase. El proveedor de canales está
desacoplado mediante `NotificationProvider`; el proveedor local
`NoopNotificationProvider` no hace llamadas de red ni escribe información
sensible. La metadata solo acepta valores escalares validados y rechaza claves
que puedan contener contraseñas, hashes, tokens, secretos o credenciales.

## Pruebas y calidad

```bash
pytest
ruff check .
ruff format --check .
alembic check
```

El seed es idempotente: se puede ejecutar varias veces sin duplicar roles,
usuarios, categorías, FAQ, tickets, conversaciones, mensajes, asignaciones,
comentarios ni historiales. Conserva datos existentes y agrega un conjunto
operativo con tickets en todos los estados, prioridades y canales, incluidos
tickets asignados, pendientes, resueltos, cerrados y cancelados. También deja
una categoría inactiva sin referencias para validar la administración del
catálogo.

Después de cargar el seed, el cliente puede consultar sus tickets, el asesor
puede revisar la bandeja y sus asignaciones, y el supervisor puede consultar
reportes, asesores, tickets e información de seguimiento. Los datos visibles
están redactados en español y no incluyen información bancaria sensible.

## Chatbot FAQ determinista y contexto conversacional

El chatbot responde exclusivamente con FAQs publicadas, activas, vigentes y de
categorías activas. Normaliza acentos, mayúsculas, espacios, palabras clave,
tags, sinónimos, título, pregunta, resumen y respuesta, y calcula un puntaje
determinista de relevancia. Los umbrales, turnos máximos, longitud, conversaciones
anónimas por hora y límites de mensajes se configuran mediante `CHATBOT_*`. Cada
mensaje consume una solicitud de búsqueda y se limita por conversación y por
minuto; las conversiones a ticket también tienen un límite diario por cliente.
La cuota de conversaciones anónimas usa una huella HMAC de la dirección de
conexión y nunca guarda la IP en claro.

La IA generativa es opcional y está deshabilitada por defecto. Una confianza
alta responde directamente; una confianza media solicita aclaración con opciones
provenientes de FAQs reales; una confianza baja ofrece derivación y no inventa
información. Sin proveedor habilitado, el flujo determinista funciona sin red.

Las conversaciones conservan únicamente contexto operativo mínimo: intención,
FAQ y categoría utilizadas, términos normalizados no sensibles, turnos,
confianza, pregunta pendiente, estado y última actividad. Los mensajes no se
registran completos en auditoría.

La máquina de estados es:

`ACTIVE → WAITING_CLARIFICATION → ESCALATED → CONVERTED_TO_TICKET`

También contempla `CLOSED` y `EXPIRED`. Los estados terminales no aceptan nuevos
mensajes. Las derivaciones pueden ser manuales o automáticas y son auditadas. El
escalamiento repetido es idempotente; reset conserva el historial y limpia solo
el contexto operativo. Convertir una conversación ya convertida devuelve 409 y
no crea un segundo ticket. Los payloads inválidos devuelven 422 y los límites
de abuso devuelven 429.

Endpoints disponibles:

- `GET /api/v1/faqs` y `GET /api/v1/faqs/{faq_id}`: consultar FAQ activas.
- `POST /api/v1/chat/conversations`: iniciar una conversación anónima o autenticada.
- `GET /api/v1/chat/conversations/{conversation_id}`: consultar una conversación autorizada.
- `POST /api/v1/chat/conversations/{conversation_id}/messages`: guardar el mensaje y obtener la respuesta FAQ del bot.
- `POST /api/v1/chat/conversations/{conversation_id}/link-user`: asociar una conversación anónima al cliente autenticado.
- `POST /api/v1/chat/conversations/{conversation_id}/reset`: reiniciar el contexto sin borrar el historial.
- `POST /api/v1/chat/conversations/{conversation_id}/escalate`: derivar la conversación a atención humana.
- `POST /api/v1/chat/conversations/{conversation_id}/feedback`: registrar feedback útil/no útil de la FAQ utilizada.

Ejemplo de uso:

```bash
# Conversación anónima
curl -X POST http://localhost:8000/api/v1/chat/conversations

# Mensaje FAQ
curl -X POST http://localhost:8000/api/v1/chat/conversations/<conversation_id>/messages \
  -H "Content-Type: application/json" \
  -d '{"content":"¿Qué requisitos necesito para una tarjeta?"}'

# Login y asociación de una conversación
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"cliente@demo.com","password":"demo-password-local"}'
curl -X POST http://localhost:8000/api/v1/chat/conversations/<conversation_id>/link-user \
  -H "Authorization: Bearer <access_token>"
```

### RAG/IA con guardrails

La Fase 12 usa la recuperación determinista existente como RAG local mínimo. No
se agregan embeddings, almacén vectorial, FULLTEXT ni un servicio externo: el
volumen y el modelo actual de FAQs no justifican esa infraestructura. Solo se
recuperan FAQs publicadas, activas, vigentes y de categorías activas; el contexto
se limita por cantidad de documentos y caracteres.

La generación está desacoplada mediante `AIProvider` y `NoopAIProvider`. El
entorno incluye `CHATBOT_AI_*`, pero `CHATBOT_AI_ENABLED=false` y
`CHATBOT_AI_PROVIDER=noop` son los valores seguros por defecto. No hay API key ni
proveedor externo incluido en el repositorio; un adaptador aprobado puede
inyectarse detrás de la interfaz sin modificar el chatbot ni sus endpoints.
El adaptador opcional `http_json` espera un gateway interno configurado por
`CHATBOT_AI_ENDPOINT`, `CHATBOT_AI_API_KEY` y `CHATBOT_AI_MODEL`; si falta
cualquier valor o el endpoint es inválido, se usa Noop y el fallback determinista.
El contrato del gateway recibe solo `model`, `input`, `policy`,
`prompt_version` y `context`, y debe devolver `text` y `source_ids`.

Antes de invocar IA se validan el mensaje, las fuentes y los guardrails contra
prompt injection, solicitudes de secretos y contenido malicioso. La salida debe
referenciar fuentes recuperadas, respetar el límite configurado y superar la
validación de contexto. Timeout, error, circuito abierto, límite o proveedor
deshabilitado conservan la respuesta determinista o escalan según la Fase 11.
No existen reintentos indefinidos ni scheduler.

Las respuestas de bot exponen únicamente `response_source` (`DETERMINISTIC`,
`AI` o `FALLBACK`) y referencias comprensibles de título/categoría. La traza
interna conserva proveedor, modelo, versión de prompt, fuentes, scores, duración,
validación y motivo de fallback, sin prompts completos, respuestas técnicas,
tokens, secretos ni datos personales. Los eventos `CHATBOT_AI` se auditan con
metadatos mínimos y redactados.

## Gestión de tickets

Un cliente autenticado puede crear un ticket manual o convertir una conversación
autenticada y no resuelta mediante
`POST /api/v1/chat/conversations/{conversation_id}/convert-to-ticket`. Cada ticket
recibe un código `TCK-...` único, categoría, prioridad y estado inicial `NUEVO`.
La conversación cambia a `CONVERTED_TO_TICKET`; los reintentos responden `409` y
no crean tickets duplicados. Visitantes deben asociar primero la conversación a
un cliente. Asesores y supervisores no pueden activar ni usar el chatbot.

Endpoints principales:

- `POST /api/v1/tickets`: creación manual autenticada.
- `GET /api/v1/tickets/mine`: tickets del cliente autenticado.
- `GET /api/v1/tickets`: consulta global para asesores y supervisores.
- `GET /api/v1/tickets/{ticket_id}` y `GET /api/v1/tickets/{ticket_id}/history`: seguimiento y trazabilidad.
- `POST /api/v1/tickets/{ticket_id}/comments`: agregar comentarios autorizados.
- `GET /api/v1/tickets/{ticket_id}/comments`: recuperar los comentarios autorizados en orden ascendente por fecha.
- `POST /api/v1/tickets/{ticket_id}/assignments`: asignación realizada por un supervisor.
- `POST /api/v1/tickets/{ticket_id}/status`: transición de estado válida.
- `POST /api/v1/tickets/{ticket_id}/close`, `/reopen` y `/cancel`: cierre, reapertura con motivo y cancelación con motivo.

Ejemplos:

```bash
# Crear ticket manual
curl -X POST http://localhost:8000/api/v1/tickets \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"category_id":"<category_id>","subject":"Consulta","description":"Necesito orientación","priority":"MEDIA"}'

# Consultar tickets propios
curl http://localhost:8000/api/v1/tickets/mine \
  -H "Authorization: Bearer <access_token>"

# Listado paginado y filtrado para un cliente o para personal autorizado
curl "http://localhost:8000/api/v1/tickets/mine?page=1&page_size=20&search=tarjeta&status=EN_PROCESO" \
  -H "Authorization: Bearer <access_token>"
curl "http://localhost:8000/api/v1/tickets?page=1&page_size=20&source=CHATBOT&assigned_advisor_id=<advisor_id>" \
  -H "Authorization: Bearer <staff_access_token>"

# Cambiar estado y consultar historial
curl -X POST http://localhost:8000/api/v1/tickets/<ticket_id>/status \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"status":"EN_PROCESO"}'
curl http://localhost:8000/api/v1/tickets/<ticket_id>/history \
  -H "Authorization: Bearer <access_token>"

# Recuperar comentarios
curl http://localhost:8000/api/v1/tickets/<ticket_id>/comments \
  -H "Authorization: Bearer <access_token>"
```

Las transiciones, asignaciones, comentarios, cancelaciones y motivos quedan
registrados en `ticket_history` junto con el actor y los valores anterior y nuevo.
Los tickets cerrados no admiten modificaciones directas.

### Paginación, búsqueda y filtros de tickets

Los endpoints de listados conservan por compatibilidad la respuesta histórica
`TicketRead[]` cuando se llaman sin `page` ni `page_size`. Los clientes que
necesiten paginación deben enviar `page` y/o `page_size`; en ese modo la respuesta
es un objeto con `page`, `page_size`, `total`, `total_pages` e `items`.
`page_size` admite valores entre 1 y 100 y las páginas empiezan en 1. Una página
fuera de rango devuelve `items: []` sin error.

Se mantienen los filtros `status`, `category_id`, `priority`, `created_from` y
`created_to` (ambos límites de fecha son inclusivos) y se agregan `search`,
`source`, `client_id` y `assigned_advisor_id`. `search` busca código de
seguimiento, asunto y descripción; para asesores y supervisores también busca
nombre y correo del cliente. `client_id` y la búsqueda de datos del cliente solo
se aplican en la consulta interna autorizada. Un cliente siempre queda limitado
a sus propios tickets, aunque envíe otro `client_id`.

Los resultados se ordenan de forma estable por `created_at` descendente e `id`
descendente. El modo paginado usa consultas SQL limitadas y un conteo separado;
los listados legacy se conservan para no romper el frontend actual y no deben
usarse para bandejas grandes.

### Directorio de asesores

El supervisor puede consultar `GET /api/v1/users/advisors` para seleccionar un
asesor activo al asignar un ticket. La ruta requiere un token Bearer con rol
`SUPERVISOR`, ordena los resultados por nombre y devuelve únicamente `id`,
`full_name`, `email` y `role` con valor `ASESOR`.

```bash
curl http://localhost:8000/api/v1/users/advisors \
  -H "Authorization: Bearer <supervisor_access_token>"
```

## Administración de conocimiento y reportes

Las lecturas públicas de FAQ y categorías muestran únicamente registros activos.
La creación, edición, activación y desactivación requieren rol `SUPERVISOR`.
Las categorías usadas por tickets no pueden desactivarse y las FAQ mantienen su
`created_by`, fecha de creación y fecha de modificación.

Endpoints de conocimiento:

- `GET /api/v1/faqs` y `GET /api/v1/faqs/{faq_id}`
- `POST /api/v1/faqs`, `PATCH /api/v1/faqs/{faq_id}` y `PATCH /api/v1/faqs/{faq_id}/status`
- `GET /api/v1/faqs/admin`: listado paginado editorial, solo `SUPERVISOR`.
- `PATCH /api/v1/faqs/{faq_id}/workflow`: transiciones `DRAFT`, `REVIEW`,
  `PUBLISHED` y `ARCHIVED`, solo `SUPERVISOR`.
- `GET /api/v1/faqs/{faq_id}/history`: snapshots editoriales, solo `SUPERVISOR`.
- `POST /api/v1/faqs/{faq_id}/feedback`: utilidad anónima o de un `CLIENTE`.
- `GET /api/v1/faqs/admin/metrics/utility`: métricas agregadas, solo `SUPERVISOR`.
- `GET /api/v1/categories`, `POST /api/v1/categories`, `PATCH /api/v1/categories/{category_id}` y `PATCH /api/v1/categories/{category_id}/status`

Las FAQs conservan pregunta, respuesta y keywords existentes, y agregan título,
resumen, tags, sinónimos, intención, prioridad, orden, contenido normalizado,
autor/editor y fechas editoriales. Los nuevos contenidos nacen como `DRAFT`.
Una publicación requiere pasar por `REVIEW`, categoría activa y contenido válido.
Editar una FAQ publicada crea una nueva versión y la devuelve a `REVIEW`, sin
destruir el snapshot anterior.

El listado público conserva la respuesta histórica cuando no se envía paginación.
Con `page`/`page_size` devuelve `page`, `page_size`, `total`, `total_pages` e
`items`. Admite `search`, `category_id`, `tag` y rangos de publicación. La
búsqueda combina título, pregunta, respuesta, resumen, keywords, tags,
sinónimos y categoría, con normalización de mayúsculas, acentos y espacios y
orden determinista por relevancia, prioridad, orden editorial y fecha.
Actualmente usa `LIKE`/`ILIKE` parametrizado sobre contenido normalizado; una
evolución a FULLTEXT queda documentada para volúmenes mayores.

El chatbot solo recupera FAQs `PUBLISHED`, activas, vigentes y pertenecientes a
categorías activas. Usa keywords, tags, sinónimos, pregunta, título, resumen y
respuesta para un ranking determinista, registra feedback agregado y mantiene un
fallback seguro. La capa RAG/IA opcional de la Fase 12 está documentada en la
sección anterior y permanece deshabilitada por defecto.

Endpoints de dashboard, disponibles solo para supervisores:

- `GET /api/v1/reports/summary`
- `GET /api/v1/reports/by-status`
- `GET /api/v1/reports/by-category`
- `GET /api/v1/reports/by-priority`
- `GET /api/v1/reports/resolution-time`

Todos aceptan los filtros opcionales `from`, `to`, `category_id`, `status` y
`priority`. Por ejemplo:

```bash
curl "http://localhost:8000/api/v1/reports/summary?status=RESUELTO&priority=ALTA" \
  -H "Authorization: Bearer <supervisor_access_token>"
curl "http://localhost:8000/api/v1/reports/by-category?from=2026-01-01T00:00:00Z&to=2026-12-31T23:59:59Z" \
  -H "Authorization: Bearer <supervisor_access_token>"
```

Los reportes devuelven ceros y listas vacías cuando no hay datos, validan rangos
de fechas y no exponen información sensible.

### Exportaciones de reportes

Los supervisores pueden exportar los reportes operativos mediante:

`GET /api/v1/reports/{report_name}/export?format=csv`

Los nombres disponibles son `summary`, `by-status`, `by-priority`,
`by-category`, `by-source`, `by-advisor`, `created-tickets`,
`resolved-tickets`, `first-response-time`, `resolution-time`,
`sla-compliance`, `conversations`, `faq-utility` y `operational-activity`.

Se conservan los filtros `from`, `to`, `category_id`, `status` y `priority`, y
se agregan `source`, `advisor_id`, `client_id`, `sla_compliant`, `search` y
`limit`. Las fechas deben incluir zona horaria y se normalizan a UTC. El rango
máximo y las filas máximas se configuran mediante `REPORT_EXPORT_MAX_RANGE_DAYS`
y `REPORT_EXPORT_MAX_ROWS` (por defecto 366 días y 10.000 filas).

La respuesta usa `text/csv`, `Content-Disposition` generado por el servidor,
`Cache-Control: no-store` y un nombre de archivo seguro. Incluye la fecha UTC y
los filtros aplicados. Los valores que comienzan por `=`, `+`, `-` o `@` se
escapan para evitar CSV injection. No se incluyen hashes, tokens, secretos,
contraseñas ni datos de cliente innecesarios.

El entorno actual no tiene una dependencia XLSX instalada; `format=xlsx`
responde `422` de forma explícita. No se generan archivos persistentes. Cada
intento autenticado queda registrado en `report_export_audits` con actor,
reporte, formato, filtros no sensibles, cantidad, resultado y fecha UTC.

## Auditoría global y trazabilidad

La auditoría transversal se almacena en `audit_logs` mediante un módulo
append-only. Conserva el actor, rol, recurso, acción, resultado, fecha UTC y
datos mínimos antes/después cuando son necesarios. Los historiales específicos
(`ticket_history`, `sla_policy_history`, `user_profile_audits`, `faq_versions`
y `report_export_audits`) se mantienen; la tabla global los complementa y no los
reemplaza.

Solo `SUPERVISOR` puede consultar la auditoría global mediante
`GET /api/v1/audit`. La respuesta es paginada (`page`, `page_size`, `total`,
`total_pages`, `items`) y admite filtros por actor, recurso, acción, tipo de
evento, resultado, rango UTC e identificadores. No existen endpoints de
actualización ni eliminación.

La redacción central elimina contraseñas, hashes, tokens, cookies, secretos,
credenciales, correos y claves equivalentes; además limita profundidad y tamaño
y reemplaza binarios por una marca segura. No se guardan contenidos completos de
conversaciones, archivos ni credenciales. La migración `0014_global_audit`
crea índices para fecha, actor, recurso, evento, acción, objetivo y resultado.

## SLA y tiempos operativos

Cada ticket recibe un registro SLA al crearse. Las políticas persistentes se
seleccionan por prioridad y pueden especializarse por categoría y origen
(`MANUAL` o `CHATBOT`). Los valores iniciales por prioridad se configuran con
`SLA_POLICY_DEFAULTS`; la zona horaria interna es UTC y el calendario local
soportado es `24x7`.

El SLA registra primera respuesta, vencimiento de resolución, pausas, tiempo
acumulado, advertencias, incumplimientos y cumplimiento. Solo comentarios de
asesores o supervisores cuentan como primera respuesta. Los comentarios del
cliente no detienen ese reloj. CERRADO y CANCELADO dejan de consumir tiempo;
una reapertura continúa el SLA existente y no reinicia sus vencimientos.

Endpoints protegidos:

- `GET /api/v1/tickets/{ticket_id}/sla`: consultar el SLA autorizado del ticket.
- `POST /api/v1/tickets/{ticket_id}/sla/pause` y `/resume`: pausar o reanudar
  explícitamente con permisos de personal y trazabilidad.
- `POST /api/v1/sla/evaluate`: evaluar bajo demanda; exclusivo para `SUPERVISOR`.
- `GET /api/v1/sla/policies`, `POST /api/v1/sla/policies` y
  `PATCH /api/v1/sla/policies/{policy_id}`: administrar políticas; exclusivos
  para `SUPERVISOR` y auditados en `sla_policy_history`.

La evaluación es idempotente y genera notificaciones `sla_warning` y
`sla_breached` sin duplicados. No introduce scheduler, Redis, Celery ni un
servicio externo; en desarrollo se ejecuta mediante el endpoint protegido o el
servicio interno testeable.

## Operación de asesores

La operación interna reutiliza el contrato paginado `page`, `page_size`, `total`,
`total_pages` e `items` mediante:

- `GET /api/v1/tickets/operations?queue=assigned_to_me`: bandeja del asesor autenticado.
- `GET /api/v1/tickets/operations?queue=unassigned`: cola de tickets sin asesor.
- `GET /api/v1/tickets/operations?queue=assigned_to_advisor&advisor_id=<id>`:
  consulta exclusiva del supervisor.
- `queue=sla_soon`, `sla_overdue`, `pending_first_response` y
  `recently_updated`: bandejas operativas filtrables.

Las bandejas aceptan los filtros existentes de estado, prioridad, categoría,
origen, búsqueda y fechas, además de `updated_from`, `updated_to` y
`recent_hours`. Los supervisores conservan el listado global de `/api/v1/tickets`;
los asesores solo reciben tickets asignados a sí mismos en ese endpoint. La cola
no asignada se consulta mediante la ruta operativa.

Acciones internas:

- `POST /api/v1/tickets/{ticket_id}/assignments`: asignar o reasignar, solo supervisor.
- `POST /api/v1/tickets/{ticket_id}/take`: tomar un ticket no asignado, solo asesor.
- `POST /api/v1/tickets/{ticket_id}/release`: liberar la asignación propia o una
  asignación global autorizada del supervisor.

Cada mutación registra actor, valores anterior/nuevo e historial, cierra la
asignación previa al reasignar y notifica al nuevo asesor. Los tickets exponen
`version` y `updated_at`; las solicitudes de mutación pueden enviar
`expected_version` y reciben `409` si otra operación actualizó el ticket.
Los estados existentes no cambian de semántica: cancelación sigue siendo solo de
supervisor, y un asesor debe tener asignación activa para operar un ticket.

La matriz operativa de transiciones es:

| Rol | Operaciones de estado autorizadas |
| --- | --- |
| `CLIENTE` | No cambia estados; solo consulta y comenta sus tickets según las reglas existentes. |
| `ASESOR` | Opera tickets asignados: `ASIGNADO → EN_PROCESO`, `EN_PROCESO → PENDIENTE_CLIENTE/RESUELTO`, `PENDIENTE_CLIENTE → EN_PROCESO` y `RESUELTO → EN_PROCESO/CERRADO`; la reapertura exige motivo y la cancelación está prohibida. |
| `SUPERVISOR` | Puede asignar, reasignar, liberar y ejecutar las transiciones válidas globales; la cancelación exige motivo. |

Los estados `CERRADO` y `CANCELADO` son terminales. `take` solo permite que un
asesor tome un ticket abierto sin asignación y `release` devuelve un ticket
asignado a `NUEVO`; ambas operaciones quedan auditadas.

No se implementan acciones masivas en esta fase: aún no existe un contrato seguro
de límites, resultados individuales, idempotencia y rollback por ticket.

## Adjuntos seguros

Los adjuntos pertenecen siempre a un ticket y pueden asociarse opcionalmente a
un comentario existente del mismo ticket. El binario no se guarda en MySQL ni se
expone como contenido estático: en desarrollo se almacena bajo
`ATTACHMENT_STORAGE_DIR` mediante un `StorageProvider` local. La clave física es
interna y aleatoria; el API nunca devuelve la ruta del servidor.

Endpoints:

- `POST /api/v1/tickets/{ticket_id}/attachments`: multipart con `file` y
  `comment_id` opcional.
- `GET /api/v1/tickets/{ticket_id}/attachments`: listar metadatos activos.
- `GET /api/v1/attachments/{attachment_id}/download`: descargar con headers
  seguros y `Content-Disposition: attachment`.
- `DELETE /api/v1/attachments/{attachment_id}`: eliminación lógica auditable y
  limpieza física local controlada.

El cliente solo puede usar adjuntos de sus propios tickets; un asesor necesita
tener el ticket asignado y el supervisor puede acceder a tickets autorizados.
Se validan extensión, MIME declarado, firma del contenido, tamaño, hash
SHA-256, nombre y cuotas por ticket. La configuración local permite PDF, PNG,
JPG/JPEG, GIF y texto plano; ejecutables, scripts, HTML y SVG no forman parte
de la lista permitida. Los archivos subidos se excluyen de Git mediante
`var/uploads/` en `.gitignore`.
