# Fase 06 — Perfil, preferencias y configuración de usuario

## Objetivo

Añadir consulta y actualización segura del perfil propio, preferencias persistentes
de notificación y auditoría redactada, sin modificar el contrato existente de
autenticación ni permitir cambios sobre campos internos.

## Hallazgos y decisiones

- `User` ya contiene `full_name`, `phone`, `email`, `email_verified`, rol y
  `sessions_invalidated_at`; no se duplicarán esas propiedades.
- No existe un flujo de cambio de correo con correo pendiente, token de un solo
  uso, proveedor real y activación posterior a la verificación. Por seguridad,
  `email` no será aceptado por el PATCH de perfil en esta fase. El correo
  continuará gestionándose mediante el registro/verificación existente hasta una
  fase posterior que implemente el flujo completo.
- Las preferencias vivirán en una tabla uno-a-uno `user_preferences`, extensible
  y separada de `users`. Los valores por defecto seguros se aplican también si
  una cuenta antigua todavía no tiene fila.
- Los cambios se registrarán en `user_profile_audits` con actor, usuario afectado,
  campo y valores redactados. No se almacenarán contraseñas, tokens, secretos ni
  valores completos de teléfono.
- Los endpoints serán autoalcanzables: `GET/PATCH /api/v1/users/me/profile` y
  `GET/PATCH /api/v1/users/me/preferences`. No se crea un contrato administrativo
  para perfiles ajenos.
- `security_event` y `password_changed` siempre se entregan, aunque el usuario
  desactive notificaciones ordinarias. Las demás notificaciones respetan la
  preferencia in-app/email correspondiente; si no hay fila se asumen habilitadas.
- Las fechas de auditoría se guardan en UTC. La zona horaria de preferencias se
  valida con `zoneinfo` y se conserva como identificador IANA para presentación.

## Secuencia TDD

1. Crear pruebas de contrato, permisos, validación, defaults, auditoría y filtro
   de notificaciones; confirmar RED.
2. Añadir modelos, migración, esquemas, repositorios, servicio y routers mínimos.
3. Integrar preferencias en `NotificationService` conservando defaults y la
   idempotencia existente; confirmar GREEN y regresión.
4. Actualizar seed, README y OpenAPI generado; ejecutar validaciones completas y
   revisión inline de seguridad, datos y arquitectura.

## Migración

Se requiere una migración Alembic nueva para `user_preferences` y
`user_profile_audits`, con unicidad por usuario y claves foráneas a `users`.
Los datos existentes no se modifican; las cuentas antiguas reciben defaults
seguros de forma perezosa al consultar o editar sus preferencias.

