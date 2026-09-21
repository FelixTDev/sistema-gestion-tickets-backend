# Fase 09 — Reportes y exportaciones

## Objetivo

Ampliar el módulo `reportes` sin romper los cinco endpoints JSON existentes,
añadiendo reportes operativos exportables en CSV, con filtros autorizados,
límite de filas, protección contra CSV injection y auditoría de generación.

## Decisiones

- Mantener `/api/v1/reports/summary`, `/by-status`, `/by-category`,
  `/by-priority` y `/resolution-time` y su contrato JSON actual.
- Añadir `GET /api/v1/reports/{report_name}/export` para reportes nombrados.
- Soportar `format=csv`. `format=xlsx` se valida pero devuelve 422 porque el
  entorno actual no tiene una dependencia XLSX instalada; no se instalarán
  dependencias en esta fase.
- Mantener autorización exclusiva de `SUPERVISOR`, porque no existe contrato
  vigente para exportaciones de `ASESOR`.
- Usar fechas timezone-aware normalizadas a UTC, rango máximo configurable y
  máximo de filas configurable.
- Crear `report_export_audits` para registrar actor, reporte, formato, filtros
  redactados, cantidad, resultado y fecha UTC; no se almacenan archivos.
- Usar consultas SQL agregadas o limitadas; no cargar datos fuera del límite.

## Reportes exportables

- `summary`
- `by-status`
- `by-priority`
- `by-category`
- `by-source`
- `by-advisor`
- `created-tickets`
- `resolved-tickets`
- `first-response-time`
- `resolution-time`
- `sla-compliance`
- `conversations`
- `faq-utility`
- `operational-activity`

Los datos exportados omiten hashes, tokens, secretos, contraseñas y PII no
necesaria. Las celdas textuales se protegen contra fórmulas de hojas de cálculo.

## TDD y validación

1. Añadir pruebas RED de permisos, filtros, exportación CSV, headers, límites,
   CSV injection, auditoría y regresión JSON.
2. Implementar esquemas, repositorio, servicio, router y auditoría mínima.
3. Ejecutar pruebas focalizadas GREEN y refactorizar sin cambiar contratos JSON.
4. Ejecutar suite completa, Ruff, Alembic, Compose, health/docs/OpenAPI y
   `git diff --check`.

No se modificará el frontend ni se crearán commits o push.
