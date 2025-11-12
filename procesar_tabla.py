import easyocr
import re
import logging
import json
from functools import lru_cache
from typing import Optional, List, Dict, Any, Tuple

from supabase import create_client, Client

from config import SUPABASE_URL, SUPABASE_KEY

logger = logging.getLogger(__name__)


def _build_response(
    success: bool,
    summary: Optional[str] = None,
    table: Optional[str] = None,
    processed: Optional[int] = None,
    inserted: Optional[int] = None,
    errors: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "success": success,
        "summary": summary,
        "table": table,
        "processed": processed,
        "inserted": inserted,
        "errors": errors or [],
    }


def _extraer_detalle_error(exc: Exception) -> str:
    """
    Intenta obtener un mensaje detallado desde diferentes atributos de la excepción.
    Incluye objetos JSON serializados completos cuando están disponibles.
    """
    candidatos: List[Any] = []

    if hasattr(exc, "args") and exc.args:
        candidatos.extend(list(exc.args))

    for attr in ("message", "msg", "detail", "details", "response"):
        valor = getattr(exc, attr, None)
        if valor and valor not in candidatos:
            candidatos.append(valor)

    candidatos.append(str(exc))

    # Priorizar estructuras tipo dict
    for candidato in candidatos:
        if isinstance(candidato, dict):
            try:
                return json.dumps(candidato, ensure_ascii=False)
            except TypeError:
                return repr(candidato)

    # Intentar interpretar strings como JSON antes de devolverlos
    for candidato in candidatos:
        if isinstance(candidato, str):
            texto = candidato.strip()
            if not texto:
                continue
            try:
                datos = json.loads(texto)
                return json.dumps(datos, ensure_ascii=False)
            except json.JSONDecodeError:
                return texto

    return repr(exc)


def _formatear_error_db(row: Dict[str, str], exc: Exception) -> str:
    folio = row.get("folio") or "folio-desconocido"
    identificador = row.get("id") or "id-desconocido"
    detalle = _extraer_detalle_error(exc)
    return f"Error DB en fila {identificador} ({folio}): {detalle}"

# Prefijo base se detecta automáticamente desde los folios completos en la tabla
CHECK_SYMBOLS = {"✅", "✔", "✓", "☑", "√", "✓", "✔"}
CROSS_SYMBOLS = {"❌", "✖", "✕", "✗", "✘", "⚠", "⚠️", "x", "X"}
CHECK_WORDS = {
    "ok",
    "si",
    "sí",
    "hecho",
    "hecha",
    "listo",
    "lista",
    "list",
    "done",
    "v",
    "va",
    "yes",
    "okey",
    "finalizado",
    "finalizada",
    "terminado",
    "terminada",
    "completo",
    "completa",
    "ready",
    "check",
}
CROSS_WORDS = {
    "no",
    "pendiente",
    "pend",
    "pend.",
    "pendiente.",
    "falta",
    "faltante",
    "x",
    "fail",
    "error",
    "cancelado",
    "cancelada",
    "incompleto",
    "incompleta",
    "no ok",
}

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

    if ":" not in hora:
        if len(hora) == 4:
            hora = f"{hora[:2]}:{hora[2:]}"
        elif len(hora) == 3:
            hora = f"{hora[0]}:{hora[1:]}"
        elif len(hora) > 4 and re.fullmatch(r"\d{5,}", hora):
            # Para cadenas largas sin separadores, tomar los últimos 4 dígitos
            # bajo el supuesto de que los dígitos finales corresponden a la hora.
            hora = f"{hora[-4:-2]}:{hora[-2:]}"

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


def icono_a_estado(icono: str) -> str:
    """Convierte un icono visual a su representación textual."""
    if not icono:
        return "indefinido"

    icono_limpio = icono.strip()

    if "✅" in icono_limpio:
        return "completado"
    if any(mark in icono_limpio for mark in {"⚠", "⚠️", "❌", "✖", "✕", "✗", "✘", "x", "X"}):
        return "pendiente"
    if "❔" in icono_limpio or "?" in icono_limpio:
        return "indefinido"

    return "indefinido"


