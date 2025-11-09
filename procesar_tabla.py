import easyocr
import re
import logging
from datetime import datetime
from functools import lru_cache
from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_KEY

logger = logging.getLogger(__name__)

reader = easyocr.Reader(["es"], gpu=False)


@lru_cache(maxsize=1)
def get_supabase_client() -> Client:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError(
            "SUPABASE_URL y SUPABASE_KEY no están configuradas. "
            "Define las variables de entorno antes de ejecutar la aplicación."
        )
    return create_client(SUPABASE_URL, SUPABASE_KEY)

# --- Funciones auxiliares ---

def limpiar_hora(hora_raw: str) -> str:
    """Normaliza texto OCR de hora (corrige l, I, -, etc.)"""
    hora = hora_raw.strip().replace("l", "1").replace("I", "1").replace("-", ":")
    match = re.match(r"(\d{1,2}):(\d{2})", hora)
    if match:
        h, m = int(match[1]), match[2]
        return f"{h:02}:{m}"
    return "00:00"

def limpiar_estado(simbolo: str) -> str:
    """Convierte símbolos OCR a estado de texto"""
    s = simbolo.strip().lower()
    if any(x in s for x in ["✔", "✓", "√", "v"]):
        return "completado"
    if any(x in s for x in ["⚠", "x"]):
        return "pendiente"
    return "indefinido"

def completar_comillas(filas):
    """Rellena valores con comillas usando el último valor conocido"""
    ultimos = {"id": None, "folio": None, "hora": None, "estado": None}
    nuevas = []
    for fila in filas:
        for k in fila:
            if fila[k] == '"' or fila[k] == "''":
                fila[k] = ultimos[k]
            else:
                ultimos[k] = fila[k]
        nuevas.append(fila)
    return nuevas

# --- Función principal ---

