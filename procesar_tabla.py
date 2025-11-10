import easyocr
import re
import logging
from functools import lru_cache
from typing import Optional
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

def limpiar_hora(hora_raw: str) -> Optional[str]:
    """Normaliza texto OCR de hora (corrige caracteres comunes y valida formato HH:MM)."""
    if not hora_raw:
        return None

    hora = hora_raw.strip()
    if not hora:
        return None

    reemplazos = {
        "l": "1",
        "I": "1",
        "|": "1",
        "O": "0",
        "o": "0",
        "S": "5",
        "s": "5",
        "B": "8",
        "v": "4",
        "V": "4",
        "-": ":",
        "—": ":",
        "–": ":",
        ";": ":",
        ",": ":",
        ".": ":",
        "*": ":",
        "·": ":",
        " ": "",
    }

    for caracter, reemplazo in reemplazos.items():
        hora = hora.replace(caracter, reemplazo)

    # Mantener solo dígitos y el separador de hora
    hora = re.sub(r"[^0-9:]", "", hora)

    # Si hay más de un separador, conservar solo el primero
    if hora.count(":") > 1:
        primera_pos = hora.find(":")
        hora = hora[: primera_pos + 1] + hora[primera_pos + 1 :].replace(":", "")

    # Insertar separador cuando falte pero la longitud sea compatible con HHMM
    if ":" not in hora and len(hora) == 4:
        hora = f"{hora[:2]}:{hora[2:]}"

    match = re.match(r"^(\d{1,2}):(\d{2})$", hora)
    if not match:
        return None

    h, m = int(match[1]), match[2]
    if h > 29 or int(m) > 59:
        # Horas con valores fuera de rango razonable
        return None

    return f"{h:02}:{m}"


def valor_orden_hora(hora: str) -> int:
    """
    Convierte una hora en minutos corridos, aplicando un offset de +24h
    para horas de madrugada (00:00-04:59) con el fin de mantener el orden cronológico.
    """
    if not hora or ":" not in hora:
        raise ValueError(f"Hora inválida para ordenar: {hora!r}")

    try:
        h_str, m_str = hora.split(":")
        h = int(h_str)
        m = int(m_str)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Hora inválida para ordenar: {hora!r}") from exc

    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"Hora fuera de rango para ordenar: {hora!r}")

    if h < 5:
        h += 24

    return h * 60 + m

def limpiar_estado(simbolo: str) -> str:
    """Convierte símbolos OCR a estado de texto"""
    s = simbolo.strip().lower()
    if any(x in s for x in ["✔", "✓", "√", "v"]):
        return "completado"
    if any(x in s for x in ["⚠", "x"]):
        return "pendiente"
    return "indefinido"


