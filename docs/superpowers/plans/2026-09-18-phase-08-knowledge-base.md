# Fase 08 — Base de conocimiento avanzada Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use executing-plans to implement this plan task-by-task. No se permite crear commits intermedios en esta evolución.

**Goal:** Evolucionar la entidad `FAQ` existente a conocimiento editorial versionable, buscable y seguro, manteniendo los contratos públicos actuales y alimentando al chatbot solo con contenido publicado y vigente.

**Architecture:** Se mantiene `app/modules/conocimiento` y se amplía `FAQ`; no se crea una entidad paralela de artículos. `FAQVersion` conserva snapshots editoriales y `FAQFeedback` conserva utilidad agregada sin PII innecesaria. Los routers solo validan HTTP, el servicio aplica ciclo editorial y permisos, y el repositorio realiza búsqueda/paginación SQL.

**Tech Stack:** FastAPI, Pydantic, SQLModel, MySQL/SQLite de pruebas, Alembic, Pytest y Ruff.

## Global Constraints

- No modificar frontend, no hacer commit ni push y mantener un único commit final para las fases 01–12.
- Mantener compatibles `GET /api/v1/faqs`, `GET /api/v1/faqs/{faq_id}`, POST/PATCH administrativos existentes y la lectura del chatbot.
- Solo `SUPERVISOR` administra conocimiento; el público y `CLIENTE` solo ven contenido publicado, activo, vigente y de categorías activas.
- No introducir RAG, LLM, motor externo de búsqueda, HTML ejecutable ni datos ficticios de runtime.
- Persistir timestamps en UTC, usar respuestas tipadas, `Annotated` y separación router → service → repository → model.
- La búsqueda usará `LIKE`/`ILIKE` sobre contenido normalizado y documentará la futura migración a FULLTEXT.

## Matriz actual versus nueva

| Capacidad | Estado actual | Resultado de Fase 08 |
| --- | --- | --- |
| FAQ pública | Lista/detalle por `is_active` | Publicación vigente, categoría activa, búsqueda y paginación compatibles |
| Administración | Crear, editar y activar/desactivar | Ciclo DRAFT/REVIEW/PUBLISHED/ARCHIVED y transiciones válidas |
| Contenido | pregunta, respuesta, keywords | título, resumen, tags, sinónimos, prioridad, orden, intención y contenido normalizado |
| Versionado | inexistente | snapshots con versión, autor/editor, acción y fecha |
| Búsqueda | chatbot por keywords | búsqueda pública/admin por contenido, tags, sinónimos y categoría con orden determinista |
| Feedback | inexistente | utilidad anónima o de cliente y métricas agregadas de supervisor |
| Chatbot | FAQs activas sin filtrar categoría | solo PUBLISHED, activas, vigentes y categoría activa; ranking ampliado |

## Secuencia TDD

### Task 1: Contratos, modelos y pruebas RED

**Files:**
- Create: `tests/integration/test_knowledge_base.py`
- Modify: `app/modules/conocimiento/models/faq.py`, `app/modules/conocimiento/schemas/faq.py`
- Create: `app/modules/conocimiento/models/version.py`, `app/modules/conocimiento/models/feedback.py`

- [ ] Escribir pruebas para ciclo editorial, permisos, publicación pública, búsqueda, paginación, versiones, feedback y métricas.
- [ ] Ejecutar `\.venv\Scripts\python.exe -m pytest -q tests/integration/test_knowledge_base.py` y confirmar fallos por rutas/campos inexistentes.

### Task 2: Migración y persistencia

**Files:**
- Create: `migrations/versions/0011_knowledge_base.py`, `migrations/versions/0012_knowledge_backfill.py`
- Modify: `app/db/models.py`
- Modify: `app/modules/conocimiento/repositories/faq_repository.py`

- [ ] Añadir columnas editoriales a `faqs`, crear tablas `faq_versions`/`faq_feedback` y backfill seguro de registros existentes como `PUBLISHED`/`ARCHIVED` según `is_active`.
- [ ] Crear snapshots iniciales y contenido normalizado de los FAQs existentes mediante la migración de datos 0012, sin depender del seed.
- [ ] Añadir índices por estado/categoría/publicación/actualización/versiones/feedback.
- [ ] Implementar consultas parametrizadas, filtros de categoría activa, vigencia, tags, estado y fechas.

### Task 3: Servicio editorial mínimo GREEN

**Files:**
- Modify: `app/modules/conocimiento/services/faq_service.py`
- Modify: `app/modules/conocimiento/schemas/faq.py`

- [ ] Implementar normalización, sanitización de texto plano, validación de duplicados, categorías activas, tags/sinónimos y contenido publicable.
- [ ] Implementar transiciones `DRAFT → REVIEW → PUBLISHED`, `PUBLISHED → REVIEW/ARCHIVED` y `ARCHIVED → DRAFT`.
- [ ] Crear snapshots versionados en cada mutación editorial sin eliminar versiones anteriores.
- [ ] Implementar feedback y métricas agregadas sin exponer usuario/comentario.
- [ ] Ejecutar pruebas GREEN focalizadas y corregir solo producción, no las expectativas válidas.

### Task 4: Router y compatibilidad HTTP

**Files:**
- Modify: `app/modules/conocimiento/api/router.py`
- Modify: `README.md`

- [ ] Mantener respuestas históricas de `GET /faqs` sin paginación cuando no se envían parámetros.
- [ ] Añadir búsqueda/filtros/paginación pública y `GET /faqs/admin` solo supervisor.
- [ ] Mantener `PATCH /faqs/{id}/status` y añadir transición explícita, historial, feedback y métricas únicamente donde el contrato lo requiere.
- [ ] Verificar 401/403/404/409/422 y OpenAPI.

### Task 5: Chatbot y seed

**Files:**
- Modify: `app/modules/chatbot/services/chatbot_service.py`
- Modify: `app/seed/demo_data.py`
- Modify: `tests/integration/test_chatbot.py`, `tests/unit/test_seed.py`

- [ ] Consumir exclusivamente FAQs publicadas, activas, vigentes y con categoría activa.
- [ ] Incluir tags, sinónimos, intención y campos de conocimiento en ranking determinista.
- [ ] Mantener fallback seguro y contratos de conversación/ticket actuales.
- [ ] Hacer el seed idempotente y crear snapshots iniciales sin duplicarlos.

### Task 6: Regresión, seguridad y documentación

- [ ] Ejecutar pruebas RED/GREEN, regresión completa y revisión inline de permisos, XSS, contenido privado y datos sensibles.
- [ ] Ejecutar `pytest -q`, `ruff check .`, `ruff format --check .`, `alembic upgrade head`, `alembic check`, `docker compose config --quiet`, health/docs/OpenAPI y `git diff --check`.
- [ ] Confirmar que no hay cambios frontend, commit ni push.

## Decisiones y límites

- Se reutiliza `FAQ` porque chatbot y API pública ya dependen de ella; una tabla `Article` duplicaría contratos y contenido.
- `tags` y `synonyms` se almacenan como JSON serializado validado dentro del módulo para no introducir tablas de relaciones sin necesidad; la búsqueda usa el contenido normalizado.
- `LIKE`/`ILIKE` es suficiente para el volumen local actual; se deja documentada la evolución a FULLTEXT cuando el volumen lo justifique.
- No se incorporan FAQs relacionadas, HTML enriquecido ni RAG porque no existen contratos necesarios y aumentarían superficie de seguridad/acoplamiento.
- No se guardan IP, user-agent ni datos personales en feedback; solo `user_id` opcional para distinguir feedback autenticado en agregados.
