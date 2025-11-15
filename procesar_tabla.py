import re
import logging
import json
from functools import lru_cache
from typing import Optional, List, Dict, Any, Tuple

from supabase import create_client, Client

from config import SUPABASE_URL, SUPABASE_KEY
from chandra_ocr import ChandraOcrError, leer_tabla as leer_tabla_chandra

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


def generar_id_unico(folio: str, hora: str) -> str:
    """Genera un ID único y determinista a partir del folio y la hora."""
    folio_limpio = re.sub(r"\W+", "", folio)
    hora_limpia = re.sub(r"\W+", "", hora)
    return f"{folio_limpio}_{hora_limpia}"


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


def _misma_fila(
    fila: Dict[str, float],
    elemento: Dict[str, float],
    tolerancia_y: float = 1.5,
) -> bool:
    """
    Determina si un elemento pertenece a una fila existente con mayor tolerancia.
    Un elemento pertenece a la fila si su centro vertical está dentro del rango
    vertical de la fila, expandido por un factor de la altura del propio elemento.
    """
    # El centro vertical del nuevo elemento
    centro_y_elemento = elemento["center_y"]

    # El rango vertical de la fila existente
    y_min_fila = fila["y_min"]
    y_max_fila = fila["y_max"]

    # La altura del nuevo elemento, para usarla como umbral dinámico
    altura_elemento = elemento["height"]

    # El margen de tolerancia se basa en la altura del elemento.
    # Un valor de 1.5 significa que el centro del elemento puede estar hasta 1.5 veces
    # su propia altura por encima o por debajo del rango de la fila.
    margen = altura_elemento * tolerancia_y

    # Comprobar si el centro del elemento cae dentro del rango extendido de la fila
    return (y_min_fila - margen) <= centro_y_elemento <= (y_max_fila + margen)


def ordenar_tokens_por_posicion(resultados_ocr: List[Any]) -> Tuple[List[str], List[List[str]]]:
    """
    Utiliza las coordenadas devueltas por el motor de OCR para ordenar los tokens de izquierda a derecha y de arriba hacia abajo.
    Usa una lógica de agrupación de filas más flexible para manejar texto manuscrito.
    """
    elementos: List[Dict[str, Any]] = []
    for entrada in resultados_ocr:
        if not (isinstance(entrada, (list, tuple)) and len(entrada) >= 2):
            continue
        
        bbox, texto = entrada[:2]
        texto_crudo = (texto or "").strip()
        if not texto_crudo:
            continue

        try:
            xs = [float(p[0]) for p in bbox]
            ys = [float(p[1]) for p in bbox]
        except (TypeError, ValueError):
            continue

        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)

        elementos.append({
            "texto": texto_crudo,
            "x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max,
            "center_x": (x_min + x_max) / 2, "center_y": (y_min + y_max) / 2,
            "height": max(y_max - y_min, 1.0),
        })

    if not elementos:
        return [], []

    # Ordenar todos los elementos por su posición vertical primero
    elementos.sort(key=lambda e: e["center_y"])

    filas_agrupadas: List[Dict[str, Any]] = []
    for elemento in elementos:
        asignado = False
        # Buscar una fila compatible
        for fila in filas_agrupadas:
            if _misma_fila(fila, elemento):
                fila["elementos"].append(elemento)
                # Actualizar el rango vertical de la fila para incluir el nuevo elemento
                fila["y_min"] = min(fila["y_min"], elemento["y_min"])
                fila["y_max"] = max(fila["y_max"], elemento["y_max"])
                asignado = True
                break
        
        if not asignado:
            # Si no se encontró fila, crear una nueva con este elemento
            filas_agrupadas.append({
                "elementos": [elemento],
                "y_min": elemento["y_min"],
                "y_max": elemento["y_max"],
            })

    # Ordenar las filas finales por su posición vertical
    filas_agrupadas.sort(key=lambda f: f["y_min"])

    filas_texto: List[List[str]] = []
    tokens_ordenados: List[str] = []
    for fila in filas_agrupadas:
        # Dentro de cada fila, ordenar los elementos por su posición horizontal
        fila["elementos"].sort(key=lambda e: e["x_min"])
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


