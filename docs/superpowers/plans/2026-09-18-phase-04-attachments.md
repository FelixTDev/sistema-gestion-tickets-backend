# Fase 04 — Sistema seguro de adjuntos

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir adjuntos seguros para tickets y comentarios, con metadatos persistentes, almacenamiento local no público, autorización por rol, trazabilidad e interfaz preparada para almacenamiento externo.

**Architecture:** Crear `app/modules/adjuntos/` con `models`, `schemas`, `repositories`, `services` y `api`. El router recibirá multipart y delegará en `AttachmentService`; el repositorio manejará adjuntos, tickets, comentarios e historial; `StorageProvider` abstraerá el binario. El ticket es la relación obligatoria y el comentario es opcional, validado para pertenecer al mismo ticket.

**Tech Stack:** FastAPI `UploadFile`, SQLModel, MySQL 8.4, Alembic, almacenamiento local de desarrollo, Pytest y Ruff.

## Decisiones de contrato

- `POST /api/v1/tickets/{ticket_id}/attachments` recibe `multipart/form-data` con `file` obligatorio y `comment_id` opcional.
- `GET /api/v1/tickets/{ticket_id}/attachments` devuelve únicamente metadatos activos, nunca bytes ni rutas físicas.
- `GET /api/v1/attachments/{attachment_id}/download` devuelve el binario autorizado con `Content-Disposition: attachment`, `X-Content-Type-Options: nosniff` y `Cache-Control: no-store`.
- `DELETE /api/v1/attachments/{attachment_id}` realiza eliminación lógica (`status=DELETED`, `deleted_at`) y deja la metadata para auditoría; el binario local puede eliminarse físicamente de forma controlada después de invalidar el recurso.
- El response público no expone `storage_key`; solo devuelve identificador, metadata y estado.
- Cliente: únicamente sus tickets. Asesor: únicamente tickets asignados a él. Supervisor: acceso global. Un supervisor no obtiene permisos especiales sobre archivos de otros usuarios fuera de sus tickets autorizados.

## Políticas de seguridad

- Extensiones permitidas por configuración: `.pdf`, `.png`, `.jpg`, `.jpeg`, `.gif`, `.txt`.
- MIME declarados permitidos: `application/pdf`, `image/png`, `image/jpeg`, `image/gif`, `text/plain`.
- El contenido se inspeccionará mediante firmas conocidas y validación UTF-8 para texto; el MIME detectado debe coincidir con el declarado y con la extensión.
- Archivos vacíos, scripts, SVG, ejecutables, discrepancias peligrosas, path traversal y nombres inválidos se rechazan.
- Límite por archivo, cantidad por ticket y bytes totales por ticket configurables mediante `ATTACHMENT_*`.
- Las claves de almacenamiento serán UUID internas bajo `attachments/`; nunca se derivarán del nombre enviado.
- `var/uploads/` queda ignorado por Git y no se monta como contenido estático.
- No se registran bytes, secretos ni rutas físicas en respuestas o logs.

## Persistencia

- Tabla `attachments` con `ticket_id` obligatorio, `comment_id` opcional, usuario, filename sanitizado, storage key interno, MIME declarado/detectado, tamaño, SHA-256, estado, fechas y `deleted_at`.
- Índices en `ticket_id + created_at`, `comment_id`, `uploaded_by_user_id` y `sha256`.
- Migración Alembic nueva dependiente de `0005_notifications`; no se agregan datos al seed porque los adjuntos son eventos de usuario y no datos de referencia.

## Plan TDD

1. Crear pruebas RED para subida, descarga, listado, borrado lógico, permisos por cliente/asesor/supervisor, 401/403/404/413/415/422, path traversal, nombres peligrosos, MIME discrepante, archivo vacío, cuotas, hash, ausencia de rutas y regresión.
2. Implementar modelo, configuración, proveedor local seguro, schemas, repositorio y servicio; registrar el modelo y la migración.
3. Implementar los endpoints multipart y descarga streaming sin exponer paths; registrar el router v1.
4. Integrar `TicketHistory` para `ATTACHMENT_UPLOADED` y `ATTACHMENT_DELETED`, sin inventar una notificación porque la Fase 03 no define un tipo de adjunto.
5. Actualizar `.env.example`, `.gitignore`, README y OpenAPI generado.
6. Ejecutar pruebas GREEN, regresión completa, migraciones, lint, formato, health, docs, OpenAPI y revisiones de seguridad/Git.
