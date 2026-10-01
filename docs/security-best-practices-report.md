# Informe de hardening y prácticas de seguridad

Fecha de revisión: 2026-10-01
Alcance: backend FastAPI, MySQL, correo SMTP, adjuntos, Dockerfile y Docker
Compose del prototipo académico.

## Resumen ejecutivo

No se identificaron hallazgos críticos o altos abiertos en el alcance revisado.
Se corrigieron y verificaron el saneamiento de errores, la selección segura de
SMTP, el aislamiento del contenedor y la configuración CORS. Los adjuntos tienen
controles preventivos sólidos, aunque un despliegue real todavía necesitaría
cuarentena o análisis antimalware.

Persisten riesgos de infraestructura que este repositorio local no puede cerrar
por sí solo: TLS y límites globales en el borde, gestión externa de secretos y
reproducibilidad completa de la cadena de suministro. No deben interpretarse
como controles implementados por Compose.

Referencias principales: [Docker Compose services](https://docs.docker.com/reference/compose-file/services/),
[orden de arranque de Compose](https://docs.docker.com/compose/how-tos/startup-order/),
[OWASP REST Security](https://cheatsheetseries.owasp.org/cheatsheets/REST_Security_Cheat_Sheet.html),
[OWASP File Upload](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html)
y [OWASP TLS](https://cheatsheetseries.owasp.org/cheatsheets/Transport_Layer_Security_Cheat_Sheet.html).

## Hallazgos corregidos y verificados

### SEC-ERR-001 — Respuestas internas potencialmente sensibles

- **Rule ID:** FASTAPI-DEPLOY-002 / OWASP REST error handling.
- **Severity:** High.
- **Estado:** Corregido y verificado.
- **Location:** `app/shared/api_errors.py:17`, `app/shared/api_errors.py:178`,
  `app/main.py:31`, `app/main.py:52`.
- **Evidence:** el request ID usa una expresión regular estricta; el handler de
  excepciones inesperadas registra solo el identificador y responde un mensaje
  genérico. Pruebas reales comprueban que SQL, DSN, trazas y secretos no aparecen
  en 500/503.
- **Impact:** sin este control, un fallo podría revelar estructura SQL,
  credenciales o detalles de implementación al cliente.
- **Fix:** contrato único `{code,message,details,request_id}`, handlers globales,
  validación de `X-Request-ID` y fallo de arranque si `DEBUG=true` en
  staging/producción (`app/core/config.py:149`).
- **Mitigation:** conservar logs operativos restringidos y correlacionarlos por
  request ID, sin incluir cuerpos ni credenciales.
- **False positive notes:** un proxy puede agregar su propio identificador; debe
  preservar o mapear el de la aplicación sin aceptar valores arbitrarios.

### SEC-SMTP-002 — Uso accidental de Noop o exposición de credenciales SMTP

- **Rule ID:** SECRET-MGMT-001.
- **Severity:** High.
- **Estado:** Corregido en aplicación; gestión externa pendiente para producción.
- **Location:** `app/core/config.py:22`, `app/core/config.py:156`,
  `app/modules/usuarios/services/email_provider.py:34`,
  `app/modules/usuarios/services/email_provider.py:79`.
- **Evidence:** `SMTP_PASSWORD` es `SecretStr`; staging/producción rechazan Noop,
  SMTP plano, configuración incompleta y combinaciones TLS inválidas. TLS
  implícito y STARTTLS usan `ssl.create_default_context()` para verificar
  certificado y hostname. El transporte aplica timeout y no registra mensajes,
  tokens ni credenciales.
- **Impact:** una selección implícita de Noop impediría notificaciones reales; el
  logging de mensajes o credenciales expondría tokens de un solo uso.
- **Fix:** factoría dependiente del entorno, validación de arranque y pruebas con
  proveedor fake y transportes SMTP sustituidos.
- **Mitigation:** inyectar secretos en runtime desde el gestor aprobado, rotarlos
  y restringir acceso a inspección del host.
- **False positive notes:** las variables vacías de `.env.example` son
  marcadores locales, no credenciales reales.

### SEC-UPLOAD-003 — Carga y distribución insegura de adjuntos

- **Rule ID:** FASTAPI-UPLOAD-001 / FASTAPI-FILES-001.
- **Severity:** Medium.
- **Estado:** Mitigado; análisis antimalware pendiente.
- **Location:** `app/modules/adjuntos/services/attachment_service.py:101`,
  `app/modules/adjuntos/services/attachment_service.py:144`,
  `app/modules/adjuntos/repositories/attachment_repository.py:16`,
  `app/modules/adjuntos/services/storage_provider.py:106`,
  `app/modules/adjuntos/api/router.py:89`.
- **Evidence:** existen límites por archivo, cuota y cantidad; allowlists de
  extensión/MIME, detección por firma, clave física UUID no controlada por el
  usuario, resolución confinada al directorio base, RBAC y descarga como
  `attachment`. Los estados terminales rechazan carga y eliminación, y el
  estado se revalida con bloqueo de fila antes de la mutación persistente.
- **Impact:** un archivo permitido aún puede contener contenido malicioso para
  aplicaciones cliente, especialmente PDF o imágenes complejas.
- **Fix:** controles aplicados en servicio y almacenamiento; volumen persistente
  separado de la raíz de solo lectura.
- **Mitigation:** para producción, agregar cuarentena y análisis antimalware antes
  de marcar un archivo disponible; aplicar también límite de cuerpo en el proxy.
- **False positive notes:** el backend no ejecuta ni sirve estáticamente los
  adjuntos y excluye HTML, SVG, scripts y ejecutables, lo cual reduce pero no
  elimina el riesgo residual.

### SEC-CTR-004 — Contenedor con privilegios y almacenamiento efímero

- **Rule ID:** DOCKER-RUNTIME-001.
- **Severity:** High.
- **Estado:** Corregido y verificado en runtime.
- **Location:** `Dockerfile:6`, `Dockerfile:19`, `Dockerfile:22`,
  `docker-compose.yml:29`, `docker-compose.yml:32`,
  `docker-compose.yml:34`, `docker-compose.yml:83`.
- **Evidence:** el proceso efectivo es UID/GID 10001; la raíz es read-only,
  `/tmp` es tmpfs con `noexec,nosuid`, `no-new-privileges:true`, `CapDrop=[ALL]`
  e init está activo. `/app/var/uploads` está respaldado por
  `backend_attachments_data`, es escribible por UID 10001 y sobrevivió una
  recreación del contenedor.
- **Impact:** ejecutar como root o conservar adjuntos en la capa efímera aumenta
  el impacto de una explotación y provoca pérdida de datos al recrear API.
- **Fix:** usuario fijo no root, permisos previos al montaje, volumen nombrado,
  capacidades eliminadas y healthcheck contra readiness.
- **Mitigation:** conservar el perfil seccomp predeterminado, limitar recursos en
  la plataforma final y no montar el socket de Docker.
- **False positive notes:** root durante `pip install` pertenece únicamente a la
  fase de construcción; `Config.User` y el proceso runtime son `app:app`.

### SEC-CORS-005 — CORS permisivo o cabecera de correlación inaccesible

- **Rule ID:** FASTAPI-CORS-001 / FASTAPI-HOST-001.
- **Severity:** Medium.
- **Estado:** Corregido y verificado.
- **Location:** `app/main.py:31`, `app/main.py:42`, `app/main.py:52`.
- **Evidence:** `TrustedHostMiddleware` valida Host; CORS usa lista explícita,
  `allow_credentials=False`, métodos/cabeceras definidos y expone únicamente
  `X-Request-ID` adicionalmente a las cabeceras simples.
- **Impact:** orígenes reflejados o comodines con credenciales ampliarían el
  alcance de ataques desde navegadores.
- **Fix:** configuración central, valores por entorno y pruebas de preflight y
  exposición de cabecera.
- **Mitigation:** en producción, limitar `CORS_ORIGINS` y `TRUSTED_HOSTS` a
  dominios concretos, sin comodines.
- **False positive notes:** CORS no reemplaza autenticación ni autorización; la
  API usa Bearer y RBAC en servidor.

## Riesgos residuales abiertos

### SEC-EDGE-006 — Controles de borde no incluidos

- **Rule ID:** FASTAPI-LIMITS-001 / TLS-001 / RATE-LIMIT-001.
- **Severity:** Medium.
- **Location:** `docker-compose.yml:80` y ausencia de proxy/gateway en el
  repositorio.
- **Evidence:** Compose publica HTTP en el puerto 8000 directamente. La aplicación
  limita adjuntos y algunos flujos de autenticación/chatbot, pero no existe un
  límite global de cuerpo, conexiones o solicitudes.
- **Impact:** un despliegue directo en Internet expondría tokens y credenciales
  sin TLS y sería más vulnerable a consumo de memoria, conexiones o tráfico.
- **Fix:** desplegar detrás de un proxy/gateway aprobado con TLS, límites de
  cuerpo, timeouts, concurrencia y rate limiting; publicar MySQL solo cuando sea
  necesario para desarrollo.
- **Mitigation:** los timeouts de MySQL/readiness, límites por adjunto y límites
  funcionales reducen parte del riesgo, pero no sustituyen el borde.
- **False positive notes:** si la plataforma ya provee TLS/WAF/API gateway, este
  hallazgo se cierra verificando su configuración efectiva; no es visible aquí.

### SEC-SECRETS-007 — Secretos de producción mediante variables de entorno

- **Rule ID:** SECRET-MGMT-002.
- **Severity:** Medium.
- **Location:** `docker-compose.yml:6` y `docker-compose.yml:14`.
- **Evidence:** Compose local inyecta `SECRET_KEY` y `SMTP_PASSWORD` como entorno,
  que puede ser inspeccionado por usuarios con privilegios sobre Docker.
- **Impact:** una cuenta con acceso al daemon podría recuperar secretos runtime y
  firmar tokens o usar el relay SMTP.
- **Fix:** en staging/producción usar el gestor de secretos de la plataforma o
  secretos montados como archivo con permisos mínimos, adaptando Settings para
  lectura `_FILE` si la plataforma lo requiere.
- **Mitigation:** Settings rechaza la clave de desarrollo, Noop y debug fuera de
  local/test; `.env` y variantes se excluyen del contexto Docker y de Git.
- **False positive notes:** Docker Compose se declara flujo local; acceso al
  daemon ya implica privilegios altos, pero sigue siendo una superficie de
  exposición relevante.

### SEC-SUPPLY-008 — Build no completamente reproducible

- **Rule ID:** FASTAPI-SUPPLY-001.
- **Severity:** Low.
- **Location:** `Dockerfile:1` y `pyproject.toml:12`.
- **Evidence:** la imagen base usa una etiqueta sin digest fijado y las
  dependencias tienen rangos compatibles, sin lockfile con hashes.
- **Impact:** reconstrucciones futuras pueden resolver artefactos distintos y
  ampliar el riesgo de regresiones o cadena de suministro.
- **Fix:** generar un lockfile aprobado con hashes, SBOM y análisis de imagen;
  fijar un digest solo después de validarlo y establecer un proceso de
  actualización deliberado.
- **Mitigation:** los rangos limitan versiones mayores, la construcción fresca
  quedó probada y el plan prohíbe inventar un digest no verificado.
- **False positive notes:** fijar versiones sin un proceso periódico de
  actualización también puede congelar vulnerabilidades; pinning y renovación
  deben diseñarse juntos.

## Evidencia de verificación

- Build sin caché de `ticket-management-backend:local`: correcto.
- Orden `db healthy → migrate exit 0 → seed exit 0 → api healthy`: correcto.
- Readiness real contra MySQL: HTTP 200.
- UID/GID runtime: 10001/10001.
- `ReadonlyRootfs=true`, `Init=true`, `SecurityOpt=[no-new-privileges:true]`,
  `CapDrop=[ALL]`.
- Persistencia de adjuntos tras recrear API: comprobada y sonda eliminada.
- Migración `0017 → 0018` validada en MySQL 8.4 desechable con ticket
  inconsistente y asignación activa; reparación, restricción, auditoría,
  historial e idempotencia comprobados; contenedor temporal eliminado.
- Smoke HTTP Cliente/Asesor/Supervisor: correcto; no se imprimieron tokens.
