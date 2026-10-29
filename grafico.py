"""Figura del informe: cómo respondió Claude (Haiku) las 10 preguntas en cada modo.

    uv run grafico.py        # lee resultados*.csv y escribe grafico.png
"""
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

AQUI = Path(__file__).parent
CORRIDAS = [   # (archivo, modo, etiqueta)
    ("resultados.csv", "memoria", "Solo memoria"),
    ("resultados_sin_salida.csv", "memoria", "Solo memoria,\nsin permiso de decir «no sé»"),
    ("resultados_web.csv", "web", "Búsqueda web"),
    ("resultados.csv", "mcp", "MCP del BCRP"),
]
RESULTADOS = [("acierta", "Acierta", "#0ca30c"), ("se equivoca", "Se equivoca", "#d03b3b"),
              ("no sabe", "No sabe", "#c3c2b7")]

filas = []
for archivo, modo, etiqueta in CORRIDAS:
    r = pd.read_csv(AQUI / archivo).query("modo == @modo")
    filas.append({"etiqueta": etiqueta, "segundos": r.segundos.mean(), **r.resultado.value_counts().to_dict()})
df = pd.DataFrame(filas).fillna(0).iloc[::-1].reset_index(drop=True)

plt.rcParams.update({"font.family": "sans-serif", "font.size": 10, "axes.edgecolor": "#898781"})
fig, ax = plt.subplots(figsize=(9, 4.2), facecolor="#fcfcfb")
ax.set_facecolor("#fcfcfb")
izq = pd.Series(0.0, index=df.index)
for clave, nombre, color in RESULTADOS:
    n = df.get(clave, pd.Series(0, index=df.index))
    ax.barh(df.index, n, left=izq, height=0.55, color=color, edgecolor="#fcfcfb", linewidth=2, label=nombre)
    for i, v in n.items():
        if v:
            ax.text(izq[i] + v / 2, i, f"{int(v)}", ha="center", va="center", fontsize=10, fontweight="bold",
                    color="#0b0b0b" if clave == "no sabe" else "#ffffff")
    izq += n
for i, s in df.segundos.items():
    ax.text(10.15, i, f"{s:.1f} s por pregunta", va="center", fontsize=9, color="#52514e")

ax.set_yticks(df.index, df.etiqueta, color="#0b0b0b")
ax.set_xlim(0, 12.2)
ax.set_xticks(range(0, 11, 2))
ax.tick_params(colors="#898781", length=0)
ax.tick_params(axis="y", labelcolor="#0b0b0b")
ax.set_xlabel("Preguntas (de 10)", color="#52514e")
for lado in ("top", "right", "left"):
    ax.spines[lado].set_visible(False)
ax.legend(ncol=3, loc="upper left", bbox_to_anchor=(0, 1.13), frameon=False, fontsize=9)
fig.suptitle("Con el MCP, Haiku acierta 10 de 10 cifras del BCRP; sin herramientas, ninguna",
             x=0.01, ha="left", fontsize=11.5, fontweight="bold", color="#0b0b0b")
fig.text(0.01, 0.01, "Claude Haiku vía claude -p, 10 preguntas con la cifra oficial del BCRP, 28 de octubre de 2026.",
         fontsize=8, color="#898781")
fig.tight_layout()
fig.savefig(AQUI / "grafico.png", dpi=160, facecolor=fig.get_facecolor())
print("grafico.png")
