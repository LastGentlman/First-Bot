import easyocr
import re
import logging
from functools import lru_cache
from typing import Optional, List, Dict, Any, Tuple

from supabase import create_client, Client

from config import SUPABASE_URL, SUPABASE_KEY

logger = logging.getLogger(__name__)

# Prefijo base se detecta automáticamente desde los folios completos en la tabla
CHECK_SYMBOLS = {"✅", "✔", "✓", "☑", "√", "✓", "✔"}
CROSS_SYMBOLS = {"❌", "✖", "✕", "✗", "✘", "⚠", "⚠️", "x", "X"}
CHECK_WORDS = {"ok", "si", "sí", "hecho", "listo", "done", "v", "va", "yes"}
CROSS_WORDS = {"no", "pendiente", "falta", "x", "fail"}

reader = easyocr.Reader(["es", "en"], gpu=False)


@lru_cache(maxsize=1)
def get_supabase_client() -> Client:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError(
            "SUPABASE_URL y SUPABASE_KEY no están configuradas. "
            "Define las variables de entorno antes de ejecutar la aplicación."
        )
    return create_client(SUPABASE_URL, SUPABASE_KEY)


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

    hora = re.sub(r"[^0-9:]", "", hora)

    if hora.count(":") > 1:
        primera_pos = hora.find(":")
        hora = hora[: primera_pos + 1] + hora[primera_pos + 1 :].replace(":", "")

    if ":" not in hora and len(hora) == 4:
        hora = f"{hora[:2]}:{hora[2:]}"

    match = re.match(r"^(\d{1,2}):(\d{2})$", hora)
    if not match:
        return None

    h, m = int(match[1]), match[2]
    if h > 29 or int(m) > 59:
        return None

    return f"{h:02}:{m}"


def valor_orden_hora(hora: str) -> int:
    """Convierte una hora en minutos corridos con offset para madrugada."""
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
    """Convierte símbolos OCR a estado de texto."""
    if not simbolo:
        return "indefinido"

    simbolo_limpio = simbolo.strip()
    if not simbolo_limpio:
        return "indefinido"

    simbolo_lower = simbolo_limpio.lower()

    if any(mark in simbolo_limpio for mark in CHECK_SYMBOLS) or simbolo_lower in CHECK_WORDS:
        return "completado"
    if any(mark in simbolo_limpio for mark in CROSS_SYMBOLS) or simbolo_lower in CROSS_WORDS:
        return "pendiente"
    if "?" in simbolo_limpio:
        return "pendiente"

    return "indefinido"


def estado_a_icono(estado: str) -> str:
    """Mapea el estado textual a un icono."""
    if not estado:
        return "❔"
    estado_normalizado = estado.strip().lower()
    if estado_normalizado == "completado":
        return "✅"
    if estado_normalizado == "pendiente":
        return "⚠️"
    return "❔"


def es_comilla(texto: str) -> bool:
    """Detecta si un texto es una comilla o símbolo de repetición."""
    texto_limpio = texto.strip()
    return texto_limpio in ['"', "'", "''", '""', ",,", "٠٠", "ʻʻ", "``"]


def normalizar_token(texto: str) -> str:
    """Limpia ruido común del OCR (comillas, espacios extra)."""
    if not texto:
        return ""

    texto_limpio = texto.strip()
    if not texto_limpio:
        return ""

    reemplazos_comillas = {
        "“": '"',
        "”": '"',
        "«": '"',
        "»": '"',
    }

    for original, reemplazo in reemplazos_comillas.items():
        texto_limpio = texto_limpio.replace(original, reemplazo)

    marcas_repeticion = {'"', "'", "''", '""', ",,", "٠٠", "ʻʻ", "``"}
    if texto_limpio in marcas_repeticion:
        return texto_limpio

    # Conserva la comilla doble si es el único carácter del token (marca de repetición habitual).
    if len(texto_limpio) > 1:
        texto_limpio = texto_limpio.replace('"', "")

    return texto_limpio


