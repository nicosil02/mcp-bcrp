"""Servidor MCP del BCRP. Empiecen aquí: buscar_series ya funciona; completen los TODO hasta que pasen las pruebas.

    uv run pytest                                           # el contrato: las 8 pruebas deben pasar
    uv run server.py                                        # el servidor, por stdio
    claude mcp add --scope project bcrp -- uv run server.py # conectarlo a Claude Code

Las cuentas las hace Python, no el modelo: así cada cifra sale de una llamada que se puede repetir.
"""
import re
import time
import unicodedata
from functools import cache
from pathlib import Path

import httpx
import pandas as pd
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

AQUI = Path(__file__).parent
CATALOGO = AQUI / "datos" / "catalogo_bcrp.csv"
URL_CATALOGO = "https://estadisticas.bcrp.gob.pe/estadisticas/series/metadata"
API = "https://estadisticas.bcrp.gob.pe/estadisticas/series/api"
SOLO_LECTURA = {"readOnlyHint": True, "openWorldHint": True}

mcp = FastMCP("bcrp", instructions=(
    "Series oficiales del Banco Central de Reserva del Perú (BCRP). Primero busca el código con buscar_series; "
    "luego usa resumen para variaciones, promedios y extremos, y serie para los datos. No calcules a mano: "
    "cita siempre el código de la serie y el periodo."))


def _sin_tildes(s):
    return unicodedata.normalize("NFD", str(s).lower()).encode("ascii", "ignore").decode()


@cache
def catalogo():
    """El catálogo oficial: 17 mil series en unos 7 MB. Se baja una vez y queda en datos/."""
    if not CATALOGO.exists():
        CATALOGO.parent.mkdir(exist_ok=True)
        r = httpx.get(URL_CATALOGO, timeout=120)
        r.raise_for_status()
        CATALOGO.write_text(r.content.decode("latin-1"), encoding="utf-8")
    df = pd.read_csv(CATALOGO, sep=";", dtype=str).fillna("")
    df = df.rename(columns={"Código de serie": "codigo", "Categoría de serie": "categoria", "Grupo de serie": "grupo",
                            "Nombre de serie": "nombre", "Frecuencia": "frecuencia",
                            "Fecha de actualización": "actualizada", "Fecha de inicio": "inicio"})
    df["texto"] = (df.categoria + " " + df.grupo + " " + df.nombre).map(_sin_tildes)
    return df


def _ficha(codigo):
    fila = catalogo().query("codigo == @codigo")
    if fila.empty:
        raise ToolError(f"No existe el código de serie {codigo!r}. Búsquenlo con buscar_series.")
    f = fila.iloc[0]
    return {"codigo": f.codigo, "nombre": f"{f.grupo} - {f.nombre}", "categoria": f.categoria,
            "frecuencia": f.frecuencia, "inicio": f.inicio, "actualizada": f.actualizada}


@mcp.tool(annotations=SOLO_LECTURA)
def buscar_series(texto: str, frecuencia: str | None = None, limite: int = 10) -> list[dict]:
    """Busca series del BCRP por palabras: todas deben aparecer en la categoría, el grupo o el nombre.

    Ejemplos: "ipc 12 meses", "tipo de cambio interbancario venta", "reservas internacionales netas",
    "tasa de referencia", "cobre precio". frecuencia: "Diaria", "Mensual", "Trimestral" o "Anual".
    Devuelve el código, el nombre completo, la frecuencia y desde cuándo hay datos. Si no hay resultados,
    prueben con menos palabras.
    """
    df = catalogo()
    m = pd.Series(True, index=df.index)
    for palabra in _sin_tildes(texto).split():
        m &= df.texto.str.contains(re.escape(palabra))
    if frecuencia:
        m &= df.frecuencia.str.lower() == frecuencia.lower()
    # primero las series vigentes y, entre ellas, las de nombre más corto (las más generales)
    sel = df[m]
    orden = sel.assign(vieja=sel.actualizada < "2025", largo=(sel.grupo + sel.nombre).str.len())
    return [_ficha(c) for c in orden.sort_values(["vieja", "largo"]).head(limite).codigo]