def procesar_tabla(imagen):
    """Lee una tabla escrita a mano con EasyOCR, limpia, ordena y guarda en Supabase"""
    try:
        logger.info(f"Iniciando procesamiento de tabla desde imagen: {imagen}")
        
        # Leer texto de la imagen con OCR
        try:
            result = reader.readtext(imagen, detail=0)
        except Exception as e:
            logger.error(f"Error en OCR: {e}")
            return f"Error: No se pudo leer el texto de la imagen. {str(e)}"
        
        if not result or len(result) == 0:
            return "Error: No se detectó texto en la imagen. Asegúrate de que la imagen sea clara y contenga texto."
        
        logger.info(f"OCR detectó {len(result)} elementos de texto")
        
        # Detectar prefijo numérico base
        prefijo = ""
        for text in result:
            if re.match(r"\d{3,}", text):
                prefijo = text[:3]
                break

        # Simular agrupación (en práctica usarías coordenadas, aquí simplificado)
        # Estructura de columnas detectadas: ID | FOLIO | HORA | ESTADO
        filas = []
        linea = []
        for texto in result:
            if re.match(r"[a-zA-Z]", texto):
                if linea:
                    filas.append(linea)
                    linea = []
                linea = [texto]
            else:
                linea.append(texto)
        if linea:
            filas.append(linea)

        # Limpieza de filas incompletas o con ruido
        datos = []
        for f in filas:
            if len(f) >= 4:
                try:
                    id_ = f[0].strip()[0]
                    folio = re.sub(r"[^\d]", "", f[1])
                    hora = limpiar_hora(f[2])
                    estado = limpiar_estado(f[3])
                    datos.append({
                        "id": id_,
                        "folio": folio,
                        "hora": hora,
                        "estado": estado
                    })
                except (IndexError, ValueError) as e:
                    logger.warning(f"Error procesando fila {f}: {e}")
                    continue

        if not datos:
            return "Error: No se pudieron extraer datos válidos de la tabla. Verifica que la imagen contenga una tabla con columnas claras."

        datos = completar_comillas(datos)

        # Agregar columna de folio completo
        for d in datos:
            if prefijo and not d["folio"].startswith(prefijo):
                d["folio_completo"] = f"{d['id']}{prefijo}{d['folio']}"
            else:
                d["folio_completo"] = f"{d['id']}{d['folio']}"

        # Ordenar por hora
        try:
            datos_ordenados = sorted(datos, key=lambda x: datetime.strptime(x["hora"], "%H:%M"))
        except ValueError as e:
            logger.warning(f"Error ordenando por hora: {e}, usando orden original")
            datos_ordenados = datos

        # Insertar en Supabase
        try:
            supabase = get_supabase_client()
            registros_insertados = 0
            errores = []
            
            for row in datos_ordenados:
                try:
                    # Validar que no haya valores None o vacíos antes de insertar
                    if not row.get("id") or row["id"] is None:
                        errores.append(f"ID vacío o None en registro: {row}")
                        continue
                    
                    if not row.get("folio_completo") or row["folio_completo"] is None:
                        errores.append(f"Folio vacío o None en registro: {row}")
                        continue
                    
                    if not row.get("hora") or row["hora"] is None:
                        errores.append(f"Hora vacía o None en registro: {row}")
                        continue
                    
                    if not row.get("estado") or row["estado"] is None:
                        errores.append(f"Estado vacío o None en registro: {row}")
                        continue
                    
                    # Preparar datos para inserción
                    datos_insert = {
                        "id": str(row["id"]).strip(),
                        "folio": str(row["folio_completo"]).strip(),
                        "hora": str(row["hora"]).strip(),
                        "estado": str(row["estado"]).strip()
                    }
                    
                    # Validar que los datos no estén vacíos después de strip
                    if not all(datos_insert.values()):
                        errores.append(f"Datos vacíos después de limpieza: {datos_insert}")
                        continue
                    
                    response = supabase.table("registros").insert(datos_insert).execute()
                    registros_insertados += 1
                    logger.debug(f"Registro insertado exitosamente: {datos_insert}")
                    
                except Exception as e:
                    error_msg = str(e)
                    logger.error(f"Error insertando registro {row}: {error_msg}")
                    
                    # Mensajes de error más específicos según el tipo de error
                    if "duplicate key" in error_msg.lower() or "unique constraint" in error_msg.lower():
                        errores.append(f"ID duplicado: {row.get('id')} (ya existe en la base de datos)")
                    elif "null value" in error_msg.lower() or "not null" in error_msg.lower():
                        errores.append(f"Valor requerido faltante en registro: {row}")
                    elif "foreign key" in error_msg.lower():
                        errores.append(f"Referencia inválida en registro: {row}")
                    elif "check constraint" in error_msg.lower():
                        errores.append(f"Valor no válido según restricciones: {row}")
                    else:
                        errores.append(f"Error de base de datos: {error_msg}")
                    continue
            
            logger.info(f"Procesamiento completado: {registros_insertados} registros insertados de {len(datos_ordenados)}")
            
            # Construir mensaje de resultado
            mensaje_resultado = f"Procesados {len(datos_ordenados)} registros, {registros_insertados} insertados exitosamente."
            
            if errores:
                mensaje_resultado += f"\n⚠️ {len(errores)} error(es): " + "; ".join(errores[:3])  # Mostrar solo los primeros 3 errores
                if len(errores) > 3:
                    mensaje_resultado += f" ... y {len(errores) - 3} más"
            
            if registros_insertados == 0:
                return f"Error: No se pudieron insertar registros en la base de datos. Se procesaron {len(datos_ordenados)} registros. " + \
                       (f"Errores: {'; '.join(errores[:2])}" if errores else "Verifica el schema de la tabla 'registros' en Supabase.")
            
            return mensaje_resultado
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error conectando a Supabase: {error_msg}", exc_info=True)
            
            # Mensajes de error más específicos
            if "connection" in error_msg.lower() or "timeout" in error_msg.lower():
                return f"Error: No se pudo conectar a la base de datos. Verifica tu conexión a internet y las credenciales de Supabase."
            elif "authentication" in error_msg.lower() or "unauthorized" in error_msg.lower():
                return f"Error: Credenciales de Supabase inválidas. Verifica SUPABASE_URL y SUPABASE_KEY."
            elif "not found" in error_msg.lower() or "404" in error_msg.lower():
                return f"Error: La tabla 'registros' no existe en Supabase. Verifica que la tabla esté creada con las columnas: id, folio, hora, estado."
            else:
                return f"Error: No se pudo conectar a la base de datos. {error_msg}"
        
    except MemoryError:
        logger.error("Error de memoria al procesar tabla")
        return "Error: No hay suficiente memoria para procesar la imagen. Intenta con una imagen más pequeña."
    
    except Exception as e:
        logger.error(f"Error inesperado al procesar tabla: {e}", exc_info=True)
        return f"Error: Error inesperado al procesar la tabla. {str(e)}"