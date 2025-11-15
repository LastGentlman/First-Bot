# First-Bot

Bot de Telegram que recibe fotografías de tablas manuscritas, extrae los datos con OCR y los guarda en una tabla de Supabase. La interacción principal se hace mediante el comando `/tabla`, que activa el modo de procesamiento y devuelve al usuario la información detectada.

## Requisitos
- Python 3.10 o superior
- Cuenta y credenciales válidas de Supabase
- Token de bot de Telegram (creado desde [@BotFather](https://t.me/BotFather))

Instala las dependencias ejecutando:

```bash
pip install -r requirements.txt
```

## Configuración
1. Define las credenciales mediante variables de entorno (p. ej. en `.env` o `fly secrets`):
   - `BOT_TOKEN`: token del bot de Telegram.
   - `SUPABASE_URL` y `SUPABASE_KEY`: credenciales de Supabase.
   - `CHANDRA_API_KEY`: API key para el servicio de Chandra OCR (obligatoria).
   - `CHANDRA_API_URL` (opcional): endpoint del servicio Chandra OCR. Por defecto `https://api.chandra-ocr.com/v1/table`.
   - `CHANDRA_MODEL` (opcional): modelo a usar en Chandra. Por defecto `chandra-table-latest`.
   - `CHANDRA_TIMEOUT` (opcional): timeout HTTP en segundos, por defecto `30`.
2. (Opcional) Carga las variables desde `.env` usando `python-dotenv` (el archivo `config.py` ya lo hace automáticamente).
3. Verifica que en Supabase exista la tabla `registros` con las columnas `id`, `folio`, `hora` y `estado`.

## Uso
```bash
python main.py
```

Acciones disponibles desde Telegram:
- `/start`: mensaje de bienvenida y guía de uso.
- `/tabla`: activa el modo de procesamiento para la siguiente imagen de tabla.
- Envío de imagen: el bot descarga la foto, procesa los datos con Chandra OCR y responde con el resultado formateado.

## OCR
El pipeline utiliza exclusivamente **Chandra OCR**. El módulo `chandra_ocr.py` envía la imagen al endpoint configurado, traduce el layout que devuelve Chandra al formato que consume el parser y, si la respuesta carece de coordenadas, genera bounding boxes sintéticos para conservar el orden de lectura. Los errores se reportan claramente para que el bot informe al usuario sin bloquear el resto de la arquitectura.

## Estructura principal
- `main.py`: arranque del bot y handlers de comandos/mensajes.
- `config.py`: credenciales y configuración sensible.
- `procesar_tabla.py`: lógica de OCR, limpieza de datos y envío a Supabase.
- `chandra_ocr.py`: integración con Chandra OCR y normalización del layout de tabla.

## Próximos pasos sugeridos
- Ajustar la agrupación de celdas utilizando las coordenadas devueltas por el motor OCR.
- Añadir validaciones y manejo de errores en las inserciones de Supabase.
- Crear pruebas automatizadas para la función `procesar_tabla` con distintos ejemplos de tablas manuscritas.

## Despliegue en Fly.io
1. Autentícate con `fly auth login` y crea la app si aún no existe (`fly launch --no-deploy`).
2. Define los secretos requeridos: `fly secrets set BOT_TOKEN=xxx SUPABASE_URL=xxx SUPABASE_KEY=xxx`.
3. Despliega con `fly deploy --remote-only` y verifica el estado con `fly status -a first-bot`.