def detectar_estado_token(token: str) -> Optional[str]:
    """Intenta inferir el estado a partir de un token OCR."""
    if not token:
        return None

    token_limpio = token.strip()
    if not token_limpio:
        return None

    token_lower = token_limpio.lower()

    # Verificar símbolos de check (completado)
    if any(mark in token_limpio for mark in CHECK_SYMBOLS) or token_lower in CHECK_WORDS:
        return "completado"
    
    # Verificar símbolos de cross/pendiente
    if any(mark in token_limpio for mark in CROSS_SYMBOLS) or token_lower in CROSS_WORDS:
        return "pendiente"

    return None


def normalizar_estado_a_icono(estado_texto: str) -> str:
    """Normaliza el estado de texto a un icono (✅ o ⚠️)."""
    if not estado_texto:
        return "⚠️"
    
    estado_normalizado = estado_texto.strip().lower()
    if estado_normalizado == "completado":
        return "✅"
    return "⚠️"


def _misma_fila(fila: Dict[str, float], elemento: Dict[str, float], tolerancia: float = 0.45) -> bool:
    """Determina si un token pertenece a la misma fila en función de su posición vertical."""
    fila_altura = max(fila["y_max"] - fila["y_min"], 1.0)
    elemento_altura = max(elemento["height"], 1.0)
    margen = max(fila_altura, elemento_altura) * tolerancia

    por_debajo = elemento["y_min"] > fila["y_max"] + margen
    por_encima = elemento["y_max"] < fila["y_min"] - margen

    return not (por_debajo or por_encima)


def ordenar_tokens_por_posicion(resultados_ocr: List[Any]) -> Tuple[List[str], List[List[str]]]:
    """
    Utiliza las coordenadas devueltas por EasyOCR para ordenar los tokens de izquierda a derecha y de arriba hacia abajo.
    Devuelve la lista plana de tokens en orden de lectura y, adicionalmente, las filas detectadas para depuración.
    """
    elementos: List[Dict[str, Any]] = []

    for entrada in resultados_ocr:
        if not entrada:
            continue

        if len(entrada) == 3:
            bbox, texto, confianza = entrada
        elif len(entrada) == 2:
            bbox, texto = entrada
            confianza = None
        else:
            logger.debug(f"Formato de resultado OCR inesperado: {entrada}")
            continue

        texto_crudo = (texto or "").strip()
        if not texto_crudo:
            continue

        try:
            xs = [float(p[0]) for p in bbox]
            ys = [float(p[1]) for p in bbox]
        except (TypeError, ValueError) as exc:
            logger.debug(f"No se pudieron extraer coordenadas de {entrada}: {exc}")
            continue

        x_min = min(xs)
        x_max = max(xs)
        y_min = min(ys)
        y_max = max(ys)

        elementos.append(
            {
                "texto": texto_crudo,
                "confianza": confianza,
                "x_min": x_min,
                "x_max": x_max,
                "y_min": y_min,
                "y_max": y_max,
                "center_x": (x_min + x_max) / 2,
                "center_y": (y_min + y_max) / 2,
                "width": max(x_max - x_min, 1.0),
                "height": max(y_max - y_min, 1.0),
            }
        )

    if not elementos:
        return [], []

    filas: List[Dict[str, Any]] = []

    for elemento in sorted(elementos, key=lambda e: e["center_y"]):
        asignado = False
        for fila in filas:
            if _misma_fila(fila, elemento):
                fila["elementos"].append(elemento)
                fila["y_min"] = min(fila["y_min"], elemento["y_min"])
                fila["y_max"] = max(fila["y_max"], elemento["y_max"])
                asignado = True
                break
        if not asignado:
            filas.append(
                {
                    "elementos": [elemento],
                    "y_min": elemento["y_min"],
                    "y_max": elemento["y_max"],
                }
            )

    filas_ordenadas = sorted(filas, key=lambda f: f["y_min"])
    filas_texto: List[List[str]] = []
    tokens_ordenados: List[str] = []

    for fila in filas_ordenadas:
        fila["elementos"].sort(key=lambda e: e["center_x"])
        fila_texto = [elem["texto"] for elem in fila["elementos"]]
        filas_texto.append(fila_texto)
        tokens_ordenados.extend(fila_texto)

    return tokens_ordenados, filas_texto


