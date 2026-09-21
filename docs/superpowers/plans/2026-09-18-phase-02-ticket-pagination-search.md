# Phase 02 — Paginación y búsqueda avanzada de tickets

## Objetivo

Añadir paginación SQL, búsqueda y filtros avanzados a los listados de tickets sin romper los consumidores actuales que esperan listas simples.

## Contrato y compatibilidad

- `GET /api/v1/tickets` y `GET /api/v1/tickets/mine` conservan la respuesta `TicketRead[]` cuando se consumen sin `page` ni `page_size`.
- Al enviar `page` o `page_size`, ambos endpoints devuelven:

  `{ page, page_size, total, total_pages, items }`

- Los parámetros existentes (`status`, `category_id`, `priority`, `created_from`, `created_to`) se conservan.
- Se agregan `search`, `source`, `client_id` y `assigned_advisor_id`.
- `search` busca código, asunto y descripción; para personal autorizado también busca nombre y correo del cliente.
- El cliente siempre queda restringido a sus propios tickets, aunque intente enviar `client_id`.

## Plan TDD

1. Añadir pruebas RED para páginas, límites, búsquedas, filtros combinados, orden estable, fechas inclusivas, conteos, aislamiento por rol, autenticación y regresión de listas simples.
2. Implementar esquemas públicos y parámetros validados sin exponer datos sensibles.
3. Implementar en repositorio una consulta parametrizada de conteo y otra limitada, con orden `created_at DESC, id DESC` y joins solo cuando se requiere buscar al cliente.
4. Integrar el servicio y los routers manteniendo la autorización existente y el modo de respuesta legacy.
5. Añadir índices B-tree compuestos para cliente/fecha, asesor/fecha, estado/fecha y fecha/id; no se agregan tablas ni columnas.
6. Actualizar README y validar OpenAPI, migraciones, pruebas, lint, formato y regresión.

## Riesgos y decisiones

- La búsqueda textual con comodines conserva compatibilidad SQLite/MySQL; los índices B-tree optimizan filtros y orden, pero no convierten una búsqueda `%término%` en full-text.
- Los listados legacy sin paginación siguen materializando todos los elementos por compatibilidad; los consumidores nuevos deben usar `page`/`page_size` para obtener consultas limitadas y conteos.
