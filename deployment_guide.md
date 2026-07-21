# Guía de Despliegue y Separación de Entornos (Staging vs Producción) · Latus CRM

Esta guía detalla la arquitectura, configuración y el paso a paso exacto para mantener **totalmente aislados** los entornos de **Staging** (pruebas) y **Producción** (operativo real) de Latus CRM.

---

## 🏗️ Arquitectura de Entornos Separados

| Componente | Entorno de Staging | Entorno de Producción |
| :--- | :--- | :--- |
| **Frontend** | Vercel (`latus-crm-staging.vercel.app`) | Vercel (`latus-crm.vercel.app` / dominio propio) |
| **Backend** | Railway (`latus-crm-staging.up.railway.app`) | Railway (`latus-crm-production.up.railway.app`) |
| **Base de Datos** | MongoDB Atlas (`DB_NAME=latus-crm-staging`) | MongoDB Atlas (`DB_NAME=latus-crm-production`) |
| **Mercado Pago** | Integración de Pruebas (`TEST-...` / Vendedor Test) | Integración Productiva (`APP_USR-...`) |
| **Variable `ENVIRONMENT`** | `ENVIRONMENT=staging` | `ENVIRONMENT=production` |

---

## 📊 Matriz de Variables de Entorno

### 1. Variables para Railway (Backend)

| Variable | Valor en Staging | Valor en Producción | Descripción |
| :--- | :--- | :--- | :--- |
| `ENVIRONMENT` | `staging` | `production` | **Crítico:** Controla las reglas de seguridad y validación de arranque. |
| `PORT` | `8000` | `8000` | Puerto en el que escucha FastAPI. |
| `MONGO_URL` | String de MongoDB Atlas | String de MongoDB Atlas | Conexión a la instancia de MongoDB Atlas. |
| `DB_NAME` | `latus-crm-staging` | `latus-crm-production` | **Crítico:** Nombre de la base de datos independiente. |
| `PUBLIC_BASE_URL` | `https://latus-crm-staging.up.railway.app` | `https://latus-crm-production.up.railway.app` | URL pública del backend, utilizada por webhooks. |
| `APP_BASE_URL` | `https://latus-crm-staging.vercel.app` | `https://latus-crm.vercel.app` | URL pública del frontend. |
| `CORS_ORIGINS` | `https://latus-crm-staging.vercel.app` | `https://latus-crm.vercel.app` | Lista exacta de frontends autorizados. No usar `*`. |
| `MERCADOPAGO_ACCESS_TOKEN` | Credencial `TEST-...` o token de vendedor de prueba | Credencial `APP_USR-...` real | Token privado de API de Mercado Pago. |
| `MERCADOPAGO_WEBHOOK_SECRET` | Secret del webhook de prueba | Secret del webhook productivo | Firma secreta para validar eventos de MP. |
| `MERCADOPAGO_MODE` | `test` | `production` | Declaración explícita del tipo de cuenta configurada. |
| `APP_ENCRYPTION_KEY` | Clave exclusiva de Staging | Clave exclusiva de Producción | Protege secretos guardados desde el CRM. No reutilizarla ni cambiarla luego. |
| `PLATFORM_ADMIN_EMAILS` | Administradores de prueba | Administradores reales | Emails con acceso a la administración multiempresa. |
| `BILLING_GRACE_DAYS` | `7` | `7` | Días de gracia ante un cobro rechazado. |
| `LATUS_SEED_DEMO` | `true` solo cuando se necesite regenerar la demo | `false` | Nunca sembrar datos demo en Producción. |
| `LATUS_LLM_KEY` | Clave API de prueba o compartida | Clave API de producción | Clave del proveedor de LLM / IA. |
| `RESEND_API_KEY` | Clave Resend | Clave Resend | Clave de envío de e-mails transaccionales. |
| `RESEND_FROM_EMAIL` | `notificaciones@somoslatus.com` | `notificaciones@somoslatus.com` | Remitente de e-mails. |
| `RESEND_FROM_NAME` | `Latus CRM (Staging)` | `Latus CRM` | Nombre visible del remitente. |

### 2. Variables para Vercel (Frontend)

