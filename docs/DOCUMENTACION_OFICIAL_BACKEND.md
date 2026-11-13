# Documentación oficial del backend

> Ticket Management API - estado real del código fuente auditado

**Fecha de auditoría:** 2026-09-22<br>
**Versión del proyecto:** 0.1.0
**Alcance:** backend de prototipo académico independiente; no se conecta a sistemas del Banco GNB Perú ni procesa operaciones bancarias reales.

## Índice

1. [Introducción y alcance](#1-introducción-y-alcance)
2. [Descripción general](#2-descripción-general)
3. [Tecnologías utilizadas](#3-tecnologías-utilizadas)
4. [Arquitectura](#4-arquitectura)
5. [Organización del código fuente](#5-organización-del-código-fuente)
6. [Módulos y funcionalidades](#6-módulos-y-funcionalidades)
7. [API REST](#7-api-rest)
8. [Seguridad, autenticación y permisos](#8-seguridad-autenticación-y-permisos)
9. [Persistencia y modelo de datos](#9-persistencia-y-modelo-de-datos)
10. [Reglas de negocio y validaciones](#10-reglas-de-negocio-y-validaciones)
11. [Integraciones externas](#11-integraciones-externas)
12. [DTOs, errores y paginación](#12-dtos-errores-y-paginación)
13. [Configuración y ejecución](#13-configuración-y-ejecución)
14. [Pruebas e infraestructura](#14-pruebas-e-infraestructura)
15. [Pendientes y observaciones](#15-pendientes-y-observaciones)
16. [Matriz de trazabilidad y glosario](#16-matriz-de-trazabilidad-y-glosario)

## 1. Introducción y alcance

Este documento describe exclusivamente los componentes encontrados en `app/`, `migrations/`, `tests/`, `pyproject.toml`, `Dockerfile`, `docker-compose.yml`, `.env.example` y `README.md`. No se documentan endpoints, tablas, servicios externos ni reglas que no tengan respaldo en esos archivos.

El backend es un monolito modular FastAPI con persistencia MySQL. Administra usuarios, autenticación, tickets, conocimiento FAQ, conversaciones de chatbot, SLA, adjuntos, notificaciones, reportes y auditoría. Se trata de un prototipo académico y el README declara expresamente que no es un sistema oficial bancario.

## 2. Descripción general

### Qué hace actualmente

- Expone una API REST versionada bajo `/api/v1`.
- Registra clientes y emite JWT de acceso; crea sesiones revocables y tokens hash para recuperación/verificación.
- Permite a clientes crear y consultar tickets, conversar con un chatbot FAQ y convertir conversaciones no resueltas en tickets.
- Permite a asesores operar tickets asignados y a supervisores administrar asignaciones, conocimiento, SLA, reportes y auditoría.
- Persiste historiales operativos, notificaciones, versiones editoriales y auditoría global redactada.
- Almacena adjuntos en el directorio local configurado, no en MySQL ni como archivos estáticos.

### Estado actual

El inventario contiene 8 módulos de dominio, 73 rutas HTTP incluyendo health, 24 entidades SQLModel, 12 repositorios y 14 archivos de servicio. La IA generativa y el envío real de correo/notificaciones están desacoplados mediante puertos, pero los adaptadores locales por defecto son `Noop`.

## 3. Tecnologías utilizadas

| Tecnología | Versión declarada | Función |
| --- | --- | --- |
| Python | >=3.11 | Lenguaje de aplicación |
| FastAPI | >=0.115,<1.0 | API REST, validación y OpenAPI en desarrollo |
| Pydantic / pydantic-settings | Pydantic >=2.6 indirecto; pydantic-settings >=2.6,<3.0 | Schemas, configuración y validaciones |
| SQLModel | >=0.0.22,<1.0 | Modelos persistentes y sesiones SQLAlchemy |
| MySQL | 8.4 en Docker Compose | Base de datos relacional |
| PyMySQL | >=1.1,<2.0 | Driver MySQL |
| Alembic | >=1.13,<2.0 | Migraciones versionadas |
| PyJWT | >=2.9,<3.0 | JWT HS256 |
| pwdlib[argon2] | >=0.2,<1.0 | Hash de contraseñas Argon2 recomendado |
| pytest / pytest-cov / httpx | dev | Pruebas unitarias e integración con TestClient |
| ruff | >=0.8,<1.0 | Lint y formato |
| Docker | python:3.12-slim; mysql:8.4 | Ejecución reproducible local |

## 4. Arquitectura

La implementación sigue una separación por módulo y responsabilidad: router -> service -> repository -> model. Los routers declaran contratos HTTP y dependencias FastAPI; los servicios aplican permisos y reglas de negocio; los repositorios encapsulan consultas SQLModel; los modelos son tablas. `app/shared` contiene utilidades transversales de paginación, fechas, texto y tipos.

```mermaid
flowchart LR
  C[Cliente o frontend] --> API[FastAPI /api/v1]
  API --> SEC[HTTPBearer + JWT + roles]
  SEC --> MOD[Módulos de dominio]
  MOD --> SVC[Services]
  SVC --> REP[Repositories / SQLModel]
  REP --> DB[(MySQL)]
  SVC --> FS[(var/uploads local)]
  SVC -. opcional .-> AI[Proveedor IA: OpenAI o http_json]
```

La configuración global incluye `TrustedHostMiddleware`, `CORSMiddleware` sin cookies, dos headers de seguridad HTTP y documentación pública únicamente cuando `APP_ENV=development`. La sesión SQLModel usa `pool_pre_ping=True`; Alembic toma la URL desde settings y apunta a `SQLModel.metadata`.

## 5. Organización del código fuente

```text
app/
├── api/deps.py                         # autenticación, usuario actual y roles
├── api/v1/router.py                    # prefijo /api/v1 e inclusión de routers
├── core/                               # configuración y logging
├── db/                                 # engine, sesión y registro de modelos
├── modules/                            # módulos por dominio
│   ├── usuarios/{api,models,repositories,schemas,services}
│   ├── tickets/{api,models,repositories,schemas,services}
│   ├── conocimiento/{api,models,repositories,schemas,services}
│   ├── chatbot/{api,models,repositories,schemas,services}
│   ├── adjuntos/{api,models,repositories,schemas,services}
│   ├── notificaciones/{api,models,repositories,schemas,services}
│   ├── reportes/{api,models,repositories,schemas,services}
│   └── auditoria/{api,models,repositories,schemas,services}
├── seed/demo_data.py                    # datos locales idempotentes
└── shared/                              # utilidades transversales
migrations/versions/                    # 17 revisiones Alembic
tests/{unit,integration,evaluation}/
```

| Módulo | Responsabilidad | Componentes principales |
| --- | --- | --- |
| usuarios | Identidad, JWT, sesiones, tokens de un solo uso, perfiles, preferencias y rate limiting. | auth_router.py, user_router.py; AuthService, UserProfileService, UserService, AuthRateLimiter |
| tickets | Creación, consulta, asignación, comentarios, estados, operaciones e historial. | TicketService, TicketRepository; modelos Ticket, Assignment, Comment, History |
| conocimiento | Categorías y FAQs públicas/editoriales, feedback, versiones y métricas. | FAQService, CategoryService; FAQRepository, CategoryRepository |
| chatbot | Conversaciones anónimas o autenticadas, matching FAQ, guardrails, feedback, escalamiento y conversión. | ChatbotService, RAGService; ConversationRepository |
| adjuntos | Carga, validación, almacenamiento local, descarga y eliminación lógica de archivos. | AttachmentService, LocalStorageProvider; AttachmentRepository |
| notificaciones | Persistencia in-app, preferencias, deduplicación y puerto de entrega desacoplado. | NotificationService, NoopNotificationProvider |
| reportes | Reportes operativos y exportaciones CSV auditadas. | ReportService, ReportRepository; ReportExportAudit |
| auditoria | Auditoría global append-only, redacción y consulta supervisada. | AuditService, AuditRepository; AuditLog |

## 6. Módulos y funcionalidades

### Usuarios, autenticación y perfil

Registro, login, logout, `GET /auth/me`, recuperación y cambio de contraseña, verificación de correo, perfiles propios, preferencias y directorio de asesores. El registro fuerza rol `CLIENTE`; la administración de roles no tiene endpoint HTTP. Los perfiles no exponen `password_hash`, sesiones ni secretos.

### Tickets y operación interna

Los tickets se crean manualmente o desde conversaciones; llevan `tracking_code` único, estado, prioridad, origen, asignación, versión optimista, timestamps y `ticket_history`. El asesor solo opera tickets asignados; el supervisor administra asignaciones, colas y cancelaciones.

Estados: `NUEVO`, `ASIGNADO`, `EN_PROCESO`, `PENDIENTE_CLIENTE`, `RESUELTO`, `CERRADO`, `CANCELADO`. Transiciones codificadas: NUEVO -> ASIGNADO/CANCELADO; ASIGNADO -> EN_PROCESO/CANCELADO; EN_PROCESO -> PENDIENTE_CLIENTE/RESUELTO/CANCELADO; PENDIENTE_CLIENTE -> EN_PROCESO; RESUELTO -> CERRADO/EN_PROCESO/CANCELADO. CERRADO y CANCELADO son terminales.

### Conocimiento y chatbot

Las FAQs se normalizan sin acentos y con espacios colapsados. El matching determinista combina pregunta, título, resumen, respuesta, keywords, tags, sinónimos y categoría. Alta confianza responde con la FAQ; confianza media ofrece hasta tres opciones; baja confianza ofrece atención humana y mantiene el fallback. Solo FAQs publicadas, activas, vigentes y de categorías activas llegan al contexto RAG.

Las conversaciones admiten estados `ACTIVE`, `WAITING_CLARIFICATION`, `ESCALATED`, `CONVERTED_TO_TICKET`, `CLOSED` y `EXPIRED`. Visitantes y clientes pueden usar el chatbot; asesores y supervisores son rechazados. El contenido no válido, terminal o fuera de cuota se rechaza.

### SLA

Cada ticket intenta iniciar un SLA según prioridad, categoría y origen. Se registran primera respuesta, vencimientos, pausas, advertencias, breaches, cumplimiento y reaperturas. Solo comentarios de asesor o supervisor cuentan como primera respuesta. La evaluación se ejecuta bajo demanda por un supervisor: no existe scheduler en el código.

### Adjuntos, notificaciones, reportes y auditoría

Los adjuntos se validan por nombre, extensión, MIME declarado, firma detectada, tamaño, cuota, conteo y hash SHA-256. Las notificaciones son in-app y usan deduplicación; el canal externo es un `NoopNotificationProvider`. Los reportes son solo para supervisores y exportan CSV seguro; XLSX devuelve 422. La auditoría global es append-only y no tiene mutaciones HTTP.

## 7. API REST

Todas las rutas se registraron desde decoradores FastAPI del código: **73 endpoints**. La documentación interactiva `/docs`, `/redoc` y `/openapi.json` solo se habilita en desarrollo.

### Índice rápido por método

| Método | Cantidad | Familias |
| --- | --- | --- |
| GET | 31 | attachments, audit, auth, categories, chat, faqs, health, notifications, reports, sla, tickets, users |
| POST | 32 | auth, categories, chat, faqs, notifications, sla, tickets |
| PATCH | 9 | categories, faqs, notifications, sla, users |
| DELETE | 1 | attachments |

### Resumen completo

| Método | Endpoint | Descripción | Autenticación | Rol |
| --- | --- | --- | --- | --- |
| DELETE | /api/v1/attachments/{attachment_id} | Marca un adjunto como DELETED, registra historial y elimina el objeto local. | Bearer JWT | CLIENTE/ASESOR/SUPERVISOR con acceso al ticket |
| GET | /api/v1/attachments/{attachment_id}/download | Descarga el binario con Content-Disposition attachment, no-store y MIME detectado. | Bearer JWT | CLIENTE/ASESOR/SUPERVISOR con acceso al ticket |
| GET | /api/v1/audit | Consulta auditoría global append-only con paginación y filtros. | Bearer JWT | SUPERVISOR |
| POST | /api/v1/auth/change-password | Cambia la contraseña con la contraseña actual y revoca sesiones anteriores. | Bearer JWT | CLIENTE, ASESOR o SUPERVISOR |
| POST | /api/v1/auth/forgot-password | Solicita recuperación sin revelar si el correo existe. | No | - |
| POST | /api/v1/auth/login | Valida credenciales y emite un access token JWT vinculado a una sesión persistente. | No | - |
| POST | /api/v1/auth/logout | Revoca la sesión asociada al JWT actual. | Bearer JWT | CLIENTE, ASESOR o SUPERVISOR |
| GET | /api/v1/auth/me | Devuelve la identidad pública del usuario autenticado. | Bearer JWT | CLIENTE, ASESOR o SUPERVISOR |
| POST | /api/v1/auth/register | Registra una cuenta nueva y siempre asigna el rol CLIENTE. | No | - |
| POST | /api/v1/auth/reset-password | Consume un token de recuperación de un solo uso y revoca sesiones previas. | No | - |
| POST | /api/v1/auth/verify-email | Consume un token de verificación de correo de un solo uso. | No | - |
| GET | /api/v1/categories | Lista categorías activas. | No | - |
| POST | /api/v1/categories | Crea una categoría de tickets. | Bearer JWT | SUPERVISOR |
| PATCH | /api/v1/categories/{category_id} | Actualiza nombre o descripción de una categoría. | Bearer JWT | SUPERVISOR |
| PATCH | /api/v1/categories/{category_id}/status | Activa o desactiva una categoría; las categorías usadas por tickets no pueden desactivarse. | Bearer JWT | SUPERVISOR |
| POST | /api/v1/chat/conversations | Crea una conversación anónima o asociada al cliente autenticado. | Opcional | Visitante o CLIENTE |
| GET | /api/v1/chat/conversations/{conversation_id} | Devuelve conversación y mensajes si el visitante o cliente está autorizado. | Opcional | Visitante de anónima o CLIENTE propietario |
| POST | /api/v1/chat/conversations/{conversation_id}/convert-to-ticket | Convierte una conversación autenticada no resuelta en un ticket único. | Bearer JWT | CLIENTE |
| POST | /api/v1/chat/conversations/{conversation_id}/escalate | Deriva una conversación a atención humana de forma idempotente. | Opcional | Visitante o CLIENTE |
| POST | /api/v1/chat/conversations/{conversation_id}/feedback | Registra utilidad y aceptación de escalamiento sobre la última FAQ usada. | Opcional | Visitante o CLIENTE |
| POST | /api/v1/chat/conversations/{conversation_id}/link-user | Asocia una conversación anónima con el cliente autenticado. | Bearer JWT | CLIENTE |
| POST | /api/v1/chat/conversations/{conversation_id}/messages | Procesa un mensaje mediante recuperación FAQ determinista y fallback/IA opcional. | Opcional | Visitante o CLIENTE |
| POST | /api/v1/chat/conversations/{conversation_id}/reset | Reinicia el contexto operativo sin borrar mensajes históricos. | Opcional | Visitante o CLIENTE |
| GET | /api/v1/faqs | Lista FAQs publicadas, activas, vigentes y de categorías activas; soporta filtros y paginación opcional. | No | - |
| POST | /api/v1/faqs | Crea contenido de conocimiento como DRAFT inactivo y registra su primera versión. | Bearer JWT | SUPERVISOR |
| GET | /api/v1/faqs/admin | Lista el catálogo editorial con filtros de estado, contenido y fechas. | Bearer JWT | SUPERVISOR |
| GET | /api/v1/faqs/admin/metrics/utility | Devuelve métricas agregadas de utilidad del feedback de FAQs. | Bearer JWT | SUPERVISOR |
| GET | /api/v1/faqs/{faq_id} | Obtiene una FAQ pública activa. | No | - |
| PATCH | /api/v1/faqs/{faq_id} | Actualiza una FAQ; si una FAQ publicada cambia contenido vuelve a REVIEW e incrementa versión. | Bearer JWT | SUPERVISOR |
| POST | /api/v1/faqs/{faq_id}/feedback | Registra feedback anónimo o de un cliente sobre una FAQ activa. | Opcional | Visitante o CLIENTE |
| GET | /api/v1/faqs/{faq_id}/history | Consulta snapshots editoriales de una FAQ. | Bearer JWT | SUPERVISOR |
| PATCH | /api/v1/faqs/{faq_id}/status | Atajo para publicar o archivar una FAQ mediante is_active. | Bearer JWT | SUPERVISOR |
| PATCH | /api/v1/faqs/{faq_id}/workflow | Aplica transiciones editoriales DRAFT, REVIEW, PUBLISHED o ARCHIVED. | Bearer JWT | SUPERVISOR |
| GET | /api/v1/health | Comprobación estática de disponibilidad de la API. | No | - |
| GET | /api/v1/notifications | Lista notificaciones propias paginadas, ordenadas por created_at e id descendentes. | Bearer JWT | CLIENTE, ASESOR o SUPERVISOR |
| POST | /api/v1/notifications/read-all | Marca como leídas todas las notificaciones propias y devuelve el contador actualizado. | Bearer JWT | CLIENTE, ASESOR o SUPERVISOR |
| GET | /api/v1/notifications/unread-count | Cuenta notificaciones no leídas del usuario actual. | Bearer JWT | CLIENTE, ASESOR o SUPERVISOR |
| PATCH | /api/v1/notifications/{notification_id}/read | Marca como leída una notificación propia. | Bearer JWT | CLIENTE, ASESOR o SUPERVISOR |
| GET | /api/v1/reports/by-category | Agrupa tickets por categoría. | Bearer JWT | SUPERVISOR |
| GET | /api/v1/reports/by-priority | Agrupa tickets por prioridad. | Bearer JWT | SUPERVISOR |
| GET | /api/v1/reports/by-status | Agrupa tickets por estado. | Bearer JWT | SUPERVISOR |
| GET | /api/v1/reports/resolution-time | Devuelve cantidad resuelta y promedio de tiempo de resolución. | Bearer JWT | SUPERVISOR |
| GET | /api/v1/reports/summary | Resumen de tickets por estado y promedio de resolución. | Bearer JWT | SUPERVISOR |
| GET | /api/v1/reports/{report_name}/export | Genera exportación CSV de un reporte permitido y audita el intento. | Bearer JWT | SUPERVISOR |
| POST | /api/v1/sla/evaluate | Evalúa bajo demanda advertencias y vencimientos de SLA idempotentemente. | Bearer JWT | SUPERVISOR |
| GET | /api/v1/sla/policies | Lista políticas SLA persistentes. | Bearer JWT | SUPERVISOR |
| POST | /api/v1/sla/policies | Crea política SLA por prioridad, categoría y origen. | Bearer JWT | SUPERVISOR |
| PATCH | /api/v1/sla/policies/{policy_id} | Actualiza una política SLA y conserva historial de valores anteriores/nuevos. | Bearer JWT | SUPERVISOR |
| GET | /api/v1/tickets | Lista tickets para personal interno; los asesores quedan limitados a tickets asignados. | Bearer JWT | ASESOR o SUPERVISOR |
| POST | /api/v1/tickets | Crea ticket manual con código TCK, prioridad y estado NUEVO; inicia SLA e historial. | Bearer JWT | CLIENTE |
| GET | /api/v1/tickets/mine | Lista únicamente los tickets del cliente actual; modo legacy sin paginación o modo paginado. | Bearer JWT | CLIENTE |
| GET | /api/v1/tickets/operations | Consulta bandejas operativas paginadas por cola, SLA, asignación o actividad reciente. | Bearer JWT | ASESOR o SUPERVISOR |
| GET | /api/v1/tickets/{ticket_id} | Obtiene un ticket si el cliente es propietario, el asesor está asignado o el actor es supervisor. | Bearer JWT | CLIENTE, ASESOR o SUPERVISOR |
| POST | /api/v1/tickets/{ticket_id}/assignments | Asigna o reasigna un ticket a un asesor activo. | Bearer JWT | SUPERVISOR |
| GET | /api/v1/tickets/{ticket_id}/attachments | Lista metadatos de adjuntos activos del ticket. | Bearer JWT | CLIENTE/ASESOR/SUPERVISOR con acceso al ticket |
| POST | /api/v1/tickets/{ticket_id}/attachments | Carga un archivo multipart asociado a un ticket y opcionalmente a un comentario. | Bearer JWT | CLIENTE/ASESOR/SUPERVISOR con acceso al ticket |
| POST | /api/v1/tickets/{ticket_id}/cancel | Cancela un ticket con motivo; la operación está reservada al supervisor. | Bearer JWT | SUPERVISOR |
| POST | /api/v1/tickets/{ticket_id}/close | Cierra un ticket mediante una transición válida. | Bearer JWT | ASESOR o SUPERVISOR |
| GET | /api/v1/tickets/{ticket_id}/comments | Lista comentarios autorizados en orden de creación. | Bearer JWT | CLIENTE, ASESOR o SUPERVISOR |
| POST | /api/v1/tickets/{ticket_id}/comments | Agrega comentario, historial, primera respuesta SLA y notificación al interlocutor. | Bearer JWT | Propietario CLIENTE, asesor asignado o SUPERVISOR |
| GET | /api/v1/tickets/{ticket_id}/history | Devuelve el historial de mutaciones del ticket. | Bearer JWT | CLIENTE/ASESOR/SUPERVISOR con autorización sobre el ticket |
| POST | /api/v1/tickets/{ticket_id}/release | Libera asignación propia del asesor o una asignación que gestiona un supervisor. | Bearer JWT | ASESOR o SUPERVISOR |
| POST | /api/v1/tickets/{ticket_id}/reopen | Reabre un ticket RESUELTO hacia EN_PROCESO y exige motivo. | Bearer JWT | ASESOR con asignación o SUPERVISOR |
| GET | /api/v1/tickets/{ticket_id}/sla | Consulta el registro SLA y la política aplicada al ticket autorizado. | Bearer JWT | CLIENTE/ASESOR/SUPERVISOR con autorización sobre el ticket |
| POST | /api/v1/tickets/{ticket_id}/sla/pause | Pausa manualmente un SLA activo con motivo e historial. | Bearer JWT | ASESOR o SUPERVISOR con acceso al ticket |
| POST | /api/v1/tickets/{ticket_id}/sla/resume | Reanuda SLA pausado y extiende vencimientos por el tiempo pausado. | Bearer JWT | ASESOR o SUPERVISOR con acceso al ticket |
| POST | /api/v1/tickets/{ticket_id}/status | Cambia el estado con la matriz de transiciones válida y registra historial/notificación. | Bearer JWT | ASESOR o SUPERVISOR |
| POST | /api/v1/tickets/{ticket_id}/take | Permite a un asesor tomar un ticket abierto sin asignación. | Bearer JWT | ASESOR |
| GET | /api/v1/users/advisors | Lista asesores activos para selección operativa. | Bearer JWT | SUPERVISOR |
| GET | /api/v1/users/me/preferences | Obtiene o crea preferencias persistentes del usuario. | Bearer JWT | CLIENTE, ASESOR o SUPERVISOR |
| PATCH | /api/v1/users/me/preferences | Actualiza preferencias de notificación, idioma y zona horaria. | Bearer JWT | CLIENTE, ASESOR o SUPERVISOR |
| GET | /api/v1/users/me/profile | Consulta el perfil público ampliado del usuario actual. | Bearer JWT | CLIENTE, ASESOR o SUPERVISOR |
| PATCH | /api/v1/users/me/profile | Actualiza únicamente full_name y phone del propio usuario. | Bearer JWT | CLIENTE, ASESOR o SUPERVISOR |

### Documentación individual

### DELETE /api/v1/attachments/{attachment_id}

**Descripción.** Marca un adjunto como DELETED, registra historial y elimina el objeto local.

- Autenticación: Bearer JWT
- Rol o alcance: CLIENTE/ASESOR/SUPERVISOR con acceso al ticket
- Entrada: Path: attachment_id
- Respuesta: HTTP 200 con `AttachmentRead`
- Errores observables: 401; 403; 404
- Implementación: `delete_attachment` en `app/modules/adjuntos/api/router.py:94`


### GET /api/v1/attachments/{attachment_id}/download

**Descripción.** Descarga el binario con Content-Disposition attachment, no-store y MIME detectado.

- Autenticación: Bearer JWT
- Rol o alcance: CLIENTE/ASESOR/SUPERVISOR con acceso al ticket
- Entrada: Path: attachment_id
- Respuesta: HTTP 200 con `StreamingResponse binaria`
- Errores observables: 401; 403; 404 adjunto o archivo físico
- Implementación: `download_attachment` en `app/modules/adjuntos/api/router.py:72`


### GET /api/v1/audit

**Descripción.** Consulta auditoría global append-only con paginación y filtros.

- Autenticación: Bearer JWT
- Rol o alcance: SUPERVISOR
- Entrada: Query: page=1, page_size=20, actor_user_id?, resource_type?, resource_id?, event_type?, action?, target_user_id?, from?, to?, success?, search?
- Respuesta: HTTP 200 con `AuditPage`
- Errores observables: 401/403; 422 rango o paginación
- Implementación: `list_audit` en `app/modules/auditoria/api/router.py:34`


### POST /api/v1/auth/change-password

**Descripción.** Cambia la contraseña con la contraseña actual y revoca sesiones anteriores.

- Autenticación: Bearer JWT
- Rol o alcance: CLIENTE, ASESOR o SUPERVISOR
- Entrada: ChangePasswordRequest: current_password, new_password
- Respuesta: HTTP 200 con `MessageResponse`
- Errores observables: 400 contraseña actual inválida; 401 autenticación; 422 validación
- Implementación: `change_password` en `app/modules/usuarios/api/auth_router.py:178`


### POST /api/v1/auth/forgot-password

**Descripción.** Solicita recuperación sin revelar si el correo existe.

- Autenticación: No
- Rol o alcance: -
- Entrada: ForgotPasswordRequest: email
- Respuesta: HTTP status.HTTP_202_ACCEPTED con `MessageResponse (202)`
- Errores observables: 202 respuesta uniforme; 422 validación
- Implementación: `forgot_password` en `app/modules/usuarios/api/auth_router.py:144`


### POST /api/v1/auth/login

**Descripción.** Valida credenciales y emite un access token JWT vinculado a una sesión persistente.

- Autenticación: No
- Rol o alcance: -
- Entrada: LoginRequest: email, password
- Respuesta: HTTP 200 con `LoginResponse`
- Errores observables: 401 credenciales inválidas o rate limit; 422 validación
- Implementación: `login` en `app/modules/usuarios/api/auth_router.py:88`


### POST /api/v1/auth/logout

**Descripción.** Revoca la sesión asociada al JWT actual.

- Autenticación: Bearer JWT
- Rol o alcance: CLIENTE, ASESOR o SUPERVISOR
- Entrada: Sin body
- Respuesta: HTTP 200 con `LogoutResponse`
- Errores observables: 401 token inválido
- Implementación: `logout` en `app/modules/usuarios/api/auth_router.py:130`


### GET /api/v1/auth/me

**Descripción.** Devuelve la identidad pública del usuario autenticado.

- Autenticación: Bearer JWT
- Rol o alcance: CLIENTE, ASESOR o SUPERVISOR
- Entrada: Sin body
- Respuesta: HTTP 200 con `PublicUser`
- Errores observables: 401 token ausente, inválido, expirado o revocado
- Implementación: `me` en `app/modules/usuarios/api/auth_router.py:118`


### POST /api/v1/auth/register

**Descripción.** Registra una cuenta nueva y siempre asigna el rol CLIENTE.

- Autenticación: No
- Rol o alcance: -
- Entrada: RegisterRequest: full_name, email, password, phone?
- Respuesta: HTTP status.HTTP_201_CREATED con `PublicUser`
- Errores observables: 409 correo duplicado; 422 validación
- Implementación: `register` en `app/modules/usuarios/api/auth_router.py:72`


### POST /api/v1/auth/reset-password

**Descripción.** Consume un token de recuperación de un solo uso y revoca sesiones previas.

- Autenticación: No
- Rol o alcance: -
- Entrada: ResetPasswordRequest: token, new_password
- Respuesta: HTTP 200 con `MessageResponse`
- Errores observables: 400 token inválido o usado; 429 rate limit; 422 validación
- Implementación: `reset_password` en `app/modules/usuarios/api/auth_router.py:161`


### POST /api/v1/auth/verify-email

**Descripción.** Consume un token de verificación de correo de un solo uso.

- Autenticación: No
- Rol o alcance: -
- Entrada: VerifyEmailRequest: token
- Respuesta: HTTP 200 con `MessageResponse`
- Errores observables: 400 token inválido o usado; 429 rate limit; 422 validación
- Implementación: `verify_email` en `app/modules/usuarios/api/auth_router.py:195`


### GET /api/v1/categories

**Descripción.** Lista categorías activas.

- Autenticación: No
- Rol o alcance: -
- Entrada: Sin body
- Respuesta: HTTP 200 con `list[CategoryRead]`
- Errores observables: -
- Implementación: `list_categories` en `app/modules/conocimiento/api/router.py:210`


### POST /api/v1/categories

**Descripción.** Crea una categoría de tickets.

- Autenticación: Bearer JWT
- Rol o alcance: SUPERVISOR
- Entrada: CategoryCreate: name, description
- Respuesta: HTTP status.HTTP_201_CREATED con `CategoryRead (201)`
- Errores observables: 401/403; 409 nombre duplicado; 422 validación
- Implementación: `create_category` en `app/modules/conocimiento/api/router.py:220`


### PATCH /api/v1/categories/{category_id}

**Descripción.** Actualiza nombre o descripción de una categoría.

- Autenticación: Bearer JWT
- Rol o alcance: SUPERVISOR
- Entrada: Path: category_id; CategoryUpdate: name?, description?
- Respuesta: HTTP 200 con `CategoryRead`
- Errores observables: 401/403; 404; 409 nombre duplicado; 422 validación
- Implementación: `update_category` en `app/modules/conocimiento/api/router.py:230`


### PATCH /api/v1/categories/{category_id}/status

**Descripción.** Activa o desactiva una categoría; las categorías usadas por tickets no pueden desactivarse.

- Autenticación: Bearer JWT
- Rol o alcance: SUPERVISOR
- Entrada: Path: category_id; ActiveStatusUpdate: is_active
- Respuesta: HTTP 200 con `CategoryRead`
- Errores observables: 401/403; 404; 409 categoría usada; 422 validación
- Implementación: `set_category_status` en `app/modules/conocimiento/api/router.py:241`


### POST /api/v1/chat/conversations

**Descripción.** Crea una conversación anónima o asociada al cliente autenticado.

- Autenticación: Opcional
- Rol o alcance: Visitante o CLIENTE
- Entrada: Sin body
- Respuesta: HTTP status.HTTP_201_CREATED con `ConversationRead (201)`
- Errores observables: 403 ASESOR/SUPERVISOR; 429 cuota anónima
- Implementación: `create_conversation` en `app/modules/chatbot/api/router.py:41`


### GET /api/v1/chat/conversations/{conversation_id}

**Descripción.** Devuelve conversación y mensajes si el visitante o cliente está autorizado.

- Autenticación: Opcional
- Rol o alcance: Visitante de anónima o CLIENTE propietario
- Entrada: Path: conversation_id
- Respuesta: HTTP 200 con `ConversationRead`
- Errores observables: 401 conversación vinculada; 403; 404
- Implementación: `get_conversation` en `app/modules/chatbot/api/router.py:59`


### POST /api/v1/chat/conversations/{conversation_id}/convert-to-ticket

**Descripción.** Convierte una conversación autenticada no resuelta en un ticket único.

- Autenticación: Bearer JWT
- Rol o alcance: CLIENTE
- Entrada: Path: conversation_id; TicketCreate
- Respuesta: HTTP status.HTTP_201_CREATED con `TicketRead (201)`
- Errores observables: 401; 403; 404; 409 estado/duplicado/no resuelta; 422 categoría; 429 cuota diaria
- Implementación: `convert_conversation_to_ticket` en `app/modules/chatbot/api/router.py:137`


### POST /api/v1/chat/conversations/{conversation_id}/escalate

**Descripción.** Deriva una conversación a atención humana de forma idempotente.

- Autenticación: Opcional
- Rol o alcance: Visitante o CLIENTE
- Entrada: Path: conversation_id; ConversationActionRequest: reason?
- Respuesta: HTTP 200 con `ConversationRead`
- Errores observables: 403; 404; 409 ya convertida; 422 validación
- Implementación: `escalate_conversation` en `app/modules/chatbot/api/router.py:84`


### POST /api/v1/chat/conversations/{conversation_id}/feedback

**Descripción.** Registra utilidad y aceptación de escalamiento sobre la última FAQ usada.

- Autenticación: Opcional
- Rol o alcance: Visitante o CLIENTE
- Entrada: Path: conversation_id; ChatFeedbackCreate: is_helpful, escalation_accepted?, reason?
- Respuesta: HTTP status.HTTP_201_CREATED con `ChatFeedbackRead (201)`
- Errores observables: 403; 404; 422 sin FAQ evaluable o validación
- Implementación: `feedback_conversation` en `app/modules/chatbot/api/router.py:109`


### POST /api/v1/chat/conversations/{conversation_id}/link-user

**Descripción.** Asocia una conversación anónima con el cliente autenticado.

- Autenticación: Bearer JWT
- Rol o alcance: CLIENTE
- Entrada: Path: conversation_id
- Respuesta: HTTP 200 con `LinkConversationResponse`
- Errores observables: 401; 403 rol o conversación; 404
- Implementación: `link_conversation` en `app/modules/chatbot/api/router.py:123`


### POST /api/v1/chat/conversations/{conversation_id}/messages

**Descripción.** Procesa un mensaje mediante recuperación FAQ determinista y fallback/IA opcional.

- Autenticación: Opcional
- Rol o alcance: Visitante o CLIENTE
- Entrada: Path: conversation_id; MessageCreate: content
- Respuesta: HTTP 200 con `BotMessageResponse`
- Errores observables: 403; 404; 409 conversación terminal; 422 contenido; 429 límites
- Implementación: `send_message` en `app/modules/chatbot/api/router.py:71`


### POST /api/v1/chat/conversations/{conversation_id}/reset

**Descripción.** Reinicia el contexto operativo sin borrar mensajes históricos.

- Autenticación: Opcional
- Rol o alcance: Visitante o CLIENTE
- Entrada: Path: conversation_id
- Respuesta: HTTP 200 con `ConversationRead`
- Errores observables: 403; 404; 409 ya convertida
- Implementación: `reset_conversation` en `app/modules/chatbot/api/router.py:95`


### GET /api/v1/faqs

**Descripción.** Lista FAQs publicadas, activas, vigentes y de categorías activas; soporta filtros y paginación opcional.

- Autenticación: No
- Rol o alcance: -
- Entrada: Query: search?, category_id?, tag?, published_from?, published_to?, page?, page_size?
- Respuesta: HTTP 200 con `list[FAQRead] o FAQPage`
- Errores observables: 422 parámetros o rango de fechas inválido
- Implementación: `list_faqs` en `app/modules/conocimiento/api/router.py:45`


### POST /api/v1/faqs

**Descripción.** Crea contenido de conocimiento como DRAFT inactivo y registra su primera versión.

- Autenticación: Bearer JWT
- Rol o alcance: SUPERVISOR
- Entrada: FAQCreate: category_id, question, answer, keywords, title?, summary?, tags?, synonyms?, intent?, priority?, display_order?
- Respuesta: HTTP status.HTTP_201_CREATED con `FAQRead (201)`
- Errores observables: 401/403; 409 contenido duplicado; 422 categoría o validación
- Implementación: `create_faq` en `app/modules/conocimiento/api/router.py:152`


### GET /api/v1/faqs/admin

**Descripción.** Lista el catálogo editorial con filtros de estado, contenido y fechas.

- Autenticación: Bearer JWT
- Rol o alcance: SUPERVISOR
- Entrada: Query: status?, search?, category_id?, tag?, created_from?, created_to?, updated_from?, updated_to?, page=1, page_size=20
- Respuesta: HTTP 200 con `FAQAdminPage`
- Errores observables: 401/403; 422 filtros o rangos
- Implementación: `list_admin_faqs` en `app/modules/conocimiento/api/router.py:70`


### GET /api/v1/faqs/admin/metrics/utility

**Descripción.** Devuelve métricas agregadas de utilidad del feedback de FAQs.

- Autenticación: Bearer JWT
- Rol o alcance: SUPERVISOR
- Entrada: Sin body
- Respuesta: HTTP 200 con `FAQUtilityMetrics`
- Errores observables: 401/403
- Implementación: `faq_utility_metrics` en `app/modules/conocimiento/api/router.py:109`


### GET /api/v1/faqs/{faq_id}

**Descripción.** Obtiene una FAQ pública activa.

- Autenticación: No
- Rol o alcance: -
- Entrada: Path: faq_id
- Respuesta: HTTP 200 con `FAQRead`
- Errores observables: 404 FAQ inexistente o no pública
- Implementación: `get_faq` en `app/modules/conocimiento/api/router.py:143`


### PATCH /api/v1/faqs/{faq_id}

**Descripción.** Actualiza una FAQ; si una FAQ publicada cambia contenido vuelve a REVIEW e incrementa versión.

- Autenticación: Bearer JWT
- Rol o alcance: SUPERVISOR
- Entrada: Path: faq_id; FAQUpdate parcial
- Respuesta: HTTP 200 con `FAQRead`
- Errores observables: 401/403; 404; 409 duplicado; 422 payload o categoría
- Implementación: `update_faq` en `app/modules/conocimiento/api/router.py:162`


### POST /api/v1/faqs/{faq_id}/feedback

**Descripción.** Registra feedback anónimo o de un cliente sobre una FAQ activa.

- Autenticación: Opcional
- Rol o alcance: Visitante o CLIENTE
- Entrada: Path: faq_id; FAQFeedbackCreate: is_helpful, comment?
- Respuesta: HTTP status.HTTP_201_CREATED con `FAQFeedbackRead (201)`
- Errores observables: 403 rol distinto de CLIENTE; 404 FAQ; 422 validación
- Implementación: `add_faq_feedback` en `app/modules/conocimiento/api/router.py:132`


### GET /api/v1/faqs/{faq_id}/history

**Descripción.** Consulta snapshots editoriales de una FAQ.

- Autenticación: Bearer JWT
- Rol o alcance: SUPERVISOR
- Entrada: Path: faq_id
- Respuesta: HTTP 200 con `list[FAQVersionRead]`
- Errores observables: 401/403; 404 FAQ inexistente
- Implementación: `get_faq_history` en `app/modules/conocimiento/api/router.py:118`


### PATCH /api/v1/faqs/{faq_id}/status

**Descripción.** Atajo para publicar o archivar una FAQ mediante is_active.

- Autenticación: Bearer JWT
- Rol o alcance: SUPERVISOR
- Entrada: Path: faq_id; ActiveStatusUpdate: is_active
- Respuesta: HTTP 200 con `FAQRead`
- Errores observables: 401/403; 404; 409 transición; 422 contenido/categoría
- Implementación: `set_faq_status` en `app/modules/conocimiento/api/router.py:184`


### PATCH /api/v1/faqs/{faq_id}/workflow

**Descripción.** Aplica transiciones editoriales DRAFT, REVIEW, PUBLISHED o ARCHIVED.

- Autenticación: Bearer JWT
- Rol o alcance: SUPERVISOR
- Entrada: Path: faq_id; WorkflowStatusUpdate: status
- Respuesta: HTTP 200 con `FAQRead`
- Errores observables: 401/403; 404; 409 transición inválida; 422 publicación no válida
- Implementación: `update_faq_workflow` en `app/modules/conocimiento/api/router.py:173`


### GET /api/v1/health

**Descripción.** Comprobación estática de disponibilidad de la API.

- Autenticación: No
- Rol o alcance: -
- Entrada: Sin body
- Respuesta: HTTP 200 con `{status, service}`
- Errores observables: -
- Implementación: `health` en `app/api/v1/router.py:30`


### GET /api/v1/notifications

**Descripción.** Lista notificaciones propias paginadas, ordenadas por created_at e id descendentes.

- Autenticación: Bearer JWT
- Rol o alcance: CLIENTE, ASESOR o SUPERVISOR
- Entrada: Query: page=1, page_size=20 (1..100)
- Respuesta: HTTP 200 con `NotificationPage`
- Errores observables: 401; 422 paginación
- Implementación: `list_notifications` en `app/modules/notificaciones/api/router.py:24`


### POST /api/v1/notifications/read-all

**Descripción.** Marca como leídas todas las notificaciones propias y devuelve el contador actualizado.

- Autenticación: Bearer JWT
- Rol o alcance: CLIENTE, ASESOR o SUPERVISOR
- Entrada: Sin body
- Respuesta: HTTP 200 con `ReadAllResponse`
- Errores observables: 401
- Implementación: `read_all` en `app/modules/notificaciones/api/router.py:53`


### GET /api/v1/notifications/unread-count

**Descripción.** Cuenta notificaciones no leídas del usuario actual.

- Autenticación: Bearer JWT
- Rol o alcance: CLIENTE, ASESOR o SUPERVISOR
- Entrada: Sin body
- Respuesta: HTTP 200 con `UnreadCountRead`
- Errores observables: 401
- Implementación: `unread_count` en `app/modules/notificaciones/api/router.py:42`


### PATCH /api/v1/notifications/{notification_id}/read

**Descripción.** Marca como leída una notificación propia.

- Autenticación: Bearer JWT
- Rol o alcance: CLIENTE, ASESOR o SUPERVISOR
- Entrada: Path: notification_id
- Respuesta: HTTP 200 con `NotificationRead`
- Errores observables: 401; 403 no pertenece al usuario; 404
- Implementación: `mark_read` en `app/modules/notificaciones/api/router.py:64`


### GET /api/v1/reports/by-category

**Descripción.** Agrupa tickets por categoría.

- Autenticación: Bearer JWT
- Rol o alcance: SUPERVISOR
- Entrada: Query: from?, to?, category_id?, status?, priority?
- Respuesta: HTTP 200 con `CategoryReport`
- Errores observables: 401/403; 422 rango inválido
- Implementación: `by_category` en `app/modules/reportes/api/router.py:104`


### GET /api/v1/reports/by-priority

**Descripción.** Agrupa tickets por prioridad.

- Autenticación: Bearer JWT
- Rol o alcance: SUPERVISOR
- Entrada: Query: from?, to?, category_id?, status?, priority?
- Respuesta: HTTP 200 con `PriorityReport`
- Errores observables: 401/403; 422 rango inválido
- Implementación: `by_priority` en `app/modules/reportes/api/router.py:114`


### GET /api/v1/reports/by-status

**Descripción.** Agrupa tickets por estado.

- Autenticación: Bearer JWT
- Rol o alcance: SUPERVISOR
- Entrada: Query: from?, to?, category_id?, status?, priority?
- Respuesta: HTTP 200 con `StatusReport`
- Errores observables: 401/403; 422 rango inválido
- Implementación: `by_status` en `app/modules/reportes/api/router.py:94`


### GET /api/v1/reports/resolution-time

**Descripción.** Devuelve cantidad resuelta y promedio de tiempo de resolución.

- Autenticación: Bearer JWT
- Rol o alcance: SUPERVISOR
- Entrada: Query: from?, to?, category_id?, status?, priority?
- Respuesta: HTTP 200 con `ResolutionTimeReport`
- Errores observables: 401/403; 422 rango inválido
- Implementación: `resolution_time` en `app/modules/reportes/api/router.py:124`


### GET /api/v1/reports/summary

**Descripción.** Resumen de tickets por estado y promedio de resolución.

- Autenticación: Bearer JWT
- Rol o alcance: SUPERVISOR
- Entrada: Query: from?, to?, category_id?, status?, priority?
- Respuesta: HTTP 200 con `SummaryReport`
- Errores observables: 401/403; 422 rango inválido
- Implementación: `summary` en `app/modules/reportes/api/router.py:84`


### GET /api/v1/reports/{report_name}/export

**Descripción.** Genera exportación CSV de un reporte permitido y audita el intento.

- Autenticación: Bearer JWT
- Rol o alcance: SUPERVISOR
- Entrada: Path: report_name; Query: format=csv|xlsx y filtros from, to, category_id, status, priority, source, advisor_id, client_id, sla_compliant, search, limit
- Respuesta: HTTP 200 con `text/csv; XLSX no disponible`
- Errores observables: 401/403; 422 formato/rango/límite; 500 error de generación
- Implementación: `export_report` en `app/modules/reportes/api/router.py:134`


### POST /api/v1/sla/evaluate

**Descripción.** Evalúa bajo demanda advertencias y vencimientos de SLA idempotentemente.

- Autenticación: Bearer JWT
- Rol o alcance: SUPERVISOR
- Entrada: Sin body
- Respuesta: HTTP 200 con `SlaEvaluationRead`
- Errores observables: 401/403
- Implementación: `evaluate_sla` en `app/modules/tickets/api/sla_router.py:64`


### GET /api/v1/sla/policies

**Descripción.** Lista políticas SLA persistentes.

- Autenticación: Bearer JWT
- Rol o alcance: SUPERVISOR
- Entrada: Sin body
- Respuesta: HTTP 200 con `list[SlaPolicyRead]`
- Errores observables: 401/403
- Implementación: `list_sla_policies` en `app/modules/tickets/api/sla_router.py:78`


### POST /api/v1/sla/policies

**Descripción.** Crea política SLA por prioridad, categoría y origen.

- Autenticación: Bearer JWT
- Rol o alcance: SUPERVISOR
- Entrada: SlaPolicyCreate
- Respuesta: HTTP status.HTTP_201_CREATED con `SlaPolicyRead (201)`
- Errores observables: 401/403; 409 policy_key duplicada; 422 categoría, calendario, zona o tiempos
- Implementación: `create_sla_policy` en `app/modules/tickets/api/sla_router.py:94`


### PATCH /api/v1/sla/policies/{policy_id}

**Descripción.** Actualiza una política SLA y conserva historial de valores anteriores/nuevos.

- Autenticación: Bearer JWT
- Rol o alcance: SUPERVISOR
- Entrada: Path: policy_id; SlaPolicyUpdate parcial
- Respuesta: HTTP 200 con `SlaPolicyRead`
- Errores observables: 401/403; 404; 409 policy_key duplicada; 422 campos nulos, zona o tiempos
- Implementación: `update_sla_policy` en `app/modules/tickets/api/sla_router.py:105`


### GET /api/v1/tickets

**Descripción.** Lista tickets para personal interno; los asesores quedan limitados a tickets asignados.

- Autenticación: Bearer JWT
- Rol o alcance: ASESOR o SUPERVISOR
- Entrada: Query: status?, category_id?, priority?, created_from?, created_to?, source?, client_id?, assigned_advisor_id?, search?, updated_from?, updated_to?, page?, page_size?
- Respuesta: HTTP 200 con `list[TicketRead] o TicketPage`
- Errores observables: 401/403; 422 filtros/rangos
- Implementación: `list_tickets` en `app/modules/tickets/api/router.py:84`


### POST /api/v1/tickets

**Descripción.** Crea ticket manual con código TCK, prioridad y estado NUEVO; inicia SLA e historial.

- Autenticación: Bearer JWT
- Rol o alcance: CLIENTE
- Entrada: TicketCreate: category_id, subject, description, priority
- Respuesta: HTTP status.HTTP_201_CREATED con `TicketRead (201)`
- Errores observables: 401/403; 422 categoría o payload
- Implementación: `create_ticket` en `app/modules/tickets/api/router.py:37`


### GET /api/v1/tickets/mine

**Descripción.** Lista únicamente los tickets del cliente actual; modo legacy sin paginación o modo paginado.

- Autenticación: Bearer JWT
- Rol o alcance: CLIENTE
- Entrada: Query: status?, category_id?, priority?, created_from?, created_to?, source?, assigned_advisor_id?, search?, updated_from?, updated_to?, page?, page_size?
- Respuesta: HTTP 200 con `list[TicketRead] o TicketPage`
- Errores observables: 401/403; 422 filtros/rangos
- Implementación: `list_my_tickets` en `app/modules/tickets/api/router.py:47`


### GET /api/v1/tickets/operations

**Descripción.** Consulta bandejas operativas paginadas por cola, SLA, asignación o actividad reciente.

- Autenticación: Bearer JWT
- Rol o alcance: ASESOR o SUPERVISOR
- Entrada: Query: queue requerido; filtros de ticket; advisor_id?, recent_hours=24, page=1, page_size=20
- Respuesta: HTTP 200 con `TicketPage`
- Errores observables: 401/403; 422 cola, asesor, rangos o recent_hours
- Implementación: `list_operational_tickets` en `app/modules/tickets/api/router.py:137`


### GET /api/v1/tickets/{ticket_id}

**Descripción.** Obtiene un ticket si el cliente es propietario, el asesor está asignado o el actor es supervisor.

- Autenticación: Bearer JWT
- Rol o alcance: CLIENTE, ASESOR o SUPERVISOR
- Entrada: Path: ticket_id
- Respuesta: HTTP 200 con `TicketRead`
- Errores observables: 401; 403 no autorizado; 404
- Implementación: `get_ticket` en `app/modules/tickets/api/router.py:178`


### POST /api/v1/tickets/{ticket_id}/assignments

**Descripción.** Asigna o reasigna un ticket a un asesor activo.

- Autenticación: Bearer JWT
- Rol o alcance: SUPERVISOR
- Entrada: Path: ticket_id; AssignmentCreate: advisor_id, expected_version?
- Respuesta: HTTP 201 con `TicketRead (201)`
- Errores observables: 401/403; 404; 409 duplicado/terminal/versión; 422 asesor o payload
- Implementación: `assign_ticket` en `app/modules/tickets/api/router.py:220`


### GET /api/v1/tickets/{ticket_id}/attachments

**Descripción.** Lista metadatos de adjuntos activos del ticket.

- Autenticación: Bearer JWT
- Rol o alcance: CLIENTE/ASESOR/SUPERVISOR con acceso al ticket
- Entrada: Path: ticket_id
- Respuesta: HTTP 200 con `list[AttachmentRead]`
- Errores observables: 401; 403; 404
- Implementación: `list_attachments` en `app/modules/adjuntos/api/router.py:59`


### POST /api/v1/tickets/{ticket_id}/attachments

**Descripción.** Carga un archivo multipart asociado a un ticket y opcionalmente a un comentario.

- Autenticación: Bearer JWT
- Rol o alcance: CLIENTE/ASESOR/SUPERVISOR con acceso al ticket
- Entrada: Multipart: file requerido, comment_id opcional
- Respuesta: HTTP 201 con `AttachmentRead (201)`
- Errores observables: 401/403; 404 ticket/comentario; 413 tamaño/cuota; 415 MIME/extensión; 422 nombre/contenido
- Implementación: `upload_attachment` en `app/modules/adjuntos/api/router.py:37`


### POST /api/v1/tickets/{ticket_id}/cancel

**Descripción.** Cancela un ticket con motivo; la operación está reservada al supervisor.

- Autenticación: Bearer JWT
- Rol o alcance: SUPERVISOR
- Entrada: Path: ticket_id; ReasonRequest: reason, expected_version?
- Respuesta: HTTP 200 con `TicketRead`
- Errores observables: 401/403; 404; 409 transición/terminal/versión; 422 motivo
- Implementación: `cancel_ticket` en `app/modules/tickets/api/router.py:294`


### POST /api/v1/tickets/{ticket_id}/close

**Descripción.** Cierra un ticket mediante una transición válida.

- Autenticación: Bearer JWT
- Rol o alcance: ASESOR o SUPERVISOR
- Entrada: Path: ticket_id
- Respuesta: HTTP 200 con `TicketRead`
- Errores observables: 401/403; 404; 409 transición/terminal
- Implementación: `close_ticket` en `app/modules/tickets/api/router.py:273`


### GET /api/v1/tickets/{ticket_id}/comments

**Descripción.** Lista comentarios autorizados en orden de creación.

- Autenticación: Bearer JWT
- Rol o alcance: CLIENTE, ASESOR o SUPERVISOR
- Entrada: Path: ticket_id
- Respuesta: HTTP 200 con `list[CommentRead]`
- Errores observables: 401; 403; 404
- Implementación: `get_comments` en `app/modules/tickets/api/router.py:199`


### POST /api/v1/tickets/{ticket_id}/comments

**Descripción.** Agrega comentario, historial, primera respuesta SLA y notificación al interlocutor.

- Autenticación: Bearer JWT
- Rol o alcance: Propietario CLIENTE, asesor asignado o SUPERVISOR
- Entrada: Path: ticket_id; CommentCreate: content, expected_version?
- Respuesta: HTTP 201 con `CommentRead (201)`
- Errores observables: 401/403; 404; 409 cerrado/cancelado o versión; 422 contenido
- Implementación: `add_comment` en `app/modules/tickets/api/router.py:188`


### GET /api/v1/tickets/{ticket_id}/history

**Descripción.** Devuelve el historial de mutaciones del ticket.

- Autenticación: Bearer JWT
- Rol o alcance: CLIENTE/ASESOR/SUPERVISOR con autorización sobre el ticket
- Entrada: Path: ticket_id
- Respuesta: HTTP 200 con `list[HistoryRead]`
- Errores observables: 401; 403; 404
- Implementación: `get_history` en `app/modules/tickets/api/router.py:263`


### POST /api/v1/tickets/{ticket_id}/release

**Descripción.** Libera asignación propia del asesor o una asignación que gestiona un supervisor.

- Autenticación: Bearer JWT
- Rol o alcance: ASESOR o SUPERVISOR
- Entrada: Path: ticket_id; TicketActionRequest opcional: expected_version?
- Respuesta: HTTP 200 con `TicketRead`
- Errores observables: 401/403; 404; 409 sin asignación/terminal/versión
- Implementación: `release_ticket` en `app/modules/tickets/api/router.py:247`


### POST /api/v1/tickets/{ticket_id}/reopen

**Descripción.** Reabre un ticket RESUELTO hacia EN_PROCESO y exige motivo.

- Autenticación: Bearer JWT
- Rol o alcance: ASESOR con asignación o SUPERVISOR
- Entrada: Path: ticket_id; ReasonRequest: reason, expected_version?
- Respuesta: HTTP 200 con `TicketRead`
- Errores observables: 401/403; 404; 409 estado/versión; 422 motivo
- Implementación: `reopen_ticket` en `app/modules/tickets/api/router.py:283`


### GET /api/v1/tickets/{ticket_id}/sla

**Descripción.** Consulta el registro SLA y la política aplicada al ticket autorizado.

- Autenticación: Bearer JWT
- Rol o alcance: CLIENTE/ASESOR/SUPERVISOR con autorización sobre el ticket
- Entrada: Path: ticket_id
- Respuesta: HTTP 200 con `TicketSlaRead`
- Errores observables: 401; 403; 404; 422 política inexistente
- Implementación: `get_ticket_sla` en `app/modules/tickets/api/sla_router.py:30`


### POST /api/v1/tickets/{ticket_id}/sla/pause

**Descripción.** Pausa manualmente un SLA activo con motivo e historial.

- Autenticación: Bearer JWT
- Rol o alcance: ASESOR o SUPERVISOR con acceso al ticket
- Entrada: Path: ticket_id; SlaActionRequest: reason
- Respuesta: HTTP 200 con `TicketSlaRead`
- Errores observables: 401/403; 404; 409 SLA inactivo/ticket terminal; 422 motivo
- Implementación: `pause_ticket_sla` en `app/modules/tickets/api/sla_router.py:41`


### POST /api/v1/tickets/{ticket_id}/sla/resume

**Descripción.** Reanuda SLA pausado y extiende vencimientos por el tiempo pausado.

- Autenticación: Bearer JWT
- Rol o alcance: ASESOR o SUPERVISOR con acceso al ticket
- Entrada: Path: ticket_id
- Respuesta: HTTP 200 con `TicketSlaRead`
- Errores observables: 401/403; 404; 409 SLA no pausado; 422 reloj inválido
- Implementación: `resume_ticket_sla` en `app/modules/tickets/api/sla_router.py:53`


### POST /api/v1/tickets/{ticket_id}/status

**Descripción.** Cambia el estado con la matriz de transiciones válida y registra historial/notificación.

- Autenticación: Bearer JWT
- Rol o alcance: ASESOR o SUPERVISOR
- Entrada: Path: ticket_id; StatusChange: status, reason?, expected_version?
- Respuesta: HTTP 200 con `TicketRead`
- Errores observables: 401/403; 404; 409 transición, terminal o versión; 422 motivo/payload
- Implementación: `change_status` en `app/modules/tickets/api/router.py:209`


### POST /api/v1/tickets/{ticket_id}/take

**Descripción.** Permite a un asesor tomar un ticket abierto sin asignación.

- Autenticación: Bearer JWT
- Rol o alcance: ASESOR
- Entrada: Path: ticket_id; TicketActionRequest opcional: expected_version?
- Respuesta: HTTP 200 con `TicketRead`
- Errores observables: 401/403; 404; 409 asignado/terminal/versión
- Implementación: `take_ticket` en `app/modules/tickets/api/router.py:231`


### GET /api/v1/users/advisors

**Descripción.** Lista asesores activos para selección operativa.

- Autenticación: Bearer JWT
- Rol o alcance: SUPERVISOR
- Entrada: Sin body
- Respuesta: HTTP 200 con `list[PublicUser]`
- Errores observables: 401/403 autenticación o rol; 422 validación
- Implementación: `list_advisors` en `app/modules/usuarios/api/user_router.py:38`


### GET /api/v1/users/me/preferences

**Descripción.** Obtiene o crea preferencias persistentes del usuario.

- Autenticación: Bearer JWT
- Rol o alcance: CLIENTE, ASESOR o SUPERVISOR
- Entrada: Sin body
- Respuesta: HTTP 200 con `PreferencesRead`
- Errores observables: 401 token inválido
- Implementación: `get_my_preferences` en `app/modules/usuarios/api/user_router.py:66`


### PATCH /api/v1/users/me/preferences

**Descripción.** Actualiza preferencias de notificación, idioma y zona horaria.

- Autenticación: Bearer JWT
- Rol o alcance: CLIENTE, ASESOR o SUPERVISOR
- Entrada: PreferencesUpdate: flags?, preferred_language?, timezone?
- Respuesta: HTTP 200 con `PreferencesRead`
- Errores observables: 401 autenticación; 422 zona horaria o seguridad desactivada
- Implementación: `update_my_preferences` en `app/modules/usuarios/api/user_router.py:75`


### GET /api/v1/users/me/profile

**Descripción.** Consulta el perfil público ampliado del usuario actual.

- Autenticación: Bearer JWT
- Rol o alcance: CLIENTE, ASESOR o SUPERVISOR
- Entrada: Sin body
- Respuesta: HTTP 200 con `ProfileRead`
- Errores observables: 401 token inválido
- Implementación: `get_my_profile` en `app/modules/usuarios/api/user_router.py:47`


### PATCH /api/v1/users/me/profile

**Descripción.** Actualiza únicamente full_name y phone del propio usuario.

- Autenticación: Bearer JWT
- Rol o alcance: CLIENTE, ASESOR o SUPERVISOR
- Entrada: ProfileUpdate: full_name?, phone?
- Respuesta: HTTP 200 con `ProfileRead`
- Errores observables: 401 autenticación; 422 payload o teléfono inválido
- Implementación: `update_my_profile` en `app/modules/usuarios/api/user_router.py:56`


## 8. Seguridad, autenticación y permisos

El esquema HTTP usa `HTTPBearer(auto_error=False)`. `get_current_user` exige claims `exp`, `iat`, `sub` y `type`; acepta solo `type=access`, valida el usuario activo, el rol y, para tokens nuevos, la fila `auth_sessions` referenciada por `jti`. Los tokens legacy sin `jti` se aceptan hasta expiración, pero quedan invalidados si el usuario tiene `sessions_invalidated_at` posterior.

Contraseñas: `pwdlib.PasswordHash.recommended()`; la política exige por defecto mínimo 8 caracteres, mayúscula, minúscula y dígito. Tokens de recuperación/verificación: se genera valor aleatorio, se guarda únicamente SHA-256, se marca `used_at` y se invalida el token anterior de la misma finalidad. El adaptador de correo local no envía ni registra tokens.

La redacción global sustituye claves sensibles (password, hash, token, secret, API key, authorization, cookie, credential, email, etc.), limita profundidad a 6, colecciones a 100 elementos y strings a 512 caracteres. Guardrails del chatbot bloquean inyección, solicitudes de secretos, contenido sensible, salida con tokens/scripts, fuentes inválidas, salida fuera de contexto y exceso de longitud.

CORS usa orígenes de `CORS_ORIGINS`, métodos GET/POST/PATCH/DELETE/OPTIONS y headers Authorization/Content-Type. `allow_credentials=False`. Trusted hosts usa `TRUSTED_HOSTS`. Headers añadidos: `X-Content-Type-Options: nosniff` y `Referrer-Policy: no-referrer`.

```mermaid
sequenceDiagram
  actor U as Usuario
  participant A as /auth/login
  participant S as AuthService
  participant DB as MySQL
  participant F as get_current_user
  U->>A: email + password
  A->>S: authenticate + rate limit
  S->>DB: usuario, role, auth_rate_limits
  S->>DB: crear auth_sessions
  S-->>U: JWT access token
  U->>F: Authorization Bearer
  F->>DB: valida usuario y jti/session
  F-->>U: identidad y rol o 401/403
```

### Roles

| Rol | Permisos observados |
| --- | --- |
| CLIENTE | Crear/consultar/comentar propios tickets; usar chatbot; asociar y convertir conversaciones; feedback FAQ; perfil, preferencias y notificaciones propias. |
| ASESOR | Consultar tickets asignados; operar estados y comentarios de tickets asignados; tomar/liberar según reglas; ver SLA y operar colas internas. |
| SUPERVISOR | Todo lo interno global; asignar/reasignar/cancelar; gestionar FAQs/categorías; gestionar políticas SLA; evaluar SLA; reportes/exportaciones; directorio de asesores; auditoría global. |

## 9. Persistencia y modelo de datos

El motor declarado es MySQL, conectado mediante `mysql+pymysql`. SQLModel registra 24 tablas en `app/db/models.py`. Alembic configura `SQLModel.metadata` y contiene 17 migraciones, desde `0001_initial_schema` hasta `0017_chatbot_ai_trace`. No se detectaron queries SQL nativas, procedures, locks optimistas de base de datos ni motor vectorial; sí existe `SELECT ... FOR UPDATE` en operaciones mutables de tickets y control optimista por `Ticket.version`/`expected_version`.

| Modelo | Tabla | Campos principales | Claves foráneas |
| --- | --- | --- | --- |
| Attachment | attachments | id, ticket_id, comment_id?, uploaded_by_user_id, filename, MIME declarado/detectado, size, sha256, status, timestamps | comment_id -> ticket_comments.id, ticket_id -> tickets.id, uploaded_by_user_id -> users.id |
| AuditLog | audit_logs | id, event_type, action, actor/target, resource, result, redacted before/after/metadata, correlation/request ids | actor_user_id -> users.id, target_user_id -> users.id |
| AuthRateLimit | auth_rate_limits | id, action, hashed key, window, attempts, blocked_until | - |
| AuthSession | auth_sessions | id, user_id, issued_at, expires_at, revoked_at | user_id -> users.id |
| AuthToken | auth_tokens | id, user_id, purpose, token_hash, expiry, used_at | user_id -> users.id |
| ChatMessage | chat_messages | id, conversation_id, sender_type, content, intent, confidence, faq_id, response_source, ai_trace_json | conversation_id -> conversations.id, faq_id -> faqs.id |
| Conversation | conversations | id, user_id?, anonymous_key, status, context, FAQ/category, turn_count, confidence, escalation/conversion timestamps | category_id -> ticket_categories.id, last_faq_id -> faqs.id, user_id -> users.id |
| FAQFeedback | faq_feedback | id, faq_id, user_id?, is_helpful, escalation_accepted?, comment, created_at | faq_id -> faqs.id, user_id -> users.id |
| FAQVersion | faq_versions | id, faq_id, version, editorial content, status, active/publication dates, changed_by, action | category_id -> ticket_categories.id, changed_by -> users.id, faq_id -> faqs.id |
| FAQ | faqs | id, category, content, normalized search fields, status, priority/order/version, active flag, authors and publication dates | category_id -> ticket_categories.id, created_by -> users.id, updated_by -> users.id |
| Notification | notifications | id, recipient, type, message, related ticket/conversation, read state, metadata, dedupe_key | recipient_user_id -> users.id, related_conversation_id -> conversations.id, related_ticket_id -> tickets.id |
| ReportExportAudit | report_export_audits | id, actor, report, format, filters, row_count, succeeded, error_code, created_at | actor_id -> users.id |
| Role | roles | id, unique name, description | - |
| SlaPolicy | sla_policies | id, unique policy_key, priority/category/source scope, response/resolution/warning seconds, UTC/24x7, active | category_id -> ticket_categories.id |
| SlaPolicyHistory | sla_policy_history | id, policy_id, actor_id, action, old/new JSON values, created_at | actor_id -> users.id, policy_id -> sla_policies.id |
| TicketAssignment | ticket_assignments | id, ticket, advisor, assigned_by, assigned_at, unassigned_at | advisor_id -> users.id, assigned_by -> users.id, ticket_id -> tickets.id |
| TicketCategory | ticket_categories | id, unique name, description, active flag, timestamps | - |
| TicketComment | ticket_comments | id, ticket, author, content, created_at | author_id -> users.id, ticket_id -> tickets.id |
| TicketHistory | ticket_history | id, ticket, actor, action, old/new values, description, created_at | actor_id -> users.id, ticket_id -> tickets.id |
| TicketSla | ticket_slas | id, unique ticket_id, policy, due dates, responses, pause/breach/completion fields, reopen_count | policy_id -> sla_policies.id, ticket_id -> tickets.id |
| Ticket | tickets | id, unique tracking_code, client, optional conversation, category, subject/description, priority/status/source, assignment, version/timestamps | assigned_advisor_id -> users.id, category_id -> ticket_categories.id, client_id -> users.id, conversation_id -> conversations.id |
| UserPreference | user_preferences | PK/FK user_id, channel flags, assignment/status/comment/SLA flags, language, timezone, timestamps | user_id -> users.id |
| UserProfileAudit | user_profile_audits | id, actor/affected user, field, redacted old/new values, created_at | actor_id -> users.id, affected_user_id -> users.id |
| User | users | id, role_id, identity, password_hash, phone, active/email_verified flags, session invalidation timestamp | role_id -> roles.id |

### Relaciones principales

```mermaid
erDiagram
  ROLES ||--o{ USERS : role_id
  USERS ||--o{ TICKETS : client_id
  USERS ||--o{ TICKETS : assigned_advisor_id
  TICKET_CATEGORIES ||--o{ TICKETS : category_id
  TICKETS ||--o{ TICKET_COMMENTS : ticket_id
  TICKETS ||--o{ TICKET_ASSIGNMENTS : ticket_id
  TICKETS ||--o{ TICKET_HISTORY : ticket_id
  TICKETS ||--|| TICKET_SLAS : ticket_id
  SLA_POLICIES ||--o{ TICKET_SLAS : policy_id
  USERS ||--o{ CONVERSATIONS : user_id
  CONVERSATIONS ||--o{ CHAT_MESSAGES : conversation_id
  FAQS ||--o{ CHAT_MESSAGES : faq_id
  TICKET_CATEGORIES ||--o{ FAQS : category_id
  FAQS ||--o{ FAQ_VERSIONS : faq_id
  TICKETS ||--o{ ATTACHMENTS : ticket_id
```

Las relaciones anteriores se derivan de `Field(foreign_key=...)` y `ForeignKey(...)` del modelo. No se agregan relaciones ORM implícitas que no estén declaradas.

### Servicios y repositorios

| Módulo | Servicios | Responsabilidad |
| --- | --- | --- |
| usuarios | AuthService, UserProfileService, UserService, AuthRateLimiter | auth, perfiles/preferencias, directorio de asesores y límites de autenticación |
| tickets | TicketService, SlaService | tickets, estados, asignaciones, SLA, historial y notificaciones |
| conocimiento | FAQService, CategoryService | editorial de FAQs/categorías, matching y feedback |
| chatbot | ChatbotService, RAGService | conversación, recuperación local, guardrails, proveedor IA opcional |
| adjuntos | AttachmentService, LocalStorageProvider | validación de archivos y almacenamiento privado local |
| notificaciones | NotificationService, NoopNotificationProvider | preferencias, persistencia y entrega desacoplada |
| reportes | ReportService | métricas, exportación CSV y auditoría de exportaciones |
| auditoria | AuditService | redacción, deduplicación y persistencia append-only |

| Repositorio | Tablas | Operaciones relevantes |
| --- | --- | --- |
| UserRepository | users, roles | email/id, role, asesores activos |
| AuthRepository | auth_sessions, auth_tokens, auth_rate_limits | sesiones, tokens hash, invalidación y rate limit |
| UserProfileRepository | user_preferences, user_profile_audits | preferencias y auditorías de perfil |
| TicketRepository | tickets, comments, assignments, history | listados legacy/paginados/operativos, locks, filtros y consultas auxiliares |
| SlaRepository | sla_policies, ticket_slas, sla_policy_history | selección de policy, SLA por ticket y persistencia de historial |
| CategoryRepository | ticket_categories | categorías activas y por nombre/id |
| FAQRepository | faqs, faq_versions, faq_feedback | públicas/editoriales, relevancia, versiones y métricas |
| ConversationRepository | conversations, chat_messages | conversaciones, mensajes, cuotas y FAQs activas |
| AttachmentRepository | attachments, ticket_comments, tickets | metadatos, cuotas, adjuntos activos y soft delete |
| NotificationRepository | notifications | deduplicación, listados y lectura |
| ReportRepository | tickets y tablas relacionadas | agregados, export rows y límites |
| AuditRepository | audit_logs | append-only, filtros y paginación |

## 10. Reglas de negocio y validaciones

| Código | Regla detectada | Módulo / evidencia |
| --- | --- | --- |
| RN-01 | El registro crea únicamente CLIENTE. | usuarios / AuthService.register |
| RN-02 | Un JWT nuevo se vincula a auth_sessions y logout revoca esa sesión. | usuarios / AuthService, deps.py |
| RN-03 | Password reset y email verification usan tokens hash, expirables y de un solo uso. | usuarios / AuthToken, AuthService |
| RN-04 | La recuperación devuelve mensaje uniforme para evitar enumeración. | usuarios / auth_router.py |
| RN-05 | Solo CLIENTE crea tickets manuales o convierte conversaciones. | tickets / TicketService |
| RN-06 | Una categoría usada por tickets no puede desactivarse. | conocimiento / CategoryService |
| RN-07 | Una FAQ se publica solo desde transición válida, con categoría activa y contenido válido. | conocimiento / FAQService |
| RN-08 | Editar contenido publicado devuelve la FAQ a REVIEW y crea snapshot/version. | conocimiento / FAQService |
| RN-09 | El chatbot acepta visitantes o CLIENTE; personal interno no puede utilizarlo. | chatbot / ChatbotService |
| RN-10 | Conversaciones terminales no aceptan nuevos mensajes y una conversión duplicada devuelve 409. | chatbot / ChatbotService, TicketService |
| RN-11 | Los tickets cerrados o cancelados no se modifican. | tickets / TicketService |
| RN-12 | Cancelar exige motivo y rol SUPERVISOR; reabrir RESUELTO exige motivo. | tickets / TicketService |
| RN-13 | Las mutaciones de tickets incrementan version y pueden validar expected_version. | tickets / TicketService |
| RN-14 | Solo asesor/supervisor cuentan como primera respuesta SLA. | tickets / SlaService |
| RN-15 | La evaluación SLA es idempotente por timestamps/dedupe de notificaciones y se invoca bajo demanda. | tickets / SlaService |
| RN-16 | Adjuntos se aceptan solo con extensión, MIME y firma compatibles, cuotas y acceso autorizado. | adjuntos / AttachmentService |
| RN-17 | Notificaciones críticas de seguridad no se pueden desactivar. | usuarios/notificaciones / PreferencesUpdate, NotificationService |
| RN-18 | Solo SUPERVISOR consulta reportes, exporta y consulta auditoría global. | reportes/auditoria / services y deps |
| RN-19 | La exportación solo entrega CSV; XLSX responde 422. | reportes / ReportService |

### Validaciones de entrada

Pydantic valida longitudes, enums, correos, rangos, listas de tags/sinónimos (máximo 20 términos de 80 caracteres), `page/page_size` (1..100), teléfonos, zonas horarias IANA, tiempos SLA (1..31.536.000 segundos) y cuerpos con `extra=forbid` donde corresponde. FastAPI genera 422 para fallos de contrato.

### Validaciones de negocio y persistencia

Los servicios validan existencia/actividad de categorías y asesores, propiedad/alcance por rol, estados terminales, transiciones, versión esperada, fechas de rango, cuotas de chatbot/adjuntos/exportación, duplicidad de FAQ/policy keys, publicación, rate limiting, tokens y acceso a notificaciones. Los modelos agregan PK, FK, unique constraints e índices.

## 11. Integraciones externas

Durante el análisis no se identificaron integraciones externas activas por defecto. Existe un puerto opcional de IA con dos adaptadores: `OpenAIResponsesProvider`, que requiere habilitación explícita, `CHATBOT_AI_PROVIDER=openai` y `OPENAI_API_KEY`; y `HttpJsonAIProvider`, que requiere `CHATBOT_AI_PROVIDER=http_json`, endpoint, API key y modelo.

| Integración | Estado | Contrato real | Seguridad y errores |
| --- | --- | --- | --- |
| OpenAI Responses API | Opcional; por defecto Noop/deshabilitado | POST con `store=false`, salida JSON estructurada (`text`, `source_ids`) y contexto FAQ autorizado; el modelo se configura por variable. | Requiere `CHATBOT_AI_ENABLED=true` y `OPENAI_API_KEY`; timeout, errores HTTP/red o salida inválida conservan el fallback seguro. |
| Gateway IA HTTP JSON | Opcional; por defecto Noop/deshabilitado | POST al endpoint configurado con model, input, policy, prompt_version y context autorizado; espera text y source_ids. | Bearer API key desde variable; timeout configurable (15 s por defecto); timeout/error/respuesta inválida -> fallback; circuito tras fallos consecutivos. |
| Correo | Noop local | Puerto EmailProvider para reset/verificación; el adaptador incluido no envía, guarda ni loguea tokens. | No hay SMTP ni SDK real en el repositorio. |
| Entrega de notificaciones | Noop local | Puerto NotificationProvider; persistencia in-app permanece local. | Proveedor externo no configurado; errores de entrega son best effort. |
| Almacenamiento de archivos | Local | LocalStorageProvider en ATTACHMENT_STORAGE_DIR. | Path traversal bloqueado; raíz privada 0700; clave interna aleatoria; no hay cloud storage. |

Ambos adaptadores IA reciben entrada minimizada, contexto FAQ autorizado y una salida acotada; las fuentes devueltas se validan antes de aceptar una respuesta. La clave OpenAI y la API key del gateway nunca se exponen al frontend. El adaptador OpenAI envía `store=false`; esto no equivale a una garantía de Zero Data Retention del proveedor.

## 12. DTOs, errores y paginación

Los contratos públicos están en `schemas/`: auth/profile, tickets/operations/SLA, FAQ/category, chatbot, attachments, notifications, reports y audit. Los `Read` usan `from_attributes` cuando se construyen desde SQLModel. Los modelos de respuesta de tickets, FAQs y notificaciones usan una envoltura paginada con `page`, `page_size`, `total`, `total_pages` e `items`.

Los endpoints de tickets y FAQs mantienen compatibilidad: sin `page` ni `page_size` devuelven listas legacy; al enviar cualquiera de ellos devuelven página. Páginas fuera de rango devuelven `items: []`. Los listados ordenan tickets por `created_at DESC, id DESC`; notificaciones por `created_at DESC, id DESC`; comentarios e historiales siguen el orden registrado por repositorio.

No existe un `@ControllerAdvice`/manejador global equivalente: los routers y servicios convierten errores de dominio conocidos en `HTTPException`; errores no previstos quedan a cargo de FastAPI/Starlette. Los formatos observables más comunes son el detalle FastAPI (`{"detail": ...}`), respuestas de validación 422 y respuestas binarias para descarga/exportación.

## 13. Configuración y ejecución

| Variable o grupo | Obligatoria | Descripción | Ejemplo seguro |
| --- | --- | --- | --- |
| APP_NAME, APP_ENV, DEBUG | No | Identidad de la API, entorno y debug. | APP_ENV=development |
| DATABASE_URL | Sí para ejecución real | URL SQLAlchemy de MySQL con PyMySQL. | mysql+pymysql://usuario:********@host:3306/tickets |
| SECRET_KEY, JWT_ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES | SECRET_KEY sí fuera de desarrollo | Firma HS256 y duración de access token. | SECRET_KEY=********; JWT_ALGORITHM=HS256 |
| PASSWORD_* | No | Política de longitud, mayúscula, minúscula, dígito y expiración de tokens. | PASSWORD_MIN_LENGTH=8 |
| AUTH_RATE_LIMIT_* | No | Intentos, ventana y bloqueo para autenticación. | AUTH_RATE_LIMIT_MAX_ATTEMPTS=5 |
| ATTACHMENT_* | No | Ruta, tamaño por archivo/ticket, cantidad, extensiones y MIME. | ATTACHMENT_STORAGE_DIR=var/uploads |
| SLA_DEFAULT_*, SLA_POLICY_DEFAULTS | No | UTC, calendario 24x7 y tiempos por prioridad. | SLA_DEFAULT_TIMEZONE=UTC |
| REPORT_EXPORT_MAX_* | No | Máximo de filas y rango de exportación. | REPORT_EXPORT_MAX_ROWS=10000 |
| CHATBOT_* | No | Umbrales, cuotas, longitud, contexto y comportamiento IA. | CHATBOT_AI_ENABLED=false |
| CHATBOT_AI_PROVIDER, CHATBOT_AI_ENDPOINT, CHATBOT_AI_API_KEY, CHATBOT_AI_MODEL | Solo para adaptadores IA opcionales | Selección y configuración del adaptador `http_json`; nunca documentar valores. | CHATBOT_AI_API_KEY=******** |
| OPENAI_API_KEY | Solo para proveedor OpenAI opcional | Clave del adaptador OpenAI Responses; nunca documentar el valor. | OPENAI_API_KEY=******** |
| TRUSTED_HOSTS, CORS_ORIGINS | No | Hosts aceptados y orígenes CORS. | CORS_ORIGINS=["http://localhost:5173"] |
| MYSQL_* | Docker Compose | Credenciales y parámetros del contenedor MySQL. | MYSQL_PASSWORD=******** |
| DEMO_*_PASSWORD | Seed local | Contraseñas de las cuentas de demostración; solo deben usarse localmente. | DEMO_CLIENT_PASSWORD=******** |

### Ejecución local documentada por el proyecto

```bash
python -m venv .venv
pip install -e ".[dev]"
docker compose up -d db
alembic upgrade head
python -m app.seed.demo_data
fastapi dev app/main.py
pytest
ruff check .
ruff format --check .
```

Swagger queda en `http://localhost:8000/docs` solo en development; health es `GET /api/v1/health`. Docker Compose publica MySQL en `3307` y API en `8000`, usa volumen `mysql_data` y espera el healthcheck de MySQL antes de iniciar API.

## 14. Pruebas e infraestructura

Se encontraron 23 archivos de prueba, 181 funciones `test_*` y 219 casos recogidos por Pytest (incluye parametrizaciones). Hay pruebas unitarias (`tests/unit`), integración (`tests/integration`) y evaluación de calidad del chatbot (`tests/evaluation`). Cubren auth/security, health, CORS, tickets, paginación, comentarios, operaciones de asesores, SLA, adjuntos, notificaciones, perfil/preferencias, knowledge base, chatbot fases 11/12, reports/exports, audit y seed.

| Tipo | Ubicación | Alcance |
| --- | --- | --- |
| Unitarias | tests/unit/test_database_models.py, test_seed.py | Modelos SQLModel, seed e idempotencia. |
| Integración | tests/integration/*.py | API con TestClient, base de datos de prueba y reglas de permisos. |
| Evaluación | tests/evaluation/test_chatbot_phase12_quality.py | Calidad y guardrails del flujo IA/RAG. |

Validación ejecutada durante esta auditoría: `pytest` -> 219 passed, 2 warnings; `ruff check .` -> All checks passed; `ruff format --check .` -> 205 files already formatted. `alembic check` no pudo conectarse porque no había MySQL escuchando en `localhost:3307`; no se modificó el backend ni la base de datos.

```mermaid
flowchart TB
  U[usuarios] --> T[tickets]
  T --> S[SLA]
  T --> A[adjuntos]
  T --> N[notificaciones]
  K[conocimiento] --> C[chatbot]
  C --> T
  R[reportes] --> T
  AU[auditoria] -. registra .-> U
  AU -. registra .-> T
  AU -. registra .-> C
```

## 15. Pendientes y observaciones

### Funcionalidades pendientes o parcialmente implementadas

- El proveedor de correo incluido es `NoopEmailProvider`; el envío real debe inyectarse fuera de este prototipo.
- El proveedor de notificaciones incluido es `NoopNotificationProvider`; no existe entrega externa activa.
- La IA generativa es opcional y está deshabilitada por defecto; OpenAI requiere habilitación y clave explícitas, mientras que `http_json` requiere un gateway aprobado y sus credenciales.
- `format=xlsx` está representado en el enum de reportes, pero el servicio responde 422 porque no existe una dependencia XLSX.
- La evaluación SLA es manual mediante endpoint protegido; no hay scheduler, Celery, Redis ni proceso de fondo.
- No hay acciones masivas de tickets.

### Observaciones técnicas descriptivas

- `GET /api/v1/health` devuelve estado estático y no verifica conectividad de MySQL.
- `alembic.ini` contiene una URL local de desarrollo y `migrations/env.py` la sustituye por `DATABASE_URL` de settings.
- La documentación OpenAPI se desactiva fuera de development mediante `docs_url`, `redoc_url` y `openapi_url`.
- La auditoría global complementa, pero no reemplaza, historiales específicos de tickets, SLA, perfil, FAQs y exportaciones.
- Las tablas y consultas usan índices específicos para listados, estados, fechas, asignaciones, SLA y auditoría.
- El `pass` encontrado en adaptadores locales/capturas de errores representa fallbacks intencionales; no se identificó un método de negocio expuesto con `NotImplemented` o `UnsupportedOperationException`.

## 16. Matriz de trazabilidad y glosario

### Matriz de trazabilidad de endpoints

| Funcionalidad | Endpoint | Controller | Service | Repository | Entidades |
| --- | --- | --- | --- | --- | --- |
| Marca un adjunto como DELETED, registra historial y elimina el objeto local. | /api/v1/attachments/{attachment_id} | app/modules/adjuntos/api/router.py | AttachmentService | AttachmentRepository | Attachment, Ticket, TicketComment, TicketHistory |
| Descarga el binario con Content-Disposition attachment, no-store y MIME detectad | /api/v1/attachments/{attachment_id}/download | app/modules/adjuntos/api/router.py | AttachmentService | AttachmentRepository | Attachment, Ticket, TicketComment, TicketHistory |
| Consulta auditoría global append-only con paginación y filtros. | /api/v1/audit | app/modules/auditoria/api/router.py | AuditRepository (consulta) | AuditRepository | AuditLog |
| Cambia la contraseña con la contraseña actual y revoca sesiones anteriores. | /api/v1/auth/change-password | app/modules/usuarios/api/auth_router.py | AuthService | UserRepository / AuthRepository / UserProfileRepository | User, Role, AuthSession, AuthToken, AuthRateLimit, UserPreference, UserProfileAudit |
| Solicita recuperación sin revelar si el correo existe. | /api/v1/auth/forgot-password | app/modules/usuarios/api/auth_router.py | AuthService | UserRepository / AuthRepository / UserProfileRepository | User, Role, AuthSession, AuthToken, AuthRateLimit, UserPreference, UserProfileAudit |
| Valida credenciales y emite un access token JWT vinculado a una sesión persisten | /api/v1/auth/login | app/modules/usuarios/api/auth_router.py | AuthService | UserRepository / AuthRepository / UserProfileRepository | User, Role, AuthSession, AuthToken, AuthRateLimit, UserPreference, UserProfileAudit |
| Revoca la sesión asociada al JWT actual. | /api/v1/auth/logout | app/modules/usuarios/api/auth_router.py | AuthService | UserRepository / AuthRepository / UserProfileRepository | User, Role, AuthSession, AuthToken, AuthRateLimit, UserPreference, UserProfileAudit |
| Devuelve la identidad pública del usuario autenticado. | /api/v1/auth/me | app/modules/usuarios/api/auth_router.py | AuthService | UserRepository / AuthRepository / UserProfileRepository | User, Role, AuthSession, AuthToken, AuthRateLimit, UserPreference, UserProfileAudit |
| Registra una cuenta nueva y siempre asigna el rol CLIENTE. | /api/v1/auth/register | app/modules/usuarios/api/auth_router.py | AuthService | UserRepository / AuthRepository / UserProfileRepository | User, Role, AuthSession, AuthToken, AuthRateLimit, UserPreference, UserProfileAudit |
| Consume un token de recuperación de un solo uso y revoca sesiones previas. | /api/v1/auth/reset-password | app/modules/usuarios/api/auth_router.py | AuthService | UserRepository / AuthRepository / UserProfileRepository | User, Role, AuthSession, AuthToken, AuthRateLimit, UserPreference, UserProfileAudit |
| Consume un token de verificación de correo de un solo uso. | /api/v1/auth/verify-email | app/modules/usuarios/api/auth_router.py | AuthService | UserRepository / AuthRepository / UserProfileRepository | User, Role, AuthSession, AuthToken, AuthRateLimit, UserPreference, UserProfileAudit |
| Lista categorías activas. | /api/v1/categories | app/modules/conocimiento/api/router.py | CategoryService | CategoryRepository | TicketCategory |
| Crea una categoría de tickets. | /api/v1/categories | app/modules/conocimiento/api/router.py | CategoryService | CategoryRepository | TicketCategory |
| Actualiza nombre o descripción de una categoría. | /api/v1/categories/{category_id} | app/modules/conocimiento/api/router.py | CategoryService | CategoryRepository | TicketCategory |
| Activa o desactiva una categoría; las categorías usadas por tickets no pueden de | /api/v1/categories/{category_id}/status | app/modules/conocimiento/api/router.py | CategoryService | CategoryRepository | TicketCategory |
| Crea una conversación anónima o asociada al cliente autenticado. | /api/v1/chat/conversations | app/modules/chatbot/api/router.py | ChatbotService / TicketService | ConversationRepository / FAQRepository / TicketRepository | Conversation, ChatMessage, FAQ, FAQFeedback, Ticket |
| Devuelve conversación y mensajes si el visitante o cliente está autorizado. | /api/v1/chat/conversations/{conversation_id} | app/modules/chatbot/api/router.py | ChatbotService / TicketService | ConversationRepository / FAQRepository / TicketRepository | Conversation, ChatMessage, FAQ, FAQFeedback, Ticket |
| Convierte una conversación autenticada no resuelta en un ticket único. | /api/v1/chat/conversations/{conversation_id}/convert-to-ticket | app/modules/chatbot/api/router.py | ChatbotService / TicketService | ConversationRepository / FAQRepository / TicketRepository | Conversation, ChatMessage, FAQ, FAQFeedback, Ticket |
| Deriva una conversación a atención humana de forma idempotente. | /api/v1/chat/conversations/{conversation_id}/escalate | app/modules/chatbot/api/router.py | ChatbotService / TicketService | ConversationRepository / FAQRepository / TicketRepository | Conversation, ChatMessage, FAQ, FAQFeedback, Ticket |
| Registra utilidad y aceptación de escalamiento sobre la última FAQ usada. | /api/v1/chat/conversations/{conversation_id}/feedback | app/modules/chatbot/api/router.py | ChatbotService / TicketService | ConversationRepository / FAQRepository / TicketRepository | Conversation, ChatMessage, FAQ, FAQFeedback, Ticket |
| Asocia una conversación anónima con el cliente autenticado. | /api/v1/chat/conversations/{conversation_id}/link-user | app/modules/chatbot/api/router.py | ChatbotService / TicketService | ConversationRepository / FAQRepository / TicketRepository | Conversation, ChatMessage, FAQ, FAQFeedback, Ticket |
| Procesa un mensaje mediante recuperación FAQ determinista y fallback/IA opcional | /api/v1/chat/conversations/{conversation_id}/messages | app/modules/chatbot/api/router.py | ChatbotService / TicketService | ConversationRepository / FAQRepository / TicketRepository | Conversation, ChatMessage, FAQ, FAQFeedback, Ticket |
| Reinicia el contexto operativo sin borrar mensajes históricos. | /api/v1/chat/conversations/{conversation_id}/reset | app/modules/chatbot/api/router.py | ChatbotService / TicketService | ConversationRepository / FAQRepository / TicketRepository | Conversation, ChatMessage, FAQ, FAQFeedback, Ticket |
| Lista FAQs publicadas, activas, vigentes y de categorías activas; soporta filtro | /api/v1/faqs | app/modules/conocimiento/api/router.py | FAQService | FAQRepository / CategoryRepository | FAQ, FAQVersion, FAQFeedback, TicketCategory |
| Crea contenido de conocimiento como DRAFT inactivo y registra su primera versión | /api/v1/faqs | app/modules/conocimiento/api/router.py | FAQService | FAQRepository / CategoryRepository | FAQ, FAQVersion, FAQFeedback, TicketCategory |
| Lista el catálogo editorial con filtros de estado, contenido y fechas. | /api/v1/faqs/admin | app/modules/conocimiento/api/router.py | FAQService | FAQRepository / CategoryRepository | FAQ, FAQVersion, FAQFeedback, TicketCategory |
| Devuelve métricas agregadas de utilidad del feedback de FAQs. | /api/v1/faqs/admin/metrics/utility | app/modules/conocimiento/api/router.py | FAQService | FAQRepository / CategoryRepository | FAQ, FAQVersion, FAQFeedback, TicketCategory |
| Obtiene una FAQ pública activa. | /api/v1/faqs/{faq_id} | app/modules/conocimiento/api/router.py | FAQService | FAQRepository / CategoryRepository | FAQ, FAQVersion, FAQFeedback, TicketCategory |
| Actualiza una FAQ; si una FAQ publicada cambia contenido vuelve a REVIEW e incre | /api/v1/faqs/{faq_id} | app/modules/conocimiento/api/router.py | FAQService | FAQRepository / CategoryRepository | FAQ, FAQVersion, FAQFeedback, TicketCategory |
| Registra feedback anónimo o de un cliente sobre una FAQ activa. | /api/v1/faqs/{faq_id}/feedback | app/modules/conocimiento/api/router.py | FAQService | FAQRepository / CategoryRepository | FAQ, FAQVersion, FAQFeedback, TicketCategory |
| Consulta snapshots editoriales de una FAQ. | /api/v1/faqs/{faq_id}/history | app/modules/conocimiento/api/router.py | FAQService | FAQRepository / CategoryRepository | FAQ, FAQVersion, FAQFeedback, TicketCategory |
| Atajo para publicar o archivar una FAQ mediante is_active. | /api/v1/faqs/{faq_id}/status | app/modules/conocimiento/api/router.py | FAQService | FAQRepository / CategoryRepository | FAQ, FAQVersion, FAQFeedback, TicketCategory |
| Aplica transiciones editoriales DRAFT, REVIEW, PUBLISHED o ARCHIVED. | /api/v1/faqs/{faq_id}/workflow | app/modules/conocimiento/api/router.py | FAQService | FAQRepository / CategoryRepository | FAQ, FAQVersion, FAQFeedback, TicketCategory |
| Comprobación estática de disponibilidad de la API. | /api/v1/health | app/api/v1/router.py | - | - | - |
| Lista notificaciones propias paginadas, ordenadas por created_at e id descendent | /api/v1/notifications | app/modules/notificaciones/api/router.py | NotificationService | NotificationRepository | Notification, UserPreference |
| Marca como leídas todas las notificaciones propias y devuelve el contador actual | /api/v1/notifications/read-all | app/modules/notificaciones/api/router.py | NotificationService | NotificationRepository | Notification, UserPreference |
| Cuenta notificaciones no leídas del usuario actual. | /api/v1/notifications/unread-count | app/modules/notificaciones/api/router.py | NotificationService | NotificationRepository | Notification, UserPreference |
| Marca como leída una notificación propia. | /api/v1/notifications/{notification_id}/read | app/modules/notificaciones/api/router.py | NotificationService | NotificationRepository | Notification, UserPreference |
| Agrupa tickets por categoría. | /api/v1/reports/by-category | app/modules/reportes/api/router.py | ReportService | ReportRepository | Ticket, ReportExportAudit |
| Agrupa tickets por prioridad. | /api/v1/reports/by-priority | app/modules/reportes/api/router.py | ReportService | ReportRepository | Ticket, ReportExportAudit |
| Agrupa tickets por estado. | /api/v1/reports/by-status | app/modules/reportes/api/router.py | ReportService | ReportRepository | Ticket, ReportExportAudit |
| Devuelve cantidad resuelta y promedio de tiempo de resolución. | /api/v1/reports/resolution-time | app/modules/reportes/api/router.py | ReportService | ReportRepository | Ticket, ReportExportAudit |
| Resumen de tickets por estado y promedio de resolución. | /api/v1/reports/summary | app/modules/reportes/api/router.py | ReportService | ReportRepository | Ticket, ReportExportAudit |
| Genera exportación CSV de un reporte permitido y audita el intento. | /api/v1/reports/{report_name}/export | app/modules/reportes/api/router.py | ReportService | ReportRepository | Ticket, ReportExportAudit |
| Evalúa bajo demanda advertencias y vencimientos de SLA idempotentemente. | /api/v1/sla/evaluate | app/modules/tickets/api/sla_router.py | SlaService | SlaRepository | TicketSla, SlaPolicy, SlaPolicyHistory |
| Lista políticas SLA persistentes. | /api/v1/sla/policies | app/modules/tickets/api/sla_router.py | SlaService | SlaRepository | TicketSla, SlaPolicy, SlaPolicyHistory |
| Crea política SLA por prioridad, categoría y origen. | /api/v1/sla/policies | app/modules/tickets/api/sla_router.py | SlaService | SlaRepository | TicketSla, SlaPolicy, SlaPolicyHistory |
| Actualiza una política SLA y conserva historial de valores anteriores/nuevos. | /api/v1/sla/policies/{policy_id} | app/modules/tickets/api/sla_router.py | SlaService | SlaRepository | TicketSla, SlaPolicy, SlaPolicyHistory |
| Lista tickets para personal interno; los asesores quedan limitados a tickets asi | /api/v1/tickets | app/modules/tickets/api/router.py | TicketService | TicketRepository | Ticket, TicketAssignment, TicketComment, TicketHistory, TicketSla |
| Crea ticket manual con código TCK, prioridad y estado NUEVO; inicia SLA e histor | /api/v1/tickets | app/modules/tickets/api/router.py | TicketService | TicketRepository | Ticket, TicketAssignment, TicketComment, TicketHistory, TicketSla |
| Lista únicamente los tickets del cliente actual; modo legacy sin paginación o mo | /api/v1/tickets/mine | app/modules/tickets/api/router.py | TicketService | TicketRepository | Ticket, TicketAssignment, TicketComment, TicketHistory, TicketSla |
| Consulta bandejas operativas paginadas por cola, SLA, asignación o actividad rec | /api/v1/tickets/operations | app/modules/tickets/api/router.py | TicketService | TicketRepository | Ticket, TicketAssignment, TicketComment, TicketHistory, TicketSla |
| Obtiene un ticket si el cliente es propietario, el asesor está asignado o el act | /api/v1/tickets/{ticket_id} | app/modules/tickets/api/router.py | TicketService | TicketRepository | Ticket, TicketAssignment, TicketComment, TicketHistory, TicketSla |
| Asigna o reasigna un ticket a un asesor activo. | /api/v1/tickets/{ticket_id}/assignments | app/modules/tickets/api/router.py | TicketService | TicketRepository | Ticket, TicketAssignment, TicketComment, TicketHistory, TicketSla |
| Lista metadatos de adjuntos activos del ticket. | /api/v1/tickets/{ticket_id}/attachments | app/modules/adjuntos/api/router.py | AttachmentService | AttachmentRepository | Attachment, Ticket, TicketComment, TicketHistory |
| Carga un archivo multipart asociado a un ticket y opcionalmente a un comentario. | /api/v1/tickets/{ticket_id}/attachments | app/modules/adjuntos/api/router.py | AttachmentService | AttachmentRepository | Attachment, Ticket, TicketComment, TicketHistory |
| Cancela un ticket con motivo; la operación está reservada al supervisor. | /api/v1/tickets/{ticket_id}/cancel | app/modules/tickets/api/router.py | TicketService | TicketRepository | Ticket, TicketAssignment, TicketComment, TicketHistory, TicketSla |
| Cierra un ticket mediante una transición válida. | /api/v1/tickets/{ticket_id}/close | app/modules/tickets/api/router.py | TicketService | TicketRepository | Ticket, TicketAssignment, TicketComment, TicketHistory, TicketSla |
| Lista comentarios autorizados en orden de creación. | /api/v1/tickets/{ticket_id}/comments | app/modules/tickets/api/router.py | TicketService | TicketRepository | Ticket, TicketAssignment, TicketComment, TicketHistory, TicketSla |
| Agrega comentario, historial, primera respuesta SLA y notificación al interlocut | /api/v1/tickets/{ticket_id}/comments | app/modules/tickets/api/router.py | TicketService | TicketRepository | Ticket, TicketAssignment, TicketComment, TicketHistory, TicketSla |
| Devuelve el historial de mutaciones del ticket. | /api/v1/tickets/{ticket_id}/history | app/modules/tickets/api/router.py | TicketService | TicketRepository | Ticket, TicketAssignment, TicketComment, TicketHistory, TicketSla |
| Libera asignación propia del asesor o una asignación que gestiona un supervisor. | /api/v1/tickets/{ticket_id}/release | app/modules/tickets/api/router.py | TicketService | TicketRepository | Ticket, TicketAssignment, TicketComment, TicketHistory, TicketSla |
| Reabre un ticket RESUELTO hacia EN_PROCESO y exige motivo. | /api/v1/tickets/{ticket_id}/reopen | app/modules/tickets/api/router.py | TicketService | TicketRepository | Ticket, TicketAssignment, TicketComment, TicketHistory, TicketSla |
| Consulta el registro SLA y la política aplicada al ticket autorizado. | /api/v1/tickets/{ticket_id}/sla | app/modules/tickets/api/sla_router.py | SlaService | SlaRepository | TicketSla, SlaPolicy, SlaPolicyHistory |
| Pausa manualmente un SLA activo con motivo e historial. | /api/v1/tickets/{ticket_id}/sla/pause | app/modules/tickets/api/sla_router.py | SlaService | SlaRepository | TicketSla, SlaPolicy, SlaPolicyHistory |
| Reanuda SLA pausado y extiende vencimientos por el tiempo pausado. | /api/v1/tickets/{ticket_id}/sla/resume | app/modules/tickets/api/sla_router.py | SlaService | SlaRepository | TicketSla, SlaPolicy, SlaPolicyHistory |
| Cambia el estado con la matriz de transiciones válida y registra historial/notif | /api/v1/tickets/{ticket_id}/status | app/modules/tickets/api/router.py | TicketService | TicketRepository | Ticket, TicketAssignment, TicketComment, TicketHistory, TicketSla |
| Permite a un asesor tomar un ticket abierto sin asignación. | /api/v1/tickets/{ticket_id}/take | app/modules/tickets/api/router.py | TicketService | TicketRepository | Ticket, TicketAssignment, TicketComment, TicketHistory, TicketSla |
| Lista asesores activos para selección operativa. | /api/v1/users/advisors | app/modules/usuarios/api/user_router.py | UserService / UserProfileService | UserRepository / AuthRepository / UserProfileRepository | User, Role, AuthSession, AuthToken, AuthRateLimit, UserPreference, UserProfileAudit |
| Obtiene o crea preferencias persistentes del usuario. | /api/v1/users/me/preferences | app/modules/usuarios/api/user_router.py | UserService / UserProfileService | UserRepository / AuthRepository / UserProfileRepository | User, Role, AuthSession, AuthToken, AuthRateLimit, UserPreference, UserProfileAudit |
| Actualiza preferencias de notificación, idioma y zona horaria. | /api/v1/users/me/preferences | app/modules/usuarios/api/user_router.py | UserService / UserProfileService | UserRepository / AuthRepository / UserProfileRepository | User, Role, AuthSession, AuthToken, AuthRateLimit, UserPreference, UserProfileAudit |
| Consulta el perfil público ampliado del usuario actual. | /api/v1/users/me/profile | app/modules/usuarios/api/user_router.py | UserService / UserProfileService | UserRepository / AuthRepository / UserProfileRepository | User, Role, AuthSession, AuthToken, AuthRateLimit, UserPreference, UserProfileAudit |
| Actualiza únicamente full_name y phone del propio usuario. | /api/v1/users/me/profile | app/modules/usuarios/api/user_router.py | UserService / UserProfileService | UserRepository / AuthRepository / UserProfileRepository | User, Role, AuthSession, AuthToken, AuthRateLimit, UserPreference, UserProfileAudit |

### Dependencias principales

| Dependencia | Versión | Finalidad |
| --- | --- | --- |
| fastapi[standard] | >=0.115,<1.0 | API y servidor de desarrollo/ejecución |
| sqlmodel | >=0.0.22,<1.0 | ORM/modelos |
| alembic | >=1.13,<2.0 | Migraciones |
| PyMySQL | >=1.1,<2.0 | Conector MySQL |
| PyJWT | >=2.9,<3.0 | Tokens JWT |
| pwdlib[argon2] | >=0.2,<1.0 | Hash de contraseñas |
| httpx | >=0.27,<1.0 dev | Cliente de pruebas/TestClient |
| pytest | >=8.0,<9.0 dev | Pruebas |
| ruff | >=0.8,<1.0 dev | Lint y formato |

### Glosario

- **CLIENTE:** rol que crea tickets propios y usa el chatbot.
- **ASESOR:** rol interno que atiende tickets asignados.
- **SUPERVISOR:** rol interno con administración global.
- **FAQ:** contenido de conocimiento publicado para el chatbot y consulta pública.
- **SLA:** registro de tiempos de primera respuesta y resolución asociado a un ticket.
- **RAG local:** recuperación determinista de FAQs autorizadas usada como contexto opcional para IA.
- **Noop provider:** adaptador local que no ejecuta entrega externa.
- **Dedupe key:** clave única para evitar duplicar auditorías/notificaciones.
- **expected_version:** control optimista para evitar sobrescribir mutaciones concurrentes.

### Conclusión técnica

El backend actual es un prototipo modular funcional de gestión de tickets con autenticación, conocimiento FAQ, chatbot determinista, operación interna, SLA, adjuntos locales, notificaciones in-app, reportes CSV y trazabilidad. La evidencia del repositorio muestra que la base está preparada para adaptadores externos, pero estos no están activos por defecto. Esta conclusión refleja el estado del código auditado y no una hoja de ruta futura.

### Resumen de auditoría

| Métrica | Cantidad / resultado |
| --- | --- |
| Módulos | 8 |
| Endpoints HTTP | 73 |
| Entidades persistentes | 24 |
| Repositories | 12 |
| Archivos de servicios | 14 |
| Roles | 3 |
| Integraciones externas activas | 0 |
| Puertos opcionales de integración IA | 1 |
| Adaptadores IA disponibles | 2 |
| Archivos de prueba | 23 |
| Funciones de prueba definidas | 181 |
| Casos recogidos por Pytest | 219 |
| Archivos generados | docs/DOCUMENTACION_OFICIAL_BACKEND.md; output/pdf/DOCUMENTACION_OFICIAL_BACKEND.pdf |
