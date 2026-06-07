# Guía de Despliegue en Producción · Latus CRM

Esta guía contiene los pasos necesarios para desplegar Latus CRM en producción utilizando **MongoDB Atlas** (Base de datos), **Railway** (Backend FastAPI) y **Vercel** (Frontend React).

---

## 1. Base de Datos: MongoDB Atlas

Recomendamos utilizar **MongoDB Atlas** (el servicio en la nube oficial de MongoDB) para alojar la base de datos de producción.

1. **Crear una cuenta**: Registrate en [MongoDB Atlas](https://www.mongodb.com/cloud/atlas).
2. **Crear un Cluster Gratuito**: Crea un cluster M0 (gratuito) en la región de tu preferencia (ej. AWS us-east-1).
3. **Configurar el Acceso a la Red (Network Access)**:
   - Ve a **Network Access** > **Add IP Address**.
   - Agrega `0.0.0.0/0` (permitir acceso desde cualquier lugar), ya que los servidores de Railway/Vercel usan IPs dinámicas.
4. **Crear un Usuario de Base de Datos (Database Access)**:
   - Ve a **Database Access** > **Add New Database User**.
   - Elige el método *Password*, define un usuario y una contraseña segura. Asígnale el rol `Read and write to any database`.
5. **Obtener la URL de Conexión**:
   - Ve a **Database** > **Connect** > **Drivers**.
   - Copia la cadena de conexión (Connection String), que se verá similar a:
     `mongodb+srv://<usuario>:<password>@cluster0.xxxx.mongodb.net/?retryWrites=true&w=majority`
   - Reemplaza `<password>` con la contraseña del usuario que creaste. Esta será tu variable de entorno `MONGO_URL`.

---

## 2. Backend: Railway

Recomendamos **Railway** para el backend porque maneja procesos de larga duración, websockets y tareas en segundo plano de forma nativa a través de Docker.

1. **Crear una cuenta**: Registrate en [Railway.app](https://railway.app) usando tu cuenta de GitHub.
2. **Crear un Nuevo Proyecto**:
   - Haz clic en **New Project** > **Deploy from GitHub repo**.
   - Selecciona tu repositorio de `Latus-CRM`.
3. **Configurar el Root Directory del Backend**:
   - Ve a la configuración de la tarjeta del servicio en Railway.
   - En **Settings** > **Root Directory**, escribe `/backend`.
   - Railway detectará automáticamente el archivo `Dockerfile` que creamos en `/backend/Dockerfile` y compilará la imagen.
4. **Configurar las Variables de Entorno (Variables)**:
   Agrega las siguientes variables en la pestaña **Variables**:

   | Variable | Valor sugerido / Descripción |
   | :--- | :--- |
   | `PORT` | `8000` |
   | `MONGO_URL` | La cadena de conexión de MongoDB Atlas (obtenida en el Paso 1). |
   | `DB_NAME` | `latus_crm` (nombre de la base de datos en MongoDB). |
   | `CORS_ORIGINS` | Tu URL de producción de Vercel (ej. `https://latus-crm.vercel.app`). *¡Crítico para las cookies!* |
   | `LATUS_SEED_DEMO` | `true` (Ponelo en `true` solo en el primer despliegue para precargar leads y pipeline demo. Luego cambialo a `false` o eliminalo). |
   | `LATUS_LLM_KEY` | Clave del sistema universal o clave API de tu proveedor (OpenAI, Anthropic). |

5. **Generar el Dominio Público**:
   - Ve a **Settings** > **Domains** > **Generate Domain** (o asocia tu propio dominio).
   - Copia la URL generada (ej. `https://latus-crm-production.up.railway.app`). Esta URL será tu `REACT_APP_BACKEND_URL` en el frontend.

---

## 3. Frontend: Vercel

Desplegaremos el frontend React SPA en **Vercel**, el cual leerá automáticamente la configuración de `vercel.json` para dar soporte a la navegación por rutas.

1. **Crear una cuenta**: Registrate en [Vercel](https://vercel.com) e inicia sesión con GitHub.
2. **Importar el Proyecto**:
   - Haz clic en **Add New** > **Project**.
   - Elige el repositorio `Latus-CRM`.
3. **Configurar el Proyecto**:
   - **Root Directory**: Haz clic en *Edit* y selecciona la carpeta `frontend`.
   - **Framework Preset**: Selecciona `Create React App`.
   - **Build Command**: Asegúrate de que sea `npm run build`.
   - **Output Directory**: Asegúrate de que sea `build`.
4. **Configurar las Variables de Entorno**:
   Agrega la siguiente variable de entorno:

   | Variable | Valor |
   | :--- | :--- |
   | `REACT_APP_BACKEND_URL` | La URL pública de tu backend en Railway (ej. `https://latus-crm-production.up.railway.app`). **Sin `/api` ni barras `/` al final.** |

5. **Desplegar**:
   - Haz clic en **Deploy**. Vercel compilará la aplicación y te dará una URL pública de producción (ej. `https://latus-crm.vercel.app`).
   - **Paso Final**: Copia esta URL de Vercel y agrégala a la variable `CORS_ORIGINS` en la configuración de tu backend de Railway para habilitar las peticiones y cookies seguras.