def detectar_prefijo_base(filas_texto: List[List[str]], prefijo_manual: Optional[str] = None) -> Optional[str]:
    """
    Detecta automáticamente el prefijo base del folio desde la primera fila.
    Toma todos los dígitos excepto los últimos 4 del primer número largo encontrado.
    Retorna None si no se puede detectar (en cuyo caso se intentará inferir de los números encontrados).
    """
    if prefijo_manual:
        logger.info(f"Usando prefijo manual: {prefijo_manual}")
        return prefijo_manual
    
    # Buscar en las primeras filas un número largo que contenga el prefijo completo
    for fila in filas_texto[:5]:  # Revisar las primeras 5 filas
        for token in fila:
            token_limpio = normalizar_token(token)
            if not token_limpio:
                continue
            
            # Buscar números de 7 o más dígitos (prefijo + 4 dígitos mínimo)
            match = re.fullmatch(r"\d{7,}", token_limpio)
            if match:
                numero_completo = match.group(0)
                # Tomar todos los dígitos excepto los últimos 4
                if len(numero_completo) > 4:
                    prefijo_detectado = numero_completo[:-4]
                    logger.info(f"Prefijo base detectado automáticamente: '{prefijo_detectado}' desde número '{numero_completo}'")
                    return prefijo_detectado
    
    # Si no se detecta, retornar None (se intentará inferir de los números encontrados)
    logger.warning("No se pudo detectar prefijo base automáticamente. Se intentará inferir de los números encontrados.")
    return None


