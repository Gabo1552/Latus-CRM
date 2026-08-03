# Plan de pruebas frontend — Integración WhatsApp

> Este documento describe los checks frontend mínimos del canal WhatsApp.
> El proyecto no incluye un framework de tests de UI (Jest/RTL/Playwright)
> instalado por defecto, por eso se documentan acá como guía de QA manual /
> referencia para `auto_frontend_testing_agent`.

## Pre-requisitos
- Sesión admin: cookie temporal obtenida mediante el inicio de sesión (ver
  `/app/memory/test_credentials.md`).
- Backend RUNNING. `GET /api/openapi.json` → 200.

## Casos

### 1. Bandeja muestra badge "WhatsApp"
- Setup: conversación con `channel="whatsapp"` (por ejemplo, generada por
  webhook real o por seed con ese campo).
- Acción: navegar a `/inbox`, seleccionar la conversación.
- Esperado: en la cabecera, junto al nombre del contacto, aparece la pill
  naranja "WhatsApp" (`[data-testid="channel-badge-whatsapp"]`).

### 2. Estado del mensaje saliente en español
- Setup: mensaje outbound con `delivery_status` en {`sent`, `delivered`,
  `read`, `failed`}.
- Acción: ver el hilo de la conversación.
- Esperado: debajo de la burbuja del mensaje (lado derecho), se muestra
  el texto en español: "Enviado" / "Entregado" / "Leído" / "Falló"
  (`[data-testid="delivery-status-{message_id}"]`).

### 3. Toast al recibir entrante (canal WhatsApp simulado)
- Acción: tocar "+ Respuesta del cliente".
- Esperado: toast "Mensaje del cliente recibido" (sigue siendo el mismo
  texto del simulador). Para webhook real, el front no recibe push
  directo — el polling de React Query renueva el hilo.

### 4. Toast en falla de envío
- Setup: integración no configurada (vacía `WHATSAPP_ACCESS_TOKEN`) en una
  conversación con `channel="whatsapp"`.
- Acción: escribir un mensaje y enviar.
- Esperado: el botón Enviar está deshabilitado (banner "WhatsApp no
  configurado") y si se forzara el POST, toast "WhatsApp no configurado".

### 5. Composer ruta WhatsApp vs manual
- Si `active.channel === "whatsapp"` y `waStatus.configured === true`:
  el `Enviar` llama a `POST /api/conversations/{id}/send-whatsapp`.
- En cualquier otro caso (manual, o WhatsApp sin configurar):
  llama al endpoint legacy `POST /api/conversations/{id}/messages`.
- El botón demo "+ Respuesta del cliente" **siempre** está disponible y
  llama a `simulate-inbound`.

### 6. Panel Admin renderiza la tarjeta WhatsApp sin secretos
- Acción: `/admin`.
- Esperado: tarjeta `[data-testid="whatsapp-integration-card"]` con:
  - Estado "WhatsApp conectado" o "WhatsApp no configurado".
  - Checklist (`[data-testid="wa-checklist"]`) con 5 entradas en español.
  - "Último webhook recibido" y "Último error de WhatsApp".
  - Sólo se muestran los últimos 4 caracteres del Phone Number ID
    (`••••XXXX`). Nunca se muestra `WHATSAPP_ACCESS_TOKEN` ni
    `WHATSAPP_APP_SECRET`.

## Notas
- Toda la copia visible está en español.
- No hay selector de idioma — la app sigue siendo Latus CRM.
- La tarjeta WhatsApp del Admin se refresca cada 30 s.