MESES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Set", "Oct", "Nov", "Dic"]
NUM_MES = {m: i + 1 for i, m in enumerate(MESES)} | {"Sep": 9}


def _fecha_api(fecha, frecuencia, fin=False):
    """Convierte "2025-01", "2025-01-15", "2025-T1" o "2025" al formato que pide la API según la frecuencia."""
    m = re.fullmatch(r"(\d{4})(?:-(T?)(\d{1,2}))?(?:-(\d{1,2}))?", fecha.strip())
    if not m:
        raise ToolError(f"Fecha {fecha!r} no reconocida. Usen 2025-01 (mes), 2025-01-15 (día), 2025-T1 o 2025.")
    anio, es_trim, mes, dia = int(m[1]), m[2], m[3], m[4]
    if frecuencia == "Anual":
        return str(anio)
    if frecuencia == "Trimestral":
        if mes is None:
            return f"{anio}-{4 if fin else 1}"
        return f"{anio}-{int(mes) if es_trim else (int(mes) - 1) // 3 + 1}"
    mes = int(mes) if mes else (12 if fin else 1)
    if frecuencia == "Diaria":
        if dia is None:   # un mes entero: del primer al último día
            dia = pd.Period(f"{anio}-{mes:02d}").days_in_month if fin else 1
        return f"{anio}-{mes:02d}-{int(dia):02d}"
    return f"{anio}-{mes}"


def _periodo(nombre):
    """"Ene.2025" -> "2025-01", "01.Oct.26" -> "2026-10-01", "T1.25" -> "2025-T1", "2025" -> "2025"."""
    partes = nombre.split(".")
    if len(partes) == 3:
        return f"20{partes[2]}-{NUM_MES[partes[1]]:02d}-{int(partes[0]):02d}"
    if len(partes) == 2 and partes[0].startswith("T"):
        return f"20{partes[1]}-{partes[0]}"
    if len(partes) == 2:
        return f"{partes[1]}-{NUM_MES[partes[0]]:02d}"
    return nombre


@cache
def _descargar(url):
    """Pide la URL a la API; si responde HTML (límite de pedidos), espera y reintenta. Guarda en caché."""
    for intento in range(5):
        try:
            r = httpx.get(url, timeout=60)
            return r.json()
        except (ValueError, httpx.HTTPError):
            time.sleep(2 + 3 * intento)
    raise ToolError(f"El BCRP no respondió con datos para {url}. Intenten de nuevo en unos segundos.")


@mcp.tool(annotations=SOLO_LECTURA)
def serie(codigo: str, desde: str, hasta: str) -> dict:
    """Datos de una serie del BCRP entre dos fechas, ambas incluidas, con la URL de la fuente.

    Fechas: "2025-01" (mensual), "2026-10-15" (diaria), "2025-T1" (trimestral) o "2025" (anual); si la serie es
    diaria y dan un mes, se toma el mes completo. Ejemplo: serie("PN01273PM", "2026-01", "2026-09").
    Busquen antes el código con buscar_series. Para variaciones, promedios o extremos usen resumen.
    """
    f = _ficha(codigo)
    url = f"{API}/{codigo}/json/{_fecha_api(desde, f['frecuencia'])}/{_fecha_api(hasta, f['frecuencia'], fin=True)}"
    datos = [{"periodo": _periodo(p["name"]), "valor": float(p["values"][0])}
             for p in _descargar(url).get("periods", []) if p["values"][0] not in ("n.d.", "")]
    if not datos:
        raise ToolError(f"La serie {codigo} no tiene datos entre {desde} y {hasta}. Revisen el inicio en su ficha.")
    return {**f, "datos": datos, "fuente": url}