def extraer_filas_lineal(filas_texto: List[List[str]], prefijo_manual: Optional[str] = None) -> List[Dict[str, str]]:
    """
    Extrae filas agrupando tokens por bloques de texto secuenciales (filas OCR).
    Reconstruye folios completos concatenando el prefijo base con subfolios de 3-4 dígitos.
    Devuelve una lista limpia de diccionarios con: letra, folio, hora, estado.
    """
    # Detectar prefijo base automáticamente
    prefijo_base = detectar_prefijo_base(filas_texto, prefijo_manual)
    
    filas_extraidas: List[Dict[str, str]] = []
    letra_actual: Optional[str] = None
    
    logger.info(f"Procesando {len(filas_texto)} filas OCR detectadas")
    
    for idx_fila, fila_tokens in enumerate(filas_texto):
        if not fila_tokens:
            continue
        
        # Normalizar tokens de la fila
        tokens_limpios = [normalizar_token(t) for t in fila_tokens]
        tokens_limpios = [t for t in tokens_limpios if t and not es_comilla(t)]
        
        if not tokens_limpios:
            continue
        
        logger.debug(f"Fila {idx_fila + 1}: tokens = {tokens_limpios}")
        
        # Variables para esta fila
        letra: Optional[str] = None
        folio: Optional[str] = None
        hora: Optional[str] = None
        estado: Optional[str] = None
        
        # Procesar cada token de la fila
        for token in tokens_limpios:
            # Detectar letra (una sola letra mayúscula o minúscula)
            if len(token) == 1 and token.isalpha():
                letra = token.upper()
                letra_actual = letra
                logger.debug(f"  Letra detectada: {letra}")
                continue
            
            # Detectar número (folio completo o subfolio)
            match_numero = re.fullmatch(r"\d{3,}", token)
            if match_numero:
                numero = match_numero.group(0)
                
                # Si el número tiene 7+ dígitos, es un folio completo
                if len(numero) >= 7:
                    # El prefijo es todo excepto los últimos 4 dígitos
                    nuevo_prefijo = numero[:-4]
                    subfolio = numero[-4:]
                    folio = numero
                    prefijo_base = nuevo_prefijo  # Actualizar prefijo para siguientes filas
                    logger.debug(f"  Folio completo detectado: {folio} (prefijo: {nuevo_prefijo}, subfolio: {subfolio})")
                # Si tiene 3-4 dígitos, es un subfolio (necesita prefijo)
                elif len(numero) in [3, 4]:
                    if prefijo_base:
                        folio = f"{prefijo_base}{numero}"
                        logger.debug(f"  Subfolio detectado: {numero} -> folio completo: {folio}")
                    else:
                        # Sin prefijo disponible, usar el número como está (se intentará detectar prefijo más adelante)
                        folio = numero
                        logger.debug(f"  Número corto detectado sin prefijo: {numero} (se usará como está)")
                # Si tiene 5-6 dígitos, podría ser un folio completo o necesitar prefijo
                elif len(numero) in [5, 6]:
                    if prefijo_base:
                        # Intentar detectar si ya incluye el prefijo
                        if numero.startswith(prefijo_base):
                            folio = numero
                            logger.debug(f"  Folio detectado (con prefijo): {folio}")
                        else:
                            # Asumir que es un subfolio más largo
                            folio = f"{prefijo_base}{numero}"
                            logger.debug(f"  Subfolio largo detectado: {numero} -> folio: {folio}")
                    else:
                        # Sin prefijo, usar el número como está
                        folio = numero
                        logger.debug(f"  Número medio detectado sin prefijo: {numero} (se usará como está)")
                continue
            
            # Detectar hora (formato HH:MM o HH.MM)
            match_hora = re.search(r"\d{1,2}[:.]\d{2}", token)
            if match_hora:
                hora_cruda = match_hora.group(0)
                hora_limpia = limpiar_hora(hora_cruda.replace(".", ":"))
                if hora_limpia:
                    hora = hora_limpia
                    logger.debug(f"  Hora detectada: {hora}")
                continue
            
            # Detectar estado (símbolos o palabras)
            estado_detectado = detectar_estado_token(token)
            if estado_detectado:
                estado = estado_detectado
                logger.debug(f"  Estado detectado: {estado} desde token '{token}'")
                continue
        
        # Si tenemos los datos mínimos (folio y hora), crear la fila
        if folio and hora:
            # Usar letra actual si no se detectó en esta fila
            letra_final = letra or letra_actual or "?"
            
            # Normalizar estado a icono (✅ o ⚠️)
            # Si no se detectó estado, marcar como pendiente (⚠️)
            estado_texto = estado or "pendiente"
            estado_icono = normalizar_estado_a_icono(estado_texto)
            
            fila_resultado = {
                "letra": letra_final,
                "folio": folio,
                "hora": hora,
                "estado": estado_icono,  # Estado como icono (✅ o ⚠️)
            }
            
            filas_extraidas.append(fila_resultado)
            logger.info(f"Fila extraída: {fila_resultado}")
        else:
            # Fila incompleta
            datos_faltantes = []
            if not folio:
                datos_faltantes.append("folio")
            if not hora:
                datos_faltantes.append("hora")
            logger.warning(f"Fila {idx_fila + 1} incompleta (faltan: {', '.join(datos_faltantes)}). Tokens: {tokens_limpios}")
    
    logger.info(f"Total de filas extraídas: {len(filas_extraidas)}")
    return filas_extraidas