def generar_tabla_markdown(registros: List[Dict[str, str]]) -> str:
    """Genera una tabla Markdown ordenada por hora a partir de los registros procesados."""
    if not registros:
        return "No hay datos disponibles."

    filas_validas: List[Dict[str, Any]] = []

    for registro in registros:
        folio = str(registro.get("folio") or "").strip()
        hora = str(registro.get("hora") or "").strip()

        if not folio or not hora:
            logger.debug(f"Registro omitido por datos incompletos para tabla: {registro}")
            continue

        try:
            orden = valor_orden_hora(hora)
        except ValueError:
            logger.debug(f"Hora inválida omitida en tabla: '{hora}' (registro: {registro})")
            continue

        icono = registro.get("icono") or registro.get("status") or estado_a_icono(registro.get("estado"))
        if not icono:
            icono = "❔"

        filas_validas.append(
            {
                "folio": folio,
                "hora": hora,
                "status": icono,
                "orden": orden,
            }
        )

    if not filas_validas:
        return "No hay datos disponibles."

    filas_ordenadas = sorted(filas_validas, key=lambda fila: (fila["orden"], fila["folio"]))

    tabla_lineas = ["Folio | Hora | Status", "----- | ---- | ------"]
    for fila in filas_ordenadas:
        tabla_lineas.append(f"{fila['folio']} | {fila['hora']} | {fila['status']}")

    return "\n".join(tabla_lineas)


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


