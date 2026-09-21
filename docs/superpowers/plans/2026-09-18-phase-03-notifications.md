# Fase 03 — Sistema de notificaciones Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Añadir notificaciones in-app persistentes, aisladas por usuario, idempotentes y transaccionales, con una interfaz segura para proveedores futuros.

**Architecture:** Crear `app/modules/notificaciones/` con modelo, esquema, repositorio, servicio y router. `TicketService` y `AuthService` recibirán el servicio de notificaciones por inyección opcional y crearán eventos antes de sus commits actuales; el proveedor desacoplado se ejecutará de forma tolerante a fallos para no romper la operación principal.

**Tech Stack:** FastAPI, SQLModel, MySQL 8.4, Alembic, Pydantic, Pytest y Ruff.

## Global Constraints

- No modificar el frontend.
- No crear commits intermedios ni hacer push.
- Mantener la arquitectura `router → service → repository → model`.
- Mantener todos los endpoints y contratos existentes.
- Usar autenticación `CurrentUser` y aislar por `recipient_user_id`.
- No persistir ni devolver secretos, contraseñas, hashes o tokens.
- Crear migración Alembic solo para la nueva tabla e índices necesarios.

## Diseño del contrato

- `GET /api/v1/notifications?page=1&page_size=20` devuelve siempre `{page, page_size, total, total_pages, items}` y ordena por `created_at DESC, id DESC`.
- `GET /api/v1/notifications/unread-count` devuelve `{unread_count}`.
- `PATCH /api/v1/notifications/{notification_id}/read` devuelve la notificación propia ya leída; repetir la operación es idempotente.
- `POST /api/v1/notifications/read-all` devuelve `{updated_count}` y solo afecta al usuario autenticado.
- No se añade DELETE porque la especificación oficial exige listado y lectura, no borrado.

## Eventos y destinatarios

- `ticket_created`: cliente creador; incluye `related_ticket_id` y, en conversiones, `related_conversation_id`.
- `ticket_assigned`: asesor asignado.
- `ticket_status_changed`: cliente cuando cambia el estado.
- `ticket_commented`: contraparte disponible; asesor asignado para comentarios del cliente y cliente para comentarios internos.
- `ticket_reopened` y `ticket_closed`: cliente.
- `password_changed`: usuario afectado en cambio o recuperación de contraseña.
- `security_event`, `sla_warning` y `sla_breached` quedan soportados como tipos persistibles, sin inventar eventos de SLA antes de la Fase 05.

## Idempotencia y seguridad

- `dedupe_key` interno único evita insertar dos veces el mismo evento lógico.
- `metadata_json` se valida como mapa de escalares acotado y rechaza claves sensibles (`password`, `token`, `secret`, `authorization`, etc.).
- `NotificationRead` expone únicamente campos públicos y transforma los nombres internos `notification_type`/`metadata_json` a `type`/`metadata`.
- Los fallos del `NotificationProvider` se absorben sin logs sensibles y no abortan la transacción de ticket o autenticación.

## Plan TDD

1. Crear pruebas RED para persistencia por eventos, idempotencia, provider tolerante a fallos, paginación, conteo, lectura, aislamiento, 401/403/404/422 y ausencia de campos sensibles.
2. Implementar modelo, schemas, repositorio, servicio y proveedor Noop; registrar el modelo.
3. Implementar los cuatro endpoints protegidos y añadir el router v1.
4. Integrar creación, asignación, comentarios, estados, reapertura, cierre, conversión de conversación y cambio/recuperación de contraseña antes de sus commits.
5. Crear la migración `0005_notifications` con tabla, clave única de deduplicación e índices de destinatario/lectura/fecha.
6. Actualizar README/OpenAPI y ejecutar regresión completa, Alembic, health, docs, lint, formato y `git diff --check`.