def extraer_filas_lineal(
    filas_texto: List[List[str]], prefijo_manual: Optional[str] = None
) -> List[Dict[str, str]]:
    """
    Versión simplificada y robusta para extraer datos de filas OCR.
    Procesa cada fila de forma independiente, manteniendo solo el contexto necesario.
    """
    filas_extraidas: List[Dict[str, str]] = []

    ultimo_prefijo_visto = prefijo_manual
    ultima_letra_vista: Optional[str] = None

    logger.info(f"Procesando {len(filas_texto)} filas de OCR con lógica simplificada.")

    for idx_fila, fila_tokens in enumerate(filas_texto):
        tokens_limpios = [normalizar_token(t) for t in fila_tokens if t.strip()]
        if not tokens_limpios:
            continue

        logger.debug(f"Fila OCR {idx_fila + 1}: {tokens_limpios}")

        letra: Optional[str] = None
        folio: Optional[str] = None
        hora: Optional[str] = None
        estado: Optional[str] = None
        usar_contexto_previo = any(es_comilla(t) for t in tokens_limpios)

        if usar_contexto_previo:
            letra = ultima_letra_vista
            logger.debug(
                "  Comilla detectada. Usando contexto: letra=%s, prefijo=%s",
                letra,
                ultimo_prefijo_visto,
            )

        for token in tokens_limpios:
            if es_comilla(token):
                continue

            if not letra and len(token) == 1 and token.isalpha():
                letra = token.upper()
                ultima_letra_vista = letra
                logger.debug(f"  Letra encontrada: {letra}")
                continue

            numero_detectado = re.search(r"\d{3,}", token)
            if not folio and numero_detectado:
                numero = numero_detectado.group(0)
                if len(numero) >= 7:
                    folio = numero
                    ultimo_prefijo_visto = numero[:-4]
                    logger.debug(
                        "  Folio completo encontrado: %s. Nuevo prefijo base: %s",
                        folio,
                        ultimo_prefijo_visto,
                    )
                elif len(numero) in [3, 4] and ultimo_prefijo_visto:
                    folio = f"{ultimo_prefijo_visto}{numero}"
                    logger.debug(f"  Subfolio '{numero}' reconstruido a: {folio}")
                else:
                    folio = numero
                    logger.debug(f"  Número sin prefijo claro: {folio}")
                continue

            if not hora:
                hora_detectada = detectar_hora_en_token(token)
                if hora_detectada:
                    hora = hora_detectada
                    logger.debug(f"  Hora encontrada: {hora}")
                    continue

            if not estado:
                estado_detectado = detectar_estado_token(token)
                if estado_detectado:
                    estado = estado_detectado
                    logger.debug(
                        "  Estado encontrado: %s desde token '%s'",
                        estado,
                        token,
                    )

        if folio and hora:
            estado_final = estado or "indefinido"
            fila_resultado = {
                "letra": letra or ultima_letra_vista or "?",
                "folio": folio,
                "hora": hora,
                "estado": normalizar_estado_a_icono(estado_final),
                "id": generar_id_unico(folio, hora),
            }
            filas_extraidas.append(fila_resultado)
            logger.info(f"Fila extraída con éxito: {fila_resultado}")

    logger.info(f"Total de filas extraídas: {len(filas_extraidas)}")
    return filas_extraidas

 
def ejecutar_ocr(imagen: str) -> List[Any]:
    """Ejecuta Chandra OCR y devuelve los resultados normalizados."""
    try:
        resultados = leer_tabla_chandra(imagen)
    except ChandraOcrError:
        logger.error("Chandra OCR devolvió un error controlado.", exc_info=True)
        raise
    except Exception as exc:
        logger.error("Error inesperado ejecutando Chandra OCR.", exc_info=True)
        raise ChandraOcrError("Fallo inesperado ejecutando Chandra OCR.") from exc

    if not resultados:
        raise ChandraOcrError("Chandra OCR no devolvió texto.")

    logger.info("Chandra OCR generó %d elementos.", len(resultados))
    return resultados


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
    for fila in filas_en_orden:
        id_unico = fila.get("id")
        folio_completo = fila.get("folio")
        hora_val = fila.get("hora")
        estado_icono = fila.get("estado") or "❔"
        estado_texto = icono_a_estado(estado_icono)

        if not all([id_unico, folio_completo, hora_val]):
            logger.debug(f"Fila descartada (datos incompletos): {fila}")
            continue

        registros_preparados.append(
            {
                "id": str(id_unico).strip(),
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
    """Lee una tabla escrita a mano con Chandra OCR, limpia, ordena y guarda en Supabase."""
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