def extraer_letra_y_numero(token: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Extrae una letra inicial seguida de un bloque numérico (folio) de un token.
    Devuelve (letra, numero) o (None, None) si no se detecta el patrón.
    """
    if not token:
        return None, None

    token_limpio = token.strip()
    if not token_limpio:
        return None, None

    # Patrones comunes: letra + número (con o sin separador típico)
    patrones = [
        r"^([A-Za-z])[-_:\s]?(\d{3,})$",
        r"([A-Za-z])(\d{3,})",
    ]

    for patron in patrones:
        match = re.search(patron, token_limpio)
        if match:
            letra = match.group(1).upper()
            numero = match.group(2)
            return letra, numero

    return None, None


def detectar_estado_token(token: str) -> Optional[str]:
    """Intenta inferir el estado a partir de un token OCR."""
    if not token:
        return None

    token_limpio = token.strip()
    if not token_limpio:
        return None

    token_lower = token_limpio.lower()
    token_sin_signos = re.sub(r"[^a-záéíóúüñ0-9]", "", token_lower)

    if "?" in token_limpio or "¿" in token_limpio or "❔" in token_limpio:
        return "indefinido"

    # Verificar símbolos de check (completado)
    if any(mark in token_limpio for mark in CHECK_SYMBOLS) or token_lower in CHECK_WORDS:
        return "completado"
    if token_sin_signos in CHECK_WORDS:
        return "completado"

    # Verificar símbolos de cross/pendiente
    if any(mark in token_limpio for mark in CROSS_SYMBOLS) or token_lower in CROSS_WORDS:
        return "pendiente"
    if token_sin_signos in CROSS_WORDS or "no ok" in token_lower.replace("_", " "):
        return "pendiente"

    return None


def normalizar_estado_a_icono(estado_texto: str) -> str:
    """Normaliza el estado de texto a un icono (✅ o ⚠️)."""
    if not estado_texto:
        return "❔"
    
    estado_normalizado = estado_texto.strip().lower()
    if estado_normalizado == "completado":
        return "✅"
    if estado_normalizado == "pendiente":
        return "⚠️"
    return "❔"


def detectar_hora_en_token(token: str) -> Optional[str]:
    """Intenta detectar una hora válida dentro de un token OCR."""
    if not token:
        return None

    token_limpio = normalizar_token(token)
    if not token_limpio:
        return None

    # Primero buscar patrones explícitos con separador.
    match_hora = re.search(r"\d{1,2}[:.]\d{2}", token_limpio)
    if match_hora:
        hora_cruda = match_hora.group(0).replace(".", ":")
        hora_limpia = limpiar_hora(hora_cruda)
        if hora_limpia:
            logger.debug(f"  Hora detectada (con separador) desde '{token}': {hora_limpia}")
            return hora_limpia

    # Si no se encontró separador, intentar con solo dígitos (ej. 742 -> 07:42).
    digitos = re.sub(r"\D", "", token_limpio)
    if not digitos or len(digitos) < 3:
        return None

    fragmentos: List[str] = []
    vistos = set()

    # Priorizar fragmentos más largos para evitar perder la decena de la hora.
    if len(digitos) in (3, 4) and digitos not in vistos:
        fragmentos.append(digitos)
        vistos.add(digitos)
    if len(digitos) >= 4:
        frag = digitos[-4:]
        if frag not in vistos:
            fragmentos.append(frag)
            vistos.add(frag)
    if len(digitos) >= 3:
        frag = digitos[-3:]
        if frag not in vistos:
            fragmentos.append(frag)
            vistos.add(frag)

    for fragmento in fragmentos:
        longitud = len(fragmento)
        for posicion in range(1, longitud):
            parte_horas = fragmento[:posicion]
            parte_minutos = fragmento[posicion:]
            if len(parte_minutos) != 2:
                continue
            hora_candidata = limpiar_hora(f"{parte_horas}:{parte_minutos}")
            if hora_candidata:
                logger.debug(
                    "  Hora detectada (heurística) desde '%s' usando fragmento '%s': %s",
                    token,
                    fragmento,
                    hora_candidata,
                )
                return hora_candidata

    return None


def _misma_fila(fila: Dict[str, float], elemento: Dict[str, float], tolerancia: float = 0.6) -> bool:
    """
    Determina si un token pertenece a la misma fila en función de su posición vertical.
    Aumentada la tolerancia a 0.6 para ser más flexible con tablas escritas a mano.
    """
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

            token_digits = re.sub(r"\D", "", token_limpio)
            if len(token_digits) >= 7:
                numero_completo = token_digits
                prefijo_detectado = numero_completo[:-4]
                logger.info(
                    "Prefijo base detectado automáticamente: '%s' desde token '%s' (normalizado a '%s')",
                    prefijo_detectado,
                    token_limpio,
                    numero_completo,
                )
                return prefijo_detectado
    
    # Si no se detecta, retornar None (se intentará inferir de los números encontrados)
    logger.warning("No se pudo detectar prefijo base automáticamente. Se intentará inferir de los números encontrados.")
    return None


def extraer_filas_lineal(filas_texto: List[List[str]], prefijo_manual: Optional[str] = None) -> List[Dict[str, str]]:
    """
    Extrae filas agrupando tokens por bloques de texto secuenciales (filas OCR).
    Reconstruye folios completos concatenando el prefijo base con subfolios de 3-4 dígitos.
    Maneja comillas como indicadores de repetición de letra y prefijo.
    Devuelve una lista limpia de diccionarios con: letra, folio, hora, estado.
    """
    # Detectar prefijo base automáticamente
    prefijo_base = detectar_prefijo_base(filas_texto, prefijo_manual)
    
    filas_extraidas: List[Dict[str, str]] = []
    letra_actual: Optional[str] = None
    fila_en_construccion: Optional[Dict[str, Optional[str]]] = None
    
    logger.info(f"Procesando {len(filas_texto)} filas OCR detectadas")
    
    for idx_fila, fila_tokens in enumerate(filas_texto):
        if not fila_tokens:
            # Si hay una fila en construcción, intentar completarla
            if fila_en_construccion and fila_en_construccion.get("folio") and fila_en_construccion.get("hora"):
                letra_final = fila_en_construccion.get("letra") or letra_actual or "?"
                estado_texto = fila_en_construccion.get("estado") or "indefinido"
                estado_icono = normalizar_estado_a_icono(estado_texto)
                
                filas_extraidas.append({
                    "letra": letra_final,
                    "folio": fila_en_construccion["folio"],
                    "hora": fila_en_construccion["hora"],
                    "estado": estado_icono,
                })
                logger.info(f"Fila completada desde construcción: {filas_extraidas[-1]}")
                fila_en_construccion = None
            continue
        
        # Normalizar tokens de la fila
        tokens_limpios = [normalizar_token(t) for t in fila_tokens]
        
        if not tokens_limpios:
            continue
        
        logger.debug(f"Fila {idx_fila + 1}: tokens = {tokens_limpios}")
        
        # Variables para esta fila
        letra: Optional[str] = None
        folio: Optional[str] = None
        hora: Optional[str] = None
        estado: Optional[str] = None
        tiene_comilla = False
        
        # Procesar cada token de la fila
        for token in tokens_limpios:
            # Detectar comilla (indica repetición de letra y prefijo)
            if es_comilla(token):
                tiene_comilla = True
                # Si hay una fila en construcción, usar sus valores
                if fila_en_construccion:
                    letra = fila_en_construccion.get("letra") or letra_actual
                    # El prefijo se mantiene del contexto
                elif letra_actual:
                    letra = letra_actual
                logger.debug(f"  Comilla detectada (repetir letra: {letra})")
                continue

            numero_detectado: Optional[str] = None

            # Detectar tokens con letra y número juntos (ej. F164172415)
            letra_token, numero_token = extraer_letra_y_numero(token)
            if letra_token:
                letra = letra_token
                letra_actual = letra_token
                logger.debug(f"  Letra + folio detectados en token '{token}': letra={letra}, numero={numero_token}")
                if numero_token:
                    numero_detectado = numero_token
            
            # Detectar letra (una sola letra mayúscula o minúscula)
            if (letra is None) and len(token) == 1 and token.isalpha():
                letra = token.upper()
                letra_actual = letra
                logger.debug(f"  Letra detectada: {letra}")
                continue
            
            # Detectar número (folio completo o subfolio)
            if numero_detectado is None:
                match_numero = re.fullmatch(r"\d{3,}", token)
                if not match_numero:
                    match_numero = re.search(r"\d{3,}", token)
                if match_numero:
                    numero_detectado = match_numero.group(0)

            if numero_detectado:
                numero = numero_detectado

                # Si el número tiene 7+ dígitos, es un folio completo
                longitud_folio_actual = len(folio) if folio else 0

                if len(numero) >= 7:
                    # El prefijo es todo excepto los últimos 4 dígitos
                    nuevo_prefijo = numero[:-4]
                    if len(numero) > longitud_folio_actual:
                        folio = numero
                        logger.debug(f"  Folio completo detectado: {folio} (prefijo: {nuevo_prefijo})")
                    prefijo_base = nuevo_prefijo  # Actualizar prefijo para siguientes filas
                # Si tiene 3-4 dígitos, es un subfolio (necesita prefijo)
                elif len(numero) in [3, 4]:
                    if prefijo_base:
                        candidato = f"{prefijo_base}{numero}"
                    else:
                        # Sin prefijo disponible, usar el número como está
                        candidato = numero
                    if len(candidato) > longitud_folio_actual:
                        folio = candidato
                        logger.debug(f"  Subfolio detectado: {numero} -> folio completo: {folio}")
                # Si tiene 5-6 dígitos, podría ser un folio completo o necesitar prefijo
                elif len(numero) in [5, 6]:
                    if prefijo_base:
                        # Intentar detectar si ya incluye el prefijo
                        if numero.startswith(prefijo_base):
                            candidato = numero
                        else:
                            # Asumir que es un subfolio más largo
                            candidato = f"{prefijo_base}{numero}"
                    else:
                        # Sin prefijo, usar el número como está
                        candidato = numero

                    if len(candidato) > longitud_folio_actual:
                        folio = candidato
                        if candidato == numero and prefijo_base and numero.startswith(prefijo_base):
                            logger.debug(f"  Folio detectado (con prefijo): {folio}")
                        elif prefijo_base:
                            logger.debug(f"  Subfolio largo detectado: {numero} -> folio: {folio}")
                        else:
                            logger.debug(f"  Número medio detectado sin prefijo: {numero}")
            
            # Detectar hora con heurísticas más flexibles
            if hora is None:
                hora_detectada = detectar_hora_en_token(token)
                if hora_detectada:
                    hora = hora_detectada
                    logger.debug(f"  Hora detectada: {hora}")
            
            # Detectar estado (símbolos o palabras)
            estado_detectado = detectar_estado_token(token)
            if estado_detectado:
                estado = estado_detectado
                logger.debug(f"  Estado detectado: {estado} desde token '{token}'")
                continue
        
        # Si tenemos comilla pero no tenemos letra, usar la letra actual
        if tiene_comilla and not letra:
            letra = letra_actual
        
        # Si tenemos folio o hora, actualizar o crear la fila en construcción
        if folio or hora:
            if not fila_en_construccion:
                fila_en_construccion = {
                    "letra": letra or letra_actual,
                    "folio": None,
                    "hora": None,
                    "estado": None,
                }
            
            if folio:
                fila_en_construccion["folio"] = folio
            if hora:
                fila_en_construccion["hora"] = hora
            if letra:
                fila_en_construccion["letra"] = letra
            if estado:
                fila_en_construccion["estado"] = estado
            
            logger.debug(f"  Fila en construcción actualizada: {fila_en_construccion}")
        
        # Si tenemos folio y hora completos, crear la fila final
        if folio and hora:
            letra_final = letra or fila_en_construccion.get("letra") if fila_en_construccion else None
            letra_final = letra_final or letra_actual or "?"
            
            estado_texto = estado or (fila_en_construccion.get("estado") if fila_en_construccion else None) or "indefinido"
            estado_icono = normalizar_estado_a_icono(estado_texto)
            
            fila_resultado = {
                "letra": letra_final,
                "folio": folio,
                "hora": hora,
                "estado": estado_icono,
            }
            
            filas_extraidas.append(fila_resultado)
            logger.info(f"Fila extraída: {fila_resultado}")
            
            # Limpiar la fila en construcción
            fila_en_construccion = None
        elif not folio and not hora:
            # Si no hay folio ni hora en esta fila, pero hay una fila en construcción,
            # mantenerla para la siguiente iteración
            if fila_en_construccion:
                logger.debug(f"Manteniendo fila en construcción: {fila_en_construccion}")
    
    # Al final, procesar cualquier fila en construcción que quede
    if fila_en_construccion and fila_en_construccion.get("folio") and fila_en_construccion.get("hora"):
        letra_final = fila_en_construccion.get("letra") or letra_actual or "?"
        estado_texto = fila_en_construccion.get("estado") or "indefinido"
        estado_icono = normalizar_estado_a_icono(estado_texto)
        
        filas_extraidas.append({
            "letra": letra_final,
            "folio": fila_en_construccion["folio"],
            "hora": fila_en_construccion["hora"],
            "estado": estado_icono,
        })
        logger.info(f"Fila final completada: {filas_extraidas[-1]}")
    
    logger.info(f"Total de filas extraídas: {len(filas_extraidas)}")
    return filas_extraidas

 
def ejecutar_ocr(imagen: str) -> List[Any]:
    """Ejecuta EasyOCR sobre la imagen recibida."""
    return reader.readtext(imagen, detail=1, paragraph=False)


def obtener_filas_desde_ocr(
    resultados_ocr: List[Any], prefijo_manual: Optional[str] = None
) -> Tuple[List[Dict[str, str]], List[List[str]], List[str]]:
    """
    Reconstruye las filas de la tabla a partir de los resultados crudos de OCR.
    Devuelve las filas normalizadas, las filas detectadas y los tokens ordenados.
    """
    tokens_ordenados, filas_detectadas = ordenar_tokens_por_posicion(resultados_ocr)
    if not tokens_ordenados or not filas_detectadas:
        return [], filas_detectadas, tokens_ordenados

    filas = extraer_filas_lineal(filas_detectadas, prefijo_manual)
    return filas, filas_detectadas, tokens_ordenados


def preparar_registros_para_supabase(
    filas_en_orden: List[Dict[str, str]]
) -> Tuple[List[Dict[str, str]], str]:
    """
    Toma las filas detectadas y produce registros listos para Supabase junto con
    la representación en tabla Markdown.
    """
    logger.info(f"Se extrajeron {len(filas_en_orden)} filas candidatas")

    registros_preparados: List[Dict[str, str]] = []
    for idx, fila in enumerate(filas_en_orden, start=1):
        letra = fila.get("letra") or f"L{idx}"
        folio_completo = fila.get("folio")
        hora_val = fila.get("hora")
        estado_icono = fila.get("estado") or "❔"  # Estado ya viene como icono (✅/⚠️/❔)
        estado_texto = icono_a_estado(estado_icono)

        if not folio_completo or not hora_val:
            logger.debug(f"Fila descartada (datos incompletos): {fila}")
            continue

        registros_preparados.append(
            {
                "id": str(letra).strip(),
                "folio": str(folio_completo).strip(),
                "hora": str(hora_val).strip(),
                "estado": estado_texto,
                "icono": estado_icono,
            }
        )

    tabla_texto = generar_tabla_markdown(registros_preparados)
    return registros_preparados, tabla_texto


def insertar_registros_supabase(registros_preparados: List[Dict[str, str]]) -> Tuple[int, List[str]]:
    """Inserta los registros en Supabase y acumula errores por fila."""
    supabase = get_supabase_client()
    registros_insertados = 0
    errores: List[str] = []

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
            logger.error(f"Error insertando {row}: {e}")
            error_normalizado = _formatear_error_db(row, e)
            if "duplicate key" in str(e).lower():
                errores.append(
                    f"Clave duplicada en fila {row.get('id')}: {row.get('folio')}. Detalle: {error_normalizado}"
                )
            else:
                errores.append(error_normalizado)

    return registros_insertados, errores


def procesar_tabla(imagen: str, prefijo_manual: Optional[str] = None):
    """Lee una tabla escrita a mano con EasyOCR, limpia, ordena y guarda en Supabase."""
    try:
        logger.info(f"Iniciando procesamiento de tabla desde imagen: {imagen}")

        try:
            resultados_ocr = ejecutar_ocr(imagen)
        except Exception as e:
            logger.error(f"Error en OCR: {e}")
            detalle = _extraer_detalle_error(e)
            return _build_response(
                False,
                summary="Error: No se pudo leer el texto de la imagen.",
                errors=[detalle],
            )

        if not resultados_ocr:
            return _build_response(
                False,
                summary="Error: No se detectó texto en la imagen.",
            )

        logger.info(f"OCR detectó {len(resultados_ocr)} elementos brutos")
        logger.debug(f"Elementos OCR brutos: {resultados_ocr}")

        filas, filas_detectadas, tokens_ordenados = obtener_filas_desde_ocr(resultados_ocr, prefijo_manual)

        if not tokens_ordenados or not filas_detectadas:
            return _build_response(
                False,
                summary="Error: No se pudo reconstruir el orden de lectura de la tabla.",
            )

        logger.info(
            f"{len(tokens_ordenados)} tokens tras ordenar por posición (filas detectadas: {len(filas_detectadas)})"
        )
        logger.debug(f"Filas detectadas (OCR): {filas_detectadas}")

        if not filas:
            return _build_response(
                False,
                summary="Error: No se pudieron extraer filas válidas.",
                errors=[f"Elementos detectados (muestra): {filas_detectadas[:3] if filas_detectadas else 'ninguno'}"],
            )

        registros_preparados, tabla_texto = preparar_registros_para_supabase(filas)

        if not registros_preparados:
            return _build_response(
                False,
                summary="Error: No se pudieron preparar registros válidos a partir del OCR.",
                table=tabla_texto,
            )

        try:
            registros_insertados, errores = insertar_registros_supabase(registros_preparados)
        except Exception as e:
            logger.error(f"Error Supabase: {e}", exc_info=True)
            detalle = _extraer_detalle_error(e)
            return _build_response(
                False,
                summary="Error: No se pudo conectar a Supabase.",
                errors=[detalle],
            )

        resumen = f"Procesados {len(registros_preparados)} registros, {registros_insertados} insertados exitosamente."

        if errores:
            return _build_response(
                False,
                summary=resumen,
                table=tabla_texto,
                processed=len(registros_preparados),
                inserted=registros_insertados,
                errors=errores,
            )

        return _build_response(
            True,
            summary=resumen,
            table=tabla_texto,
            processed=len(registros_preparados),
            inserted=registros_insertados,
        )

    except Exception as e:
        logger.error(f"Error general: {e}", exc_info=True)
        detalle = _extraer_detalle_error(e)
        return _build_response(
            False,
            summary="Error: Fallo inesperado procesando la tabla.",
            errors=[detalle],
        )