@mcp.tool(annotations=SOLO_LECTURA)
def resumen(codigo: str, desde: str, hasta: str) -> dict:
    """Resumen de una serie del BCRP entre dos fechas: primer y último dato, cambio absoluto, variación %,
    promedio, máximo y mínimo (con su periodo). Úsenla en vez de calcular a mano.

    Mismas fechas que serie. Ejemplo: resumen("PN01273PM", "2025-12", "2026-09") para ver cómo fue la inflación.
    """
    s = serie(codigo, desde, hasta)
    df = pd.DataFrame(s["datos"])
    punto = lambda i: {"periodo": df.periodo[i], "valor": round(df.valor[i], 4)}
    primero, ultimo = df.valor.iloc[0], df.valor.iloc[-1]
    return {"codigo": codigo, "nombre": s["nombre"], "frecuencia": s["frecuencia"], "n": len(df),
            "primero": punto(0), "ultimo": punto(len(df) - 1), "cambio": round(ultimo - primero, 4),
            "variacion_pct": round((ultimo / primero - 1) * 100, 2) if primero else None,
            "promedio": round(df.valor.mean(), 4), "maximo": punto(df.valor.idxmax()),
            "minimo": punto(df.valor.idxmin()), "fuente": s["fuente"]}


INDICADORES_BM = {"crecimiento_pbi": "NY.GDP.MKTP.KD.ZG", "inflacion": "FP.CPI.TOTL.ZG",
                  "desempleo": "SL.UEM.TOTL.ZS", "pbi_per_capita": "NY.GDP.PCAP.KD",
                  "exportaciones_pct_pbi": "NE.EXP.GNFS.ZS", "deuda_publica_pct_pbi": "GC.DOD.TOTL.GD.ZS"}


@mcp.tool(annotations=SOLO_LECTURA)
def comparar_paises(indicador: str, paises: str = "PER;CHL;COL;MEX;BRA", desde: int = 2015,
                    hasta: int = 2025) -> dict:
    """Compara al Perú con otros países en un indicador anual del Banco Mundial (el BCRP solo tiene datos del Perú).

    Úsenla cuando pregunten cómo le va al Perú frente a la región o a otro país. indicador: "crecimiento_pbi" (%),
    "inflacion" (%), "desempleo" (% de la fuerza laboral), "pbi_per_capita" (US$ constantes de 2015),
    "exportaciones_pct_pbi" o "deuda_publica_pct_pbi". paises: códigos ISO3 separados por ";", por ejemplo "PER;CHL".
    Devuelve los valores por país y año, el promedio de cada país en el periodo y la URL de la fuente.
    """
    if indicador not in INDICADORES_BM:
        raise ToolError(f"Indicador {indicador!r} no disponible. Usen uno de: {', '.join(INDICADORES_BM)}.")
    url = (f"https://api.worldbank.org/v2/country/{paises}/indicator/{INDICADORES_BM[indicador]}"
           f"?format=json&date={desde}:{hasta}&per_page=1000")
    r = _descargar(url)
    if len(r) < 2 or not r[1]:
        raise ToolError(f"El Banco Mundial no devolvió datos para {paises!r}. Revisen los códigos ISO3.")
    df = pd.DataFrame([{"pais": x["country"]["value"], "iso3": x["countryiso3code"], "anio": int(x["date"]),
                        "valor": x["value"]} for x in r[1]]).dropna()
    return {"indicador": indicador, "codigo_bm": INDICADORES_BM[indicador],
            "datos": df.sort_values(["iso3", "anio"]).round(3).to_dict("records"),
            "promedio_por_pais": df.groupby("pais").valor.mean().round(3).sort_values(ascending=False).to_dict(),
            "fuente": url}


@mcp.resource("bcrp://serie/{codigo}")
def ficha(codigo: str) -> dict:
    """Ficha de una serie del BCRP: nombre, categoría, frecuencia, desde cuándo hay datos y última actualización."""
    return _ficha(codigo)


@mcp.prompt
def informe(tema: str) -> str:
    """Informe breve con cifras oficiales del BCRP, cada una con su serie y periodo."""
    return (f"Escribe un informe de 5 viñetas sobre «{tema}» en el Perú con datos del BCRP. Busca las series con "
            "buscar_series, usa resumen para variaciones y extremos y serie para los datos; no calcules a mano. "
            "En cada viñeta cita el código de la serie y el periodo, y al final lista las URL de las fuentes.")


if __name__ == "__main__":
    mcp.run()
