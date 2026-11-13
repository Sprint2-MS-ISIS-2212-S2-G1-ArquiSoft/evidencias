import argparse
import json
import os
from urllib.parse import parse_qs
 
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
 
FASES = ["normal", "falla", "recuperacion"]
COLOR_FASE = {"normal": "#e8f5e9", "falla": "#ffebee", "recuperacion": "#e3f2fd"}
 
 
def tags(cadena):
    if not isinstance(cadena, str):
        return {}
    return {k: v[0] for k, v in parse_qs(cadena).items()}
 
 
def cargar(ruta):
    df = pd.read_csv(ruta, low_memory=False)
    t = df["extra_tags"].apply(tags)
    df["fase"] = t.apply(lambda x: x.get("fase"))
    df["tipo"] = t.apply(lambda x: x.get("tipo"))
    df["origen"] = t.apply(lambda x: x.get("origen"))
    return df
 
 
def analizar(ruta, fase_s, salida):
    nombre = os.path.splitext(os.path.basename(ruta))[0]
    df = cargar(ruta)
    dur = df[(df.metric_name == "http_req_duration") & (df.tipo == "cotizacion")].copy()
    chk = df[df.metric_name == "checks"].copy()
    org = df[df.metric_name == "origen_score"].copy()
    t0 = dur.timestamp.min()
    for d in (dur, chk, org):
        d["seg"] = d.timestamp - t0
 
    filas = []
    for fase in FASES:
        c = chk[chk.fase == fase]
        d = dur[dur.fase == fase]
        o = org[org.fase == fase].groupby("origen").metric_value.sum().to_dict()
        total = len(c)
        exitosas = int(c.metric_value.sum())
        filas.append({
            "corrida": nombre,
            "fase": fase,
            "total": total,
            "exitosas": exitosas,
            "fallidas": total - exitosas,
            "disponibilidad_%": round(100 * exitosas / total, 3) if total else None,
            "p95_ms": round(d.metric_value.quantile(0.95), 1) if len(d) else None,
            "p99_ms": round(d.metric_value.quantile(0.99), 1) if len(d) else None,
            "max_ms": round(d.metric_value.max(), 1) if len(d) else None,
            "%_>250ms": round(100 * (d.metric_value > 250).mean(), 2) if len(d) else None,
            "OPEN_DATA": int(o.get("OPEN_DATA", 0)),
            "CACHE": int(o.get("CACHE", 0)),
            "DEFECTO": int(o.get("DEFECTO", 0)),
            "ERROR": int(o.get("ERROR", 0)),
        })
 
    # Tiempos clave de la linea de tiempo.
    falla = org[(org.seg >= fase_s) & (org.seg < 2 * fase_s)]
    deg = falla[falla.origen.isin(["CACHE", "DEFECTO"])]
    rec = org[(org.seg >= 2 * fase_s) & (org.origen == "OPEN_DATA")]
    lentas = dur[(dur.fase == "falla") & (dur.metric_value > 250)]
    tiempos = {
        "corrida": nombre,
        "primera_degradada_s_desde_falla": (int(deg.seg.min() - fase_s) if len(deg) else None),
        "ultima_lenta_>250ms_s_desde_falla": (int(lentas.seg.max() - fase_s) if len(lentas) else None),
        "vuelta_a_open_data_s_desde_restablecer": (int(rec.seg.min() - 2 * fase_s) if len(rec) else None),
    }
 
    # Graficas por ventanas de 10 s.
    dur["ventana"] = (dur.seg // 10) * 10
    chk["ventana"] = (chk.seg // 10) * 10
    org["ventana"] = (org.seg // 10) * 10
    disp = chk.groupby("ventana").metric_value.mean() * 100
    p95 = dur.groupby("ventana").metric_value.quantile(0.95)
    p99 = dur.groupby("ventana").metric_value.quantile(0.99)
    origen = org.pivot_table(index="ventana", columns="origen", values="metric_value", aggfunc="sum").fillna(0)
 
    fig, ejes = plt.subplots(3, 1, figsize=(11, 10), sharex=True)
    for eje in ejes:
        for i, fase in enumerate(FASES):
            eje.axvspan(i * fase_s, (i + 1) * fase_s, color=COLOR_FASE[fase], zorder=0)
    ejes[0].plot(disp.index, disp.values, marker=".", color="#2e7d32")
    ejes[0].axhline(99.9, ls="--", color="red", lw=1, label="Meta 99,9 %")
    ejes[0].set_ylabel("Cotizaciones exitosas (%)")
    ejes[0].set_ylim(min(-2, disp.min() - 2), 101)
    ejes[0].legend(loc="lower left")
    ejes[1].plot(p95.index, p95.values, marker=".", label="p95")
    ejes[1].plot(p99.index, p99.values, marker=".", label="p99", alpha=0.7)
    ejes[1].axhline(250, ls="--", color="orange", lw=1, label="Meta p95 250 ms")
    ejes[1].axhline(500, ls="--", color="red", lw=1, label="Meta p99 500 ms")
    ejes[1].set_ylabel("Latencia (ms)")
    ejes[1].legend(loc="upper left")
    colores = {"OPEN_DATA": "#1565c0", "CACHE": "#f9a825", "DEFECTO": "#8d6e63", "ERROR": "#c62828"}
    abajo = None
    for col in ["OPEN_DATA", "CACHE", "DEFECTO", "ERROR"]:
        if col in origen:
            ejes[2].bar(origen.index, origen[col], width=10, align="edge", bottom=abajo, label=col, color=colores[col])
            abajo = origen[col] if abajo is None else abajo + origen[col]
    ejes[2].set_ylabel("Cotizaciones por ventana de 10 s")
    ejes[2].set_xlabel("Segundos desde el inicio de la prueba")
    ejes[2].legend(loc="upper left", ncol=4)
    fig.suptitle(f"EXP-006 · {nombre}  (verde: normal · rojo: falla · azul: recuperación)")
    fig.tight_layout()
    fig.savefig(os.path.join(salida, f"{nombre}.png"), dpi=130)
    plt.close(fig)
    return filas, tiempos
 
 
def main():
    p = argparse.ArgumentParser()
    p.add_argument("csv", nargs="+")
    p.add_argument("--fase-s", type=int, default=300)
    p.add_argument("--salida", default="analisis")
    a = p.parse_args()
    os.makedirs(a.salida, exist_ok=True)
    todas, tiempos = [], []
    for ruta in a.csv:
        f, t = analizar(ruta, a.fase_s, a.salida)
        todas += f
        tiempos.append(t)
    tabla = pd.DataFrame(todas)
    tabla.to_csv(os.path.join(a.salida, "tabla-por-fase.csv"), index=False)
    with open(os.path.join(a.salida, "tiempos.json"), "w") as fh:
        json.dump(tiempos, fh, indent=2)
    pd.set_option("display.width", 250)
    print(tabla.to_string(index=False))
    print(json.dumps(tiempos, indent=2))
 
 
if __name__ == "__main__":
    main()