# Modelo de amenazas del backend de tickets y chatbot

## Alcance y supuestos

Este análisis corresponde al código del prototipo académico del backend. Se
asume que la API puede estar expuesta a Internet y atender a varios usuarios
autenticados, además de conversaciones anónimas. El prototipo no es un sistema
oficial del Banco GNB Perú ni procesa operaciones bancarias reales.

El análisis cubre los flujos de conversación, búsqueda de FAQ, consulta de
tickets, borradores y conversión autenticada. No se verificaron infraestructura,
configuración del despliegue, proxy/WAF, monitoreo, controles de cuenta del
proveedor, políticas de retención, configuración real de secretos ni base de
datos de producción. No se accedió a datos productivos.

## Activos

- Identidad, roles y sesiones JWT de clientes y personal.
- Tickets: códigos de seguimiento, asuntos, descripciones, estado y, en los
  flujos internos, historial y comentarios.
- Conversaciones, borradores, selecciones pendientes y referencias de tickets.
- Contenido y fuentes de las FAQ publicadas.
- Claves de proveedor IA, límites de gasto, trazas de llamadas y métricas.
- Integridad de la conversión entre una conversación y un ticket.

## Límites de confianza

1. **Internet → API:** textos, IDs de opciones y códigos de ticket son datos no
   confiables, aunque provengan de un cliente autenticado.
2. **API → base de datos:** los servicios aplican permisos y propiedad; los
   repositorios realizan las consultas. Los routers no deben decidir permisos
   ni construir SQL.
3. **API → proveedor IA:** el proveedor es un tercero. Sus respuestas y llamadas
   de herramienta son propuestas no confiables; el servicio no le entrega una
   conexión a base de datos ni permite que ejecute herramientas directamente.
4. **FAQ recuperadas → modelo:** el contenido es público según su publicación y
   elegibilidad, pero puede contener texto malicioso o incorrecto.
5. **Conversación → tickets:** convertir un borrador es una mutación que requiere
   autenticación, propiedad, borrador válido y confirmación explícita.

## Amenazas y controles observados

| Amenaza | Controles implementados | Riesgo residual |
|---|---|---|
| Un cliente consulta un ticket ajeno o enumera códigos | La identidad se obtiene del JWT; las consultas pasan por servicios de tickets propios; la búsqueda usa propietario autenticado. Un ticket ajeno e inexistente comparten una respuesta pública. | Revisar autorización en cualquier endpoint nuevo que acepte IDs de ticket o conversación. |
| Un usuario anónimo accede a datos privados | Las consultas privadas requieren cliente autenticado; la conversación se vincula al usuario antes de responder datos privados. | La conversación anónima puede conservar mensajes personales que el usuario haya escrito voluntariamente; definir retención y límites de almacenamiento en despliegue. |
| Prompt injection en mensajes, FAQ, asunto o descripción de ticket | El modelo solo recibe un catálogo cerrado de herramientas con argumentos validados; el backend vuelve a validar acción, estado, campos y propiedad. Las instrucciones en asunto o descripción provocan omitir esos campos de la proyección. Las respuestas se someten a validación y, si fallan, se usa fallback determinístico. | Los detectores y validadores son heurísticos; no garantizan detectar toda instrucción indirecta ni todo contenido adversarial. Mantener revisión humana y ampliar pruebas adversariales. |
| El modelo inventa una acción, prioridad, estado o dato de ticket | No hay herramienta de creación/conversión. La prioridad requiere una selección explícita y no ambigua; las categorías se comprueban contra el catálogo activo; las consultas y transiciones vuelven a validarse en el backend. | La respuesta natural puede ser rechazada por guardrails y resultar menos fluida; los cambios de catálogos y enums requieren pruebas y actualización de contrato. |
| Exposición de datos privados al proveedor | La orquestación inicial no recibe datos del ticket. Para síntesis, el backend construye una proyección de campos solicitados y autorizados: código, asunto, estado o descripción. No incluye IDs internos, identidad, asignación, prioridad ni comentarios del asesor. Los dos mensajes de usuario recientes se limitan; los códigos se reemplazan y la minimización redacta credenciales comunes, correos, teléfonos formateados y números largos sensibles. | El proveedor externo recibe texto de usuario y, para síntesis autorizada, algunos campos del ticket. La residencia, retención y uso por el proveedor dependen de su contrato y configuración, que no se verificaron aquí. |
| Doble creación por timeout/reintento o payload alterado | La conversión valida borrador confirmado y propietario; el reintento idéntico recupera el ticket existente, incluso ante la colisión de unicidad cubierta. Un payload distinto devuelve `409`; la lectura de la conversación incluye `converted_ticket`. | La garantía depende de las restricciones de unicidad de la base de datos desplegada y de que los conflictos ajenos a la misma operación no sean absorbidos. |
| Abuso para agotar proveedor o costo | Límites de llamadas, presupuesto diario, caché y circuit breaker; errores del proveedor caen al fallback. | El presupuesto diario y estados de protección son locales al proceso. No coordinan réplicas y se reinician con el proceso; configurar también límites en la cuenta del proveedor y protección de infraestructura. |
| Reutilización de opción antigua o manipulación del flujo | El backend ofrece IDs de opciones vinculados al estado y vuelve a comprobar pertenencia y vigencia; selecciones inválidas no ejecutan acciones. | El frontend debe seguir mostrando solo las opciones estructuradas recibidas; clientes de API alternativos también están sujetos a validación del backend. |

## Controles operativos que quedan fuera de este cambio

- Límites de red, WAF, detección de bots, TLS y configuración de cabeceras del
  despliegue.
- Rotación, permisos y monitorización de credenciales del proveedor.
- Coordinación distribuida de presupuestos y rate limits entre réplicas.
- Cifrado, retención, borrado y respaldo de conversaciones y tickets en el
  entorno desplegado.
- Alertas y respuesta ante exfiltración, abuso de cuenta o gasto anómalo.
- Revisión contractual de almacenamiento y uso de datos del proveedor IA.

## Recomendaciones de seguimiento

1. Mantener en CI las pruebas de aislamiento entre clientes, prompt injection,
   herramientas inválidas, prioridad explícita, confirmación y reintentos
   concurrentes.
2. En despliegues con varias réplicas, trasladar límites y presupuesto a un
   almacén compartido o aplicar límites equivalentes en el gateway/proveedor.
3. Verificar, antes de producción, la política contractual de datos del
   proveedor, retención de logs, gestión de secretos y controles de red.
4. Tratar los tickets y FAQ como entrada no confiable aunque los haya creado un
   usuario autenticado o un operador.
