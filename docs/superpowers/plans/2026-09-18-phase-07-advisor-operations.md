# Fase 07 — Operación del asesor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Completar las bandejas y acciones operativas de asesores y supervisores con permisos derivados del JWT/base de datos, historial, notificaciones y control optimista de concurrencia.

**Architecture:** Mantener el flujo router → `TicketService`/servicio operativo → `TicketRepository` → modelos existentes. Las consultas operativas reutilizarán `TicketPage` y filtros SQL paginados. Las acciones de asignación, toma y liberación vivirán en el servicio de tickets para conservar una sola matriz de transiciones e integraciones con SLA, historial y notificaciones.

**Tech Stack:** FastAPI, Pydantic, SQLModel, SQLAlchemy, MySQL/SQLite de pruebas, Alembic y Pytest.

## Global Constraints

- No modificar el frontend, no hacer commit ni push y mantener un único commit final para las fases 01–12.
- No confiar en `user_id`, `role` ni `assignee_id` enviados por el cliente; la identidad y el rol provienen de `CurrentUser` y la base de datos.
- No romper los endpoints actuales; los cambios de visibilidad del asesor son una corrección de autorización y se documentan.
- Mantener UTC interno, paginación estable y respuestas sin secretos ni datos de autenticación.
- No implementar acciones masivas en esta fase por ausencia de un contrato transaccional seguro.

## Matriz operativa

- CLIENTE: consulta y comenta sus tickets; no asigna ni cambia estados.
- ASESOR: consulta tickets asignados a sí mismo y la cola no asignada; puede tomar un ticket no asignado, liberar uno propio y operar estados de tickets asignados.
- SUPERVISOR: consulta global, cola no asignada y asignaciones específicas; asigna/reasigna/libera y administra estados globalmente.
- Transiciones funcionales existentes se conservan. La mutación del asesor exige asignación activa, excepto `take`; cancelación queda exclusiva del supervisor.

## Secuencia TDD

1. Añadir pruebas RED para bandejas, autorización, asignación/reasignación, toma/liberación, estados, conflicto de versión e integraciones.
2. Implementar `version`/`updated_at`, filtros operativos y migración mínima.
3. Implementar asignación segura y acciones del asesor con historial/notificaciones/SLA.
4. Endurecer acceso de asesor a tickets, comentarios, historial, adjuntos y SLA; adaptar regresiones al contrato autorizado.
5. Actualizar README/OpenAPI generado, revisar seguridad inline y ejecutar validación completa.

## Concurrencia

Los tickets tendrán `version` inicial 1 y `updated_at` UTC. Las respuestas expondrán ambos campos. Las solicitudes nuevas aceptarán `expected_version` opcional para compatibilidad; cuando se envíe, el servicio devuelve HTTP 409 si no coincide. Cuando no se envíe, la operación usa bloqueo pesimista compatible con la sesión para evitar actualizaciones silenciosas en MySQL.

Las migraciones `0009_ticket_ops` y `0010_assignment_idx` añaden esos campos y los índices para actualización reciente y consulta de asignaciones activas.

## Operación masiva

No se implementa. El repositorio no tiene aún un contrato de resultados por ticket, límites, idempotencia ni semántica de rollback para acciones masivas. Se documenta como pendiente en README y riesgos.
