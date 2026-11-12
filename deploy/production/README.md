# Despliegue en Contabo

Este perfil es independiente del `docker-compose.yml` de desarrollo. Solo Caddy
publica puertos; MySQL no tiene puerto público, el API solo es accesible dentro
de Docker y el seed demo nunca se ejecuta.

## Antes de levantar servicios

1. Confirma en Cloudflare que `api.felixtintaya.com` apunta a la IPv4 de Contabo
   como **DNS only**. El registro debe resolver a `217.77.6.124`.
2. UFW debe permitir TCP `22`, `80` y `443`. No abras `3306` ni `8000`.
3. En `/opt/gnb-backend`, actualiza `main` al commit que contiene estos archivos.
4. Crea el archivo privado de variables, sin pegar sus valores en chats:

   ```bash
   cd /opt/gnb-backend
   cp .env.production.example .env.production
   chmod 600 .env.production
   nano .env.production
   ```

   Genera cada secreto localmente con `openssl rand -hex 32`; usa valores
   distintos para `MYSQL_PASSWORD`, `MYSQL_ROOT_PASSWORD` y `SECRET_KEY`. Esos
   valores hexadecimales también evitan problemas de escape en `DATABASE_URL`.
   Completa `SMTP_USERNAME`, `SMTP_PASSWORD` y `SMTP_FROM_EMAIL` con el login,
   la clave SMTP y el remitente que ya verificaste en Brevo. No uses la API key
   de Brevo en `SMTP_PASSWORD`.

5. Comprueba que Compose puede interpolar la configuración sin mostrarla:

   ```bash
   docker compose --env-file .env.production -f deploy/production/compose.yml config --quiet
   ```

## Primer arranque

Ejecuta desde `/opt/gnb-backend`. La primera fase levanta solo MySQL y aplica
migraciones; todavía no publica el API en Internet.

```bash
docker compose --env-file .env.production -f deploy/production/compose.yml up -d db
docker compose --env-file .env.production -f deploy/production/compose.yml run --rm migrate
docker compose --profile bootstrap --env-file .env.production -f deploy/production/compose.yml run --rm --no-deps bootstrap python -m app.seed.production_bootstrap --email TU_CORREO --name "Tu nombre completo"
docker compose --env-file .env.production -f deploy/production/compose.yml up -d api caddy
```

El bootstrap pide la contraseña en la terminal, la valida con la política de
cuentas y crea un Supervisor, roles, categorías, políticas SLA y las FAQs
curadas. No crea cuentas demo, tickets, conversaciones ni datos operativos.
Solo ejecútalo una vez; no reemplaza ni restablece cuentas existentes.

Caddy obtiene y renueva HTTPS automáticamente. La emisión necesita DNS correcto
y acceso entrante TCP a `80` y `443`; por eso el registro de API debe quedar
DNS-only durante la configuración inicial.

## Comprobación

```bash
docker compose --env-file .env.production -f deploy/production/compose.yml ps
curl -fsS https://api.felixtintaya.com/api/v1/health/ready
docker compose --env-file .env.production -f deploy/production/compose.yml logs --tail=100 api caddy
```

La documentación interactiva `/docs` y `/openapi.json` queda deshabilitada en
producción. El frontend debe usar `https://api.felixtintaya.com` como API base.

## Actualizaciones

Antes de cada actualización, crea un respaldo de MySQL y verifica que tienes
acceso SSH. Luego:

```bash
cd /opt/gnb-backend
git pull --ff-only origin main
docker compose --env-file .env.production -f deploy/production/compose.yml build
docker compose --env-file .env.production -f deploy/production/compose.yml run --rm migrate
docker compose --env-file .env.production -f deploy/production/compose.yml up -d api caddy
```

No ejecutes `docker compose up` desde la raíz con el Compose de desarrollo: ese
perfil publica puertos de desarrollo y carga usuarios/tickets demo.

## OpenAI

El perfil inicial deja el proveedor IA apagado. Para habilitarlo después de
probar SMTP y el API, coloca la clave en `OPENAI_API_KEY` dentro del archivo
privado, cambia `CHATBOT_AI_ENABLED=true` y `CHATBOT_AI_PROVIDER=openai`, y
recrea el servicio API. Configura además un límite de gasto en OpenAI. No
incluyas la clave en el repositorio, imágenes, argumentos de comandos o logs.

## Persistencia y respaldo

MySQL, adjuntos y certificados usan volúmenes Docker persistentes. La copia
automática contratada en Contabo protege el VPS según su política, pero no
sustituye un volcado lógico de MySQL que puedas restaurar. Antes de actualizar,
respalda la base y verifica periódicamente una restauración aislada.
