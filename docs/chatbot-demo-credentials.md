# Cuentas de demostración local

Estas cuentas son únicamente para una base SQLite local sembrada por el script
de demo. No corresponden a usuarios reales ni deben reutilizarse en otro
entorno.

| Rol | Correo |
|---|---|
| Cliente principal | `cliente@demo.com` |
| Segundo cliente para comprobar aislamiento | `cliente2@demo.com` |
| Asesor | `asesor@demo.com` |
| Supervisor | `supervisor@demo.com` |

La contraseña local por defecto es `demo-password-local` para las cuatro
cuentas. El seed acepta valores alternativos mediante `DEMO_CLIENT_PASSWORD`,
`DEMO_SECOND_CLIENT_PASSWORD`, `DEMO_ADVISOR_PASSWORD` y
`DEMO_SUPERVISOR_PASSWORD`. El código persiste hashes Argon2; esta contraseña
visible se limita al entorno de demostración.

El runner E2E no usa esta contraseña: genera credenciales aleatorias en memoria
para cada corrida y elimina su SQLite temporal al terminar.
