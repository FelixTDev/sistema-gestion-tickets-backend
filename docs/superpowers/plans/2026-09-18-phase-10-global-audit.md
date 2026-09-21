# Fase 10 — Auditoría global y trazabilidad transversal

## Diseño

- Crear el módulo `auditoria` con modelo `AuditLog`, repositorio, servicio,
  schemas y router.
- Mantener `ticket_history`, `sla_policy_history`, `user_profile_audits`,
  `faq_versions` y `report_export_audits`; la auditoría global replica eventos
  relevantes sin reemplazar esos historiales.
- Hacer `AuditService.record()` transaccional, append-only y con redacción
  determinista de claves sensibles, estructuras anidadas, binarios y valores
  largos.
- Persistir `event_type`, `action`, actor, rol, recurso, recurso objetivo,
  fecha UTC, resultado, error, before/after, metadata segura y deduplicación
  opcional.
- Exponer únicamente `GET /api/v1/audit` para `SUPERVISOR`, paginado y con
  filtros estables. No habrá endpoints de actualización o eliminación.
- No capturar IP/user-agent/request-id porque no existe un mecanismo transversal
  seguro en la aplicación actual; los campos quedarán preparados como opcionales.

## Integraciones

- Autenticación: login, logout, recuperación, cambio de contraseña,
  verificación y eventos de seguridad.
- Usuarios: perfil y preferencias.
- Tickets: creación, comentarios, estados, asignaciones, toma/liberación y
  concurrencia.
- Adjuntos, SLA, FAQs, feedback, exportaciones y notificaciones críticas.

## TDD

1. Pruebas RED para redacción, append-only, consulta y permisos.
2. Implementación del módulo y migración.
3. Integración mínima en servicios existentes sin sustituir historiales.
4. GREEN, refactor y regresión completa.

No se modificará el frontend, no se eliminarán datos históricos y no se harán
commits ni push.