def procesar_tabla(imagen: str, prefijo_manual: Optional[str] = None):
    """Lee una tabla escrita a mano con EasyOCR, limpia, ordena y guarda en Supabase."""
    try:
        logger.info(f"Iniciando procesamiento de tabla desde imagen: {imagen}")

        try:
            result = reader.readtext(imagen, detail=1, paragraph=False)
        except Exception as e:
            logger.error(f"Error en OCR: {e}")
            return f"Error: No se pudo leer el texto de la imagen. {str(e)}"

        if not result:
            return "Error: No se detectó texto en la imagen."

        logger.info(f"OCR detectó {len(result)} elementos brutos")
        logger.debug(f"Elementos OCR brutos: {result}")

        tokens_ordenados, filas_detectadas = ordenar_tokens_por_posicion(result)

        if not tokens_ordenados or not filas_detectadas:
            return "Error: No se pudo reconstruir el orden de lectura de la tabla."

        logger.info(f"{len(tokens_ordenados)} tokens tras ordenar por posición (filas detectadas: {len(filas_detectadas)})")
        logger.debug(f"Filas detectadas (OCR): {filas_detectadas}")

        # Usar las filas agrupadas directamente en lugar de tokens lineales
        filas = extraer_filas_lineal(filas_detectadas, prefijo_manual)

        if not filas:
            return f"Error: No se pudieron extraer filas válidas. Elementos detectados: {filas_detectadas[:3] if filas_detectadas else 'ninguno'}"

        logger.info(f"Se extrajeron {len(filas)} filas candidatas")

        filas_en_orden = filas

        registros_preparados: List[Dict[str, str]] = []
        for idx, fila in enumerate(filas_en_orden, start=1):
            letra = fila.get("letra") or f"L{idx}"
            folio_completo = fila.get("folio")
            hora_val = fila.get("hora")
            estado_icono = fila.get("estado") or "⚠️"  # Estado ya viene como icono (✅ o ⚠️)

            if not folio_completo or not hora_val:
                logger.debug(f"Fila descartada (datos incompletos): {fila}")
                continue

            registros_preparados.append(
                {
                    "id": str(letra).strip(),
                    "folio": str(folio_completo).strip(),
                    "hora": str(hora_val).strip(),
                    "estado": str(estado_icono).strip(),  # Estado como icono
                    "icono": estado_icono,  # Mantener compatibilidad
                }
            )

        if not registros_preparados:
            return "Error: No se pudieron preparar registros válidos a partir del OCR."

        try:
            supabase = get_supabase_client()
            registros_insertados = 0
            errores = []

            for row in registros_preparados:
                try:
                    datos_insert = {
                        "id": row["id"],
                        "folio": row["folio"],
                        "hora": row["hora"],
                        "estado": row["estado"],
                    }

                    if not all(datos_insert.values()):
                        errores.append(f"Datos vacíos: {datos_insert}")
                        continue

                    supabase.table("registros").insert(datos_insert).execute()
                    registros_insertados += 1
                    logger.debug(f"Insertado: {datos_insert}")

                except Exception as e:
                    error_msg = str(e)
                    logger.error(f"Error insertando {row}: {error_msg}")

                    if "duplicate key" in error_msg.lower():
                        errores.append(f"ID duplicado: {row.get('id')}")
                    else:
                        errores.append(f"Error DB: {error_msg[:50]}")

            mensaje = f"Procesados {len(registros_preparados)} registros, {registros_insertados} insertados exitosamente."

            tabla_lineas = ["Folio | Hora | Status", "----- | ---- | ------"]
            for row in registros_preparados:
                tabla_lineas.append(f"{row['id']}{row['folio']} | {row['hora']} | {row['icono']}")
            mensaje += "\n\n" + "\n".join(tabla_lineas)

            if errores:
                mensaje += f"\n\n⚠️ {len(errores)} error(es): {'; '.join(errores[:2])}"

            return mensaje

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error Supabase: {error_msg}", exc_info=True)
            return f"Error: No se pudo conectar a Supabase. {error_msg[:100]}"

    except Exception as e:
        logger.error(f"Error general: {e}", exc_info=True)
        return f"Error: {str(e)}"