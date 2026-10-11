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
  clasifican como baja confianza ni ofrecen un ticket por sí solos.
- Las respuestas sobre productos y procesos solo usan FAQs publicadas, activas y
  vigentes con URL HTTPS de `bancognb.com.pe` o `www.bancognb.com.pe` y fecha de
  verificación. La fuente usada se devuelve en `sources[]`.
- Las coincidencias medias o ambiguas piden aclaración sin abrir un ticket ni
  escalar automáticamente. La conversación conserva hasta dos mensajes breves
  de aclaración y, ante una referencia clara, usa el último mensaje del cliente
  y la respuesta anterior del asistente para recuperar el tema.
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
  explícita de asesor usa la escalación existente. Una solicitud explícita de
  ticket devuelve `suggested_action=CONVERT_TO_TICKET`; ese campo no crea el
  ticket.
- `BotMessageResponse.suggested_action` y `conversation_status` son campos
  tipados. `requires_clarification`, `clarification_options`, `sources` y los
  estados existentes siguen disponibles para clientes anteriores.
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
  Esta entrega no habilita ni exige un proveedor externo ni una nueva API key.

## Fuente y vigencia de cada FAQ

`faqs` y `faq_versions` guardan `source_url` y `source_verified_at`. El contrato
acepta únicamente HTTPS en `bancognb.com.pe` o `www.bancognb.com.pe`; no acepta
subdominios imitadores, credenciales, puertos ni HTTP. El chatbot devuelve
`sources[]` con título, categoría, URL y fecha de verificación para que el
frontend pueda mostrar la atribución.

Toda FAQ nueva o modificada debe:

1. Citar una página oficial que respalde la respuesta; registrar fecha de
   verificación en UTC.
2. Usar lenguaje de orientación y enlazar al canal oficial para condiciones
   variables, evaluación crediticia, disponibilidad, tarifas y requisitos.
3. Excluir credenciales, códigos de un solo uso, números completos de tarjeta,
   datos de cuenta, información de clientes, decisiones financieras o promesas
   de resultado.
4. Ser revisada y publicada por un Supervisor. Si cambia el contenido publicado,
   queda inactiva en estado `REVIEW` hasta que se revise y publique de nuevo.
5. Si cambia la URL, su fecha de verificación previa se elimina, a menos que la
   nueva fuente se vuelva a verificar expresamente en la misma actualización.
6. Mantener versiones históricas para auditoría; una actualización no debe
   sobrescribir la procedencia de la versión anterior.

Las URLs de las FAQs iniciales se seleccionaron de páginas oficiales del Banco:

| Tema | Fuente |
| --- | --- |
| Tarjetas | <https://www.bancognb.com.pe/inicio/banca-personas/tarjetas> |
| Bloqueo de tarjeta/canal | <https://www.bancognb.com.pe/inicio/servicio-cliente/reclamaciones-consulta-24hr> |
| Cuentas de ahorro | <https://www.bancognb.com.pe/inicio/banca-personas/cuentas/cuenta-ahorro> |
| Cuentas | <https://www.bancognb.com.pe/inicio/banca-personas/cuentas> |
| Banca Móvil y guías | <https://www.bancognb.com.pe/inicio/canales/canales-digitales/banca-movil-personas> |
| Banca por Teléfono y horarios | <https://www.bancognb.com.pe/inicio/canales/banca-por-telefono> |
| Préstamo personal | <https://www.bancognb.com.pe/inicio/banca-personas/prestamos/prestamo-personal> |
| Reclamos y requerimientos | <https://www.bancognb.com.pe/inicio/servicio-cliente/reclamaciones-consulta> |
| Reclamo formal | <https://www.bancognb.com.pe/inicio/servicio-cliente/reclamaciones> |

Las páginas pueden cambiar después de su revisión. La fecha guardada es una
señal de procedencia, no una garantía de vigencia futura; el responsable debe
volver a verificar el contenido antes de mantenerlo publicado.

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
