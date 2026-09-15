# Plan: poblar datos operativos locales

## Objetivo

Extender el seed del backend para que la base MySQL local tenga datos coherentes
para las vistas públicas, cliente, asesor y supervisor, sin cambiar contratos de
API, permisos, modelos ni frontend.

## Tareas

- [x] Corregir el seed base de roles, categorías y FAQs para que el contenido
      visible sea profesional y libre de términos prohibidos.
- [x] Mantener las tres cuentas de acceso, con contraseñas provenientes de
      variables de entorno y hash Argon2, actualizando de forma segura los
      registros conocidos sin exponer secretos.
- [x] Añadir carga idempotente de categorías, FAQs, tickets, conversaciones,
      mensajes, comentarios, asignaciones e historiales con claves
      deterministas y fechas coherentes.
- [x] Añadir una opción explícita para que los fixtures SQLite existentes puedan
      usar únicamente el seed base, manteniendo sus supuestos de aislamiento;
      el comando de seed local deberá cargar siempre el dataset operativo.
- [x] Añadir o actualizar pruebas unitarias que verifiquen idempotencia,
      cobertura de estados/prioridades/orígenes, trazabilidad y ausencia de
      términos prohibidos.
- [x] Actualizar README con el comando de carga local, cuentas disponibles y
      qué datos quedan disponibles para cada rol.
- [x] Ejecutar validaciones de Docker Compose, Alembic, API HTTP, permisos,
      pytest, Ruff y diff; revisar que no se haya tocado el frontend ni se
      rastreen secretos.
- [x] Solicitar revisión interna del cambio, corregir hallazgos y crear el
      commit final en `main` sin hacer push.

## Verificación

Se ejecutarán dos veces el comando `python -m app.seed.demo_data`, se
compararán conteos y claves deterministas, y se consultarán los endpoints
requeridos usando las cuentas de entorno local. No se ejecutarán migraciones de
modelo ni operaciones destructivas sobre el volumen MySQL.
