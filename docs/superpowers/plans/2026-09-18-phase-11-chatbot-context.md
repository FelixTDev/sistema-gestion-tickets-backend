# Plan Fase 11 — Chatbot FAQ determinista, contexto y derivación

## Objetivo

Evolucionar el chatbot FAQ existente sin RAG, LLM ni proveedores externos:
matching determinista sobre FAQs públicas, confianza, aclaraciones, contexto
seguro, derivación y conversión trazable a ticket.

## Contratos preservados

- Se mantienen los endpoints actuales de conversaciones, mensajes, asociación y
  conversión.
- `offers_ticket` continúa presente; se complementa con estado y señales nuevas.
- La conversión reutiliza `TicketService.convert_conversation` y su unicidad por
  `conversation_id`.

## Cambios por capas

1. Pruebas RED de normalización, ranking, confianza, aclaración, estados,
   aislamiento, límites, derivación, feedback y auditoría.
2. Extender conversación con contexto seguro y estados explícitos.
3. Crear configuración de límites y servicio determinista de intención/FAQ.
4. Añadir endpoints mínimos de reinicio, derivación y feedback si los contratos
   actuales no los cubren.
5. Integrar `AuditService`, notificaciones y conversión existente.
6. Migrar con Alembic, actualizar README/OpenAPI y ejecutar regresión completa.

## Seguridad

- Solo FAQs publicadas, activas, vigentes y de categorías activas.
- No se persisten secretos ni datos sensibles innecesarios.
- Visitantes solo acceden a conversaciones anónimas; clientes a las propias.
- Asesores y supervisores no usan el chatbot como usuario interno.
- Los mensajes se validan, acotan y no se registran íntegramente en auditoría.
