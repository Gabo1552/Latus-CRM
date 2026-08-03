# Pruebas externas con autenticación

Nunca insertes una sesión predecible en MongoDB ni guardes tokens reales en el
repositorio. Creá un usuario de prueba aislado, iniciá sesión por el flujo normal
y cargá la URL y el token resultante como secretos temporales del entorno:

```powershell
$env:RUN_EXTERNAL_TESTS = "1"
$env:E2E_BACKEND_URL = "https://tu-backend-de-prueba.example"
$env:E2E_SESSION_TOKEN = "<token-temporal-obtenido-mediante-login>"
python -m pytest backend/tests/test_ai_bot_http.py -q
```

Usá únicamente staging, asigná el mínimo permiso necesario y cerrá todas las
sesiones de esa cuenta al terminar. Las sesiones se almacenan en la base como
hashes; no deben crearse directamente en la colección.