| Variable | Valor en Staging | Valor en Producción |
| :--- | :--- | :--- |
| `REACT_APP_BACKEND_URL` | `https://latus-crm-staging.up.railway.app` | `https://latus-crm-production.up.railway.app` |

---

## 🗄️ Paso a Paso: Crear la Base de Datos de Producción en MongoDB Atlas

1. Inicia sesión en [MongoDB Atlas](https://cloud.mongodb.com/).
2. Ve a **Database** > Selecciona tu Cluster existente.
3. No es necesario crear un cluster nuevo si utilizas Atlas M0/M10/M20. Las bases de datos se crean automáticamente especificando el nombre en `DB_NAME`.
4. Para la variable `DB_NAME` en el proyecto backend de **Producción en Railway**, establece exactamente:
   ```env
   DB_NAME=latus-crm-production
   ```
5. Para el entorno de **Staging en Railway**, establece:
   ```env
   DB_NAME=latus-crm-staging
   ```
6. El backend creará automáticamente las colecciones e índices necesarios de forma aislada sin interferir entre sí.

> Se puede utilizar el mismo cluster de Atlas, pero cada entorno debe tener un `DB_NAME` diferente. Para un aislamiento más estricto, usa usuarios de MongoDB distintos y limita cada uno a su propia base.

---

## 🚂 Paso a Paso: Crear el Backend de Producción en Railway

1. Crea un servicio nuevo desde el repositorio de Latus CRM. No reutilices el servicio de Staging.
2. Selecciona la rama aprobada para Producción y configura `backend` como directorio raíz.
3. Genera el dominio público del servicio y guárdalo en `PUBLIC_BASE_URL`.
4. Carga las variables de la columna Producción de la tabla anterior. Usa secretos nuevos para Producción.
5. Mantén `LATUS_SEED_DEMO=false` desde el primer arranque.
6. Despliega y verifica:
   - `https://TU-BACKEND/api/health` responde `environment: production`.
   - `https://TU-BACKEND/api/health/ready` responde `ok: true`.
7. Si alguna protección rechaza el arranque, corrige las variables; no desactives la validación.

## ▲ Paso a Paso: Crear el Frontend de Producción en Vercel

1. Crea un proyecto Vercel independiente desde el mismo repositorio.
2. Configura `frontend` como directorio raíz.
3. Agrega `REACT_APP_BACKEND_URL` con el dominio del Railway de Producción, sin `/api` ni barra final.
4. Despliega y copia el dominio definitivo del frontend.
5. Regresa a Railway Producción y usa ese dominio exacto en `APP_BASE_URL` y `CORS_ORIGINS`.
6. Redespliega el backend y luego el frontend.
7. No agregues comodines ni dominios de previews de Vercel a Producción. Si necesitas probar un preview, agrégalo temporalmente y solo en Staging.

---

## 💳 Paso a Paso: Configurar Aplicaciones e Integraciones en Mercado Pago

Debes mantener dos aplicaciones o configuraciones separadas en el panel de desarrolladores de Mercado Pago:

### A. Aplicación de Staging (Pruebas)
1. Ve a **Mercado Pago Developers > Tus integraciones**.
2. Abre la aplicación de pruebas o crea una llamada `Latus CRM - Staging`.
3. Ve a **Credenciales de prueba** y copia el `Access Token` (`TEST-...`) o utiliza las credenciales de tu **Cuenta Vendedora de Prueba**.
4. Configura en Railway Staging `MERCADOPAGO_ACCESS_TOKEN` y `MERCADOPAGO_MODE=test`.
5. Ve a **Webhooks** en la aplicación de pruebas y agrega la URL:
   ```text
   https://latus-crm-staging.up.railway.app/api/webhooks/mercadopago
   ```
6. Eventos a suscribir: `Pagos`, `Suscripciones vinculadas` (`subscription_preapproval`) y `Pagos autorizados de suscripciones`.

### B. Aplicación de Producción (Cobros Reales)
1. En **Tus integraciones**, crea o abre la aplicación oficial productiva (ej. `Latus CRM`).
2. Ve a **Credenciales de producción** (requiere activar credenciales completando rubro y sitio web).
3. Copia el `Access Token` de producción (`APP_USR-...`).
4. Configura en Railway Producción `MERCADOPAGO_ACCESS_TOKEN` y `MERCADOPAGO_MODE=production`.
5. Ve a **Webhooks** (Modo Producción) y agrega la URL:
   ```text
   https://latus-crm-production.up.railway.app/api/webhooks/mercadopago
   ```
6. Copia el secret generado a `MERCADOPAGO_WEBHOOK_SECRET` en Railway Producción.

---

## 🛡️ Guardrails de Seguridad Implementados en el Código

El backend de Latus CRM incluye verificaciones de seguridad automáticas al iniciar (`startup guardrails` en `server.py`):

1. **Protección de Base de Datos en Producción:**
   Si `ENVIRONMENT=production` y `DB_NAME` contiene `staging` o `test`, el servidor **rehúsa iniciar** y lanza una excepción indicando que se debe corregir la variable.
2. **Protección de Credenciales de Pago en Producción:**
   Si `ENVIRONMENT=production` y `MERCADOPAGO_ACCESS_TOKEN` comienza con `TEST-`, el servidor **rehúsa iniciar** para evitar transacciones simuladas en producción.
3. **Protección de Staging:**
   Si `ENVIRONMENT=staging` y `DB_NAME` contiene `production`, el servidor rehúsa iniciar para impedir que pruebas afecten datos reales.
4. **CORS estricto:**
   Staging y Producción exigen HTTPS, rechazan `*` y solo aceptan los dominios declarados explícitamente en `CORS_ORIGINS`.
5. **Coherencia de URLs:**
   `APP_BASE_URL` debe estar incluida exactamente en `CORS_ORIGINS`; Producción también rechaza URLs que contengan `staging`.
6. **Modo de Mercado Pago:**
   Cuando Mercado Pago está configurado, Staging exige `MERCADOPAGO_MODE=test` y Producción exige `MERCADOPAGO_MODE=production`.

---

## ✅ Lista de Comprobación de Aislamiento (Checklist)

- [ ] `ENVIRONMENT=staging` configurado en Railway Staging.
- [ ] `ENVIRONMENT=production` configurado en Railway Producción.
- [ ] `DB_NAME` de Staging es `latus-crm-staging`.
- [ ] `DB_NAME` de Producción es `latus-crm-production`.
- [ ] `MERCADOPAGO_ACCESS_TOKEN` en Producción arranca con `APP_USR-...`.
- [ ] `MERCADOPAGO_MODE=test` en Staging y `production` en Producción.
- [ ] `PUBLIC_BASE_URL` apunta al Railway correspondiente en cada entorno.
- [ ] `APP_ENCRYPTION_KEY` es distinta y está guardada de forma segura en cada entorno.
- [ ] `LATUS_SEED_DEMO=false` en Producción.
- [ ] Webhook de Staging apunta a `https://latus-crm-staging.up.railway.app/api/webhooks/mercadopago`.
- [ ] Webhook de Producción apunta a `https://latus-crm-production.up.railway.app/api/webhooks/mercadopago`.
- [ ] `CORS_ORIGINS` en Backend Staging únicamente permite `https://latus-crm-staging.vercel.app`.
- [ ] `CORS_ORIGINS` en Backend Producción únicamente permite el frontend de Producción.
- [ ] `REACT_APP_BACKEND_URL` en Vercel Staging apunta al backend de Staging.
- [ ] `REACT_APP_BACKEND_URL` en Vercel Producción apunta al backend de Producción.

## 🔎 Validación final antes de habilitar clientes

1. Crea un contacto identificable únicamente en Staging y confirma que no aparece en Producción.
2. Crea otro contacto únicamente en Producción y confirma que no aparece en Staging.
3. Comprueba que Staging muestra enlaces de Mercado Pago de prueba y nunca solicita dinero real.
4. Comprueba que los webhooks de cada aplicación llegan solamente a su backend correspondiente.
5. Verifica inicio y cierre de sesión desde ambos frontends.
6. Conserva una copia segura de las variables y documenta quién puede modificarlas.
