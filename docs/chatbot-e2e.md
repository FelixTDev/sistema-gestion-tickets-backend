# E2E local del chatbot

El flujo de Playwright comprueba la integración entre el navegador, el API y una
base SQLite desechable. Usa únicamente el catálogo de FAQ sembrado con datos
ficticios, mantiene la IA deshabilitada, no necesita OpenAI y no ejecuta
migraciones. El servidor del API y Vite se inician desde un directorio temporal
nuevo; el runner comprueba que ese directorio no contenga `.env` sin abrir ese
archivo. Al terminar, detiene ambos procesos y elimina solo ese directorio
temporal que creó para la corrida.

## Preparación y ejecución

Requisitos: dependencias instaladas en el frontend (`npm ci`), el entorno Python
del backend (`backend\.venv`) y Chromium de Playwright. Si Chromium aún no está
instalado, desde `frontend` ejecutar una vez:

```powershell
npm run e2e:install
```

Cada corrida se ejecuta desde `frontend`:

```powershell
npm run e2e
```

El script usa el `backend\.venv` local y crea una SQLite con nombre aleatorio
bajo el directorio temporal del sistema. El usuario de prueba es
`cliente@demo.com`; su clave aleatoria se genera en memoria para cada corrida y
solo se usa contra esa base temporal. No se conecta a MySQL, no lee `.env` y
configura `APP_ENV=development`,
`CHATBOT_AI_ENABLED=false`, `CHATBOT_AI_PROVIDER=noop`, claves vacías y correo
`noop` en los procesos del backend.

## Comprobaciones cubiertas

- Bienvenida del widget, CTA del menú, selección de categoría y subcategoría,
  navegación hacia atrás, vuelta al menú y restauración de estado tras recargar.
- Fuentes FAQ limitadas a URLs oficiales verificadas y respuesta de una consulta
  pública basada en el catálogo.
- Comentario no útil sin escalamiento prematuro; saldo, aritmética e inyección
  atendidos por ruta determinística sin fuentes ni proveedor IA.
- Inicio de sesión del cliente, asociación de la conversación anónima, borrador
  de asesor, pausa al mostrar el menú, falta de reanudación ante un saludo y
  reanudación mediante la opción explícita.
- Formulario web, confirmación explícita y conversión a ticket; consulta
  autenticada de los tickets propios y cierre persistido que rechaza mensajes
  posteriores.

La E2E es una comprobación funcional reproducible, no una prueba estadística del
modelo ni de comportamiento con usuarios. Las métricas de intención, RAG,
grounding, seguridad, latencia y consumo se deben reportar con sus denominadores
y separar los casos determinísticos de los que permiten clasificación IA. La
corrida E2E apaga IA por diseño y no aporta medición probabilística, latencia de
proveedor ni costo.
