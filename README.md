# First-Bot

Bot de Telegram que recibe fotografías de tablas manuscritas, extrae los datos con OCR y los guarda en una tabla de Supabase. La interacción principal se hace mediante el comando `/tabla`, que activa el modo de procesamiento y devuelve al usuario la información detectada.

## Requisitos
- Python 3.10 o superior
- Cuenta y credenciales válidas de Supabase
- Token de bot de Telegram (creado desde [@BotFather](https://t.me/BotFather))

Instala las dependencias ejecutando:

```bash
pip install -r requirements.text
```

> Nota: El archivo de dependencias se llama `requirements.text`.

## Configuración
1. Copia tus credenciales al archivo `config.py`:
   - `BOT_TOKEN`: token del bot de Telegram.
   - `SUPABASE_URL` y `SUPABASE_KEY`: credenciales de Supabase. También puedes sobreescribirlas en `procesar_tabla.py` si trabajas con distintos entornos.
2. Verifica que en Supabase exista la tabla `registros` con las columnas `id`, `folio`, `hora` y `estado`.

## Uso
```bash
python main.py
```

Acciones disponibles desde Telegram:
- `/start`: mensaje de bienvenida y guía de uso.
- `/tabla`: activa el modo de procesamiento para la siguiente imagen de tabla.
- Envío de imagen: el bot descarga la foto, procesa los datos con EasyOCR y responde con el resultado formateado.

## Estructura principal
- `main.py`: arranque del bot y handlers de comandos/mensajes.
- `config.py`: credenciales y configuración sensible.
- `procesar_tabla.py`: lógica de OCR, limpieza de datos y envío a Supabase.

## Próximos pasos sugeridos
- Ajustar la agrupación de celdas utilizando las coordenadas devueltas por EasyOCR.
- Añadir validaciones y manejo de errores en las inserciones de Supabase.
- Crear pruebas automatizadas para la función `procesar_tabla` con distintos ejemplos de tablas manuscritas.
