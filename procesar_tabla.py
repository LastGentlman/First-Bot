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
        
        # Detectar prefijo numérico base (busca números de 5+ dígitos o usa el patrón común)
        prefijo_detectado = None
        for text in result:
            text_clean = text.strip()
            # Busca números largos que probablemente contengan el prefijo
            if re.match(r"^\d{5,}$", text_clean):
                prefijo_detectado = text_clean[:3]
                logger.info(f"Prefijo detectado automáticamente: {prefijo_detectado}")
                break
        
        # Si no se detectó, buscar en números de 3-4 dígitos (podría ser el prefijo completo)
        if not prefijo_detectado:
            for text in result:
                text_clean = text.strip()
                if re.match(r"^\d{3,4}$", text_clean):
                    # Si es de 3 dígitos, podría ser el prefijo
                    if len(text_clean) == 3:
                        prefijo_detectado = text_clean
                        logger.info(f"Prefijo detectado desde número de 3 dígitos: {prefijo_detectado}")
                        break
        
        # Si aún no se detectó, usar prefijo por defecto
        if not prefijo_detectado:
            prefijo_detectado = "168"
            logger.info(f"Usando prefijo por defecto: {prefijo_detectado}")
        
        # Filtrar líneas relevantes (folios, horas, símbolos)
        # El algoritmo busca patrones: Letra -> Folio -> Hora -> Estado
        datos = []
        letra = None
        
        for i, texto in enumerate(result):
            texto = texto.strip()
            
            # Detecta letra inicial (ej. A)
            if re.match(r"^[A-Z]$", texto):
                letra = texto
                logger.debug(f"Letra detectada: {letra}")
                continue
            
            # Detecta folio (número de 3-4 dígitos)
            if re.match(r"^\d{3,4}$", texto):
                numero = texto
                
                # Busca siguiente elemento (hora)
                hora = None
                estado = None
                
                if i + 1 < len(result):
                    posible_hora = result[i + 1].strip()
                    # Usar la función limpiar_hora existente para normalizar
                    hora_normalizada = limpiar_hora(posible_hora)
                    
                    # Verificar si realmente es una hora válida
                    if re.match(r"^\d{1,2}:\d{2}$", hora_normalizada):
                        # Parsear la hora para convertir horas tempranas
                        match_hora = re.match(r"(\d{1,2}):(\d{2})", hora_normalizada)
                        if match_hora:
                            h = int(match_hora.group(1))
                            m = match_hora.group(2)
                            
                            # Si la hora es temprana (1:00-4:59), probablemente es AM del siguiente día
                            if h < 5:
                                hora = f"{h+24}:{m}"
                            else:
                                hora = f"{h:02d}:{m}"
                            
                            # Busca símbolo (✅ o ❌) en el siguiente elemento
                            if i + 2 < len(result):
                                simbolo = result[i + 2].strip()
                                estado = limpiar_estado(simbolo)
                            else:
                                # Si no hay símbolo, buscar en elementos más adelante
                                for j in range(i + 2, min(i + 5, len(result))):
                                    simbolo = result[j].strip()
                                    estado_temp = limpiar_estado(simbolo)
                                    if estado_temp != "indefinido":
                                        estado = estado_temp
                                        break
                                if not estado:
                                    estado = "indefinido"
                
                # Si encontramos todos los componentes, agregar el registro
                if letra and numero and hora and estado:
                    folio_completo = f"{prefijo_detectado}{numero}"
                    datos.append({
                        "id": letra,
                        "folio": numero,
                        "folio_completo": folio_completo,
                        "hora": hora,
                        "estado": estado
                    })
                    logger.debug(f"Registro detectado: Letra={letra}, Folio={folio_completo}, Hora={hora}, Estado={estado}")
                    # Resetear letra para el siguiente registro
                    letra = None
                elif letra and numero:
                    # Si tenemos letra y folio pero falta hora o estado, loguear para debugging
                    logger.debug(f"Registro incompleto: Letra={letra}, Folio={numero}, Hora={hora}, Estado={estado}")

        if not datos:
            return "Error: No se pudieron extraer datos válidos de la tabla. Verifica que la imagen contenga una tabla con columnas claras."
        
        logger.info(f"Se detectaron {len(datos)} registros completos")

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