def estado_a_icono(estado: str) -> str:
    """Mapea el estado textual a un icono representativo para mostrar en resultados."""
    if not estado:
        return "❔"
    estado_normalizado = estado.strip().lower()
    if estado_normalizado == "completado":
        return "✅"
    if estado_normalizado == "pendiente":
        return "⚠️"
    if estado_normalizado == "indefinido":
        return "❔"
    return estado

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
        logger.info(f"Elementos detectados por OCR: {result}")  # Mostrar todos los elementos para debugging
        
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
        
        # Inicializar listas de componentes
        letras_encontradas = []
        folios_encontrados = []
        horas_encontradas = []
        estados_encontrados = []
        
        try:
            # Primero, buscar todas las letras, folios, horas y estados en los resultados
            logger.info("Iniciando búsqueda de componentes en resultados OCR...")

            for i, texto in enumerate(result):
                texto = texto.strip()
                logger.debug(f"Procesando elemento {i}: '{texto}'")

                # Detecta letra inicial (ej. A) permitiendo ruido adicional
                texto_letra = re.sub(r"[^A-Za-z]", "", texto)
                if len(texto_letra) == 1 and texto_letra.isalpha():
                    letra_normalizada = texto_letra.upper()
                    letras_encontradas.append((i, letra_normalizada))
                    logger.info(f"Letra detectada en posición {i}: {texto} -> {letra_normalizada}")

                # Detecta folio (número de 3-4 dígitos, tolerando OCR ruidoso)
                folio_normalizado = re.sub(r"\D", "", texto)
                if 3 <= len(folio_normalizado) <= 4:
                    folios_encontrados.append((i, folio_normalizado))
                    logger.info(f"Folio detectado en posición {i}: {texto} -> {folio_normalizado}")

                # Detecta hora (patrón HH:MM o H:MM)
                try:
                    hora_normalizada = limpiar_hora(texto)
                    if hora_normalizada:
                        horas_encontradas.append((i, hora_normalizada, texto))
                        logger.info(f"Hora detectada en posición {i}: {texto} -> {hora_normalizada}")
                except Exception as e:
                    logger.debug(f"Error procesando hora en posición {i}: {e}")

                # Detecta estado (símbolos)
                try:
                    estado_temp = limpiar_estado(texto)
                    if estado_temp != "indefinido":
                        estados_encontrados.append((i, estado_temp, texto))
                        logger.info(f"Estado detectado en posición {i}: {texto} -> {estado_temp}")
                except Exception as e:
                    logger.debug(f"Error procesando estado en posición {i}: {e}")

            logger.info(
                f"Resumen: {len(letras_encontradas)} letras, "
                f"{len(folios_encontrados)} folios, "
                f"{len(horas_encontradas)} horas, "
                f"{len(estados_encontrados)} estados"
            )
        except Exception as e:
            logger.error(f"Error al buscar componentes: {e}", exc_info=True)
            return f"Error: Error al procesar los resultados de OCR. {str(e)}"
        
        # Ahora intentar emparejar los componentes
        # Estrategia: buscar folios y luego buscar letra, hora y estado cercanos
        logger.info(f"Iniciando emparejamiento de componentes para {len(folios_encontrados)} folios...")
        
        for folio_idx, folio_num in folios_encontrados:
            logger.debug(f"Procesando folio {folio_num} en posición {folio_idx}")

            # Buscar letra más cercana antes del folio
            letra_cercana = None
            for letra_idx, letra_text in letras_encontradas:
                if letra_idx < folio_idx and (folio_idx - letra_idx) <= 5:
                    letra_cercana = letra_text
                    break

            # Buscar hora más cercana después del folio
            hora_cercana = None
            for hora_idx, hora_norm, _ in horas_encontradas:
                if hora_idx > folio_idx and (hora_idx - folio_idx) <= 5:
                    hora_cercana = hora_norm
                    break

            # Buscar estado más cercano después de la hora (o después del folio si no hay hora)
            estado_cercano = None
            buscar_desde = folio_idx
            if hora_cercana:
                for hora_idx, _, _ in horas_encontradas:
                    if hora_idx > folio_idx:
                        buscar_desde = hora_idx
                        break

            for estado_idx, estado_text, _ in estados_encontrados:
                if estado_idx > buscar_desde and (estado_idx - buscar_desde) <= 5:
                    estado_cercano = estado_text
                    break
            
            # Si no encontramos estado, usar "indefinido"
            if not estado_cercano:
                estado_cercano = "indefinido"

            # Si tenemos folio y hora, crear el registro (letra opcional con fallback)
            if folio_num and hora_cercana:
                match_hora = re.match(r"(\d{1,2}):(\d{2})", hora_cercana)
                if match_hora:
                    h = int(match_hora.group(1))
                    m = match_hora.group(2)

                    if h < 5:
                        logger.debug(
                            f"Hora {hora_cercana} interpretada como madrugada; "
                            f"se almacenará como {h:02d}:{m} aplicando offset para ordenamiento."
                        )

                    hora_final = f"{h:02d}:{m}"

                    folio_completo = f"{prefijo_detectado}{folio_num}"
                    letra_final = letra_cercana or f"fila_{len(datos)+1:02d}"
                    if not letra_cercana:
                        logger.debug(
                            f"No se detectó letra para folio {folio_completo}; "
                            f"se asigna identificador por defecto {letra_final}"
                        )

                    datos.append({
                        "id": letra_final,
                        "folio": folio_num,
                        "folio_completo": folio_completo,
                        "hora": hora_final,
                        "estado": estado_cercano
                    })
                    logger.info(
                        f"Registro creado: Letra={letra_final}, "
                        f"Folio={folio_completo}, Hora={hora_final}, Estado={estado_cercano}"
                    )
                else:
                    logger.debug(
                        f"Hora '{hora_cercana}' no coincide con patrón esperado para folio {folio_num}"
                    )
            else:
                logger.debug(
                    f"No se pudo crear registro para folio {folio_num}: "
                    f"letra={letra_cercana}, hora={hora_cercana}"
                )

        if not datos:
            # Proporcionar información más detallada sobre qué se detectó
            info_detectado = f"Se detectaron: {len(letras_encontradas)} letras, {len(folios_encontrados)} folios, {len(horas_encontradas)} horas, {len(estados_encontrados)} estados. "
            info_detectado += f"Elementos OCR: {result[:15] if len(result) > 0 else 'ninguno'}"
            logger.warning(f"No se pudieron extraer datos válidos. {info_detectado}")
            logger.warning(f"Letras encontradas: {letras_encontradas}")
            logger.warning(f"Folios encontrados: {folios_encontrados}")
            logger.warning(f"Horas encontradas: {horas_encontradas}")
            logger.warning(f"Estados encontrados: {estados_encontrados}")
            return f"Error: No se pudieron extraer datos válidos de la tabla. {info_detectado} Verifica que la imagen contenga una tabla con columnas claras (Letra, Folio, Hora, Estado)."
        
        logger.info(f"Se detectaron {len(datos)} registros completos")

        # Ordenar por hora
        try:
            datos_ordenados = sorted(datos, key=lambda x: valor_orden_hora(x["hora"]))
        except ValueError as e:
            logger.warning(f"Error ordenando por hora: {e}, usando orden original")
            datos_ordenados = datos

        # Preparar filas para la tabla de salida
        tabla_filas = []
        for row in datos_ordenados:
            id_valor = str(row.get("id") or "").strip()
            folio_base = str(row.get("folio_completo") or row.get("folio") or "").strip()
            if id_valor and len(id_valor) == 1 and id_valor.isalpha() and folio_base:
                folio_con_letra = f"{id_valor.upper()}{folio_base}"
            else:
                folio_con_letra = folio_base or id_valor or "-"

            tabla_filas.append({
                "folio": folio_con_letra,
                "hora": str(row.get("hora") or "").strip() or "-",
                "icono": estado_a_icono(row.get("estado"))
            })

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
            tabla_texto = ""

            if tabla_filas:
                tabla_lineas = ["Folio | Hora | Status", "----- | ---- | ------"]
                for fila in tabla_filas:
                    tabla_lineas.append(f"{fila['folio']} | {fila['hora']} | {fila['icono']}")
                tabla_texto = "\n\n" + "\n".join(tabla_lineas)
            
            if errores:
                mensaje_resultado += f"\n⚠️ {len(errores)} error(es): " + "; ".join(errores[:3])  # Mostrar solo los primeros 3 errores
                if len(errores) > 3:
                    mensaje_resultado += f" ... y {len(errores) - 3} más"
            
            if registros_insertados == 0:
                mensaje_error = (
                    f"Error: No se pudieron insertar registros en la base de datos. "
                    f"Se procesaron {len(datos_ordenados)} registros. "
                )
                mensaje_error += (
                    f"Errores: {'; '.join(errores[:2])}"
                    if errores else "Verifica el schema de la tabla 'registros' en Supabase."
                )
                return mensaje_error + tabla_texto
            
            return mensaje_resultado + tabla_texto
            
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