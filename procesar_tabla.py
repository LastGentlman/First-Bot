import easyocr
import re
from datetime import datetime
from supabase import create_client, Client

# Configuración de Supabase
SUPABASE_URL = "https://TU_URL.supabase.co"
SUPABASE_KEY = "TU_API_KEY"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

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
    reader = easyocr.Reader(["es"], gpu=False)
    result = reader.readtext(imagen, detail=0)
    
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

    datos = completar_comillas(datos)

    # Agregar columna de folio completo
    for d in datos:
        if prefijo and not d["folio"].startswith(prefijo):
            d["folio_completo"] = f"{d['id']}{prefijo}{d['folio']}"
        else:
            d["folio_completo"] = f"{d['id']}{d['folio']}"

    # Ordenar por hora
    datos_ordenados = sorted(datos, key=lambda x: datetime.strptime(x["hora"], "%H:%M"))

    # Insertar en Supabase
    for row in datos_ordenados:
        supabase.table("registros").insert({
            "id": row["id"],
            "folio": row["folio_completo"],
            "hora": row["hora"],
            "estado": row["estado"]
        }).execute()

    return datos_ordenados