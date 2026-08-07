#!/usr/bin/env python3
"""
Fase P — transformador PLC → eventos, Green Mills (Armadora de Pan, PLC
Siemens LOGO! con gateway Node-RED).

Cada cliente con ingesta por PLC va a necesitar su propio transformador:
el paradigma de señales (pulso vs. basculación, qué variable es ruido,
qué canal corresponde a qué formato) depende del modelo/programación
del PLC, no hay una lógica universal. Este módulo es el de Green Mills
específicamente -- documenta, en código, la ingeniería inversa que hizo
el cliente sobre su propio PLC:

  Paradigma: BASCULACIÓN (flip-flop), no pulso. El evento de producción
  es el CAMBIO de estado de la variable, no un `true` momentáneo -- una
  vez que pasa una bandeja, la variable invierte su valor y se queda
  ahí hasta la próxima. Esto hace al polling robusto ante micro-cortes:
  nunca se “pierde” un pulso porque no hay pulso, hay un estado que se
  puede releer.

  Canal A (selector físico en 5 moldes): `Entrada 1` bascula por cada
  bandeja de 5 panes. `Entrada 2` es el espejo exacto -- se ignora.
  Canal B (selector en 4 moldes): el PLC "congela" el Canal A y enruta
  el conteo por `Salida 1`, que bascula por cada bandeja de 4 panes.
  Ruido, se descarta siempre: `Salida 2`, `Marca 1`, `Palabra 0/2/4`.

  Regla exclusiva (si/sino-si/sino, NO dos ifs independientes -- ver
  nota en derivar_eventos): el cambio de Entrada 1 tiene prioridad. Esto
  importa para logs donde Salida 1 basculó en espejo de Entrada 2 en
  cada lectura (visto en log-3 de la prueba): sin la prioridad, cada
  lectura contaría DOBLE (una bandeja de 5 Y una de 4 a la vez).

Uso como librería (parsing/derivación, sin red -- lo que usa el test de
integración en tests/test_plc_green_mills_logs.py):

    from scripts.plc_green_mills_transform import parse_nodered_log, derivar_eventos
    lecturas = parse_nodered_log(texto_del_log)
    eventos = derivar_eventos(lecturas)

Uso como CLI (reproduce un log real contra un backend real -- dev, o
local con --api-url http://localhost:8000):

    python scripts/plc_green_mills_transform.py \\
        --log-file tests/fixtures/plc_green_mills/log-3.txt \\
        --api-url https://api-dev.tymeo.inspectia.ai \\
        --device-key "<key_id>.<secret>" \\
        --estacion-id <uuid-de-la-estacion-en-dev>

    Agregar --dry-run para ver los payloads sin mandarlos.
"""
from __future__ import annotations

import argparse
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

_TS_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4}),\s*(\d{1,2}):(\d{2}):(\d{2})nodo:")
_FIELD_RE = re.compile(
    r"^(Entrada 1|Entrada 2|Salida 1|Salida 2|Marca 1|Palabra 0|Palabra 2|Palabra 4):\s*(true|false|-?\d+)\s*$"
)
_BOOL_FIELDS = {"Entrada 1", "Entrada 2", "Salida 1", "Salida 2", "Marca 1"}
_LINEAS_IGNORADAS = {"msg.payload : Object", "object"}


def parse_nodered_log(texto: str) -> list[dict]:
    """Parsea un volcado del panel debug de Node-RED a una lista de
    lecturas ordenadas cronológicamente (una por bloque timestamp).
    Tolerante a variaciones de formato entre capturas -- líneas en
    blanco, subconjunto de "Palabra N" presentes, anotaciones manuales
    "==== ... ====" -- todo eso se ignora en vez de romper el parseo.
    """
    lecturas: list[dict] = []
    actual: dict | None = None
    for linea in texto.splitlines():
        linea = linea.strip()
        if not linea or linea in _LINEAS_IGNORADAS or linea.startswith("===="):
            continue
        m_ts = _TS_RE.match(linea)
        if m_ts:
            if actual is not None:
                lecturas.append(actual)
            dia, mes, anio, hh, mm, ss = (int(g) for g in m_ts.groups())
            actual = {"timestamp": datetime(anio, mes, dia, hh, mm, ss)}
            continue
        m_f = _FIELD_RE.match(linea)
        if m_f and actual is not None:
            nombre, valor = m_f.groups()
            actual[nombre] = (valor == "true") if nombre in _BOOL_FIELDS else int(valor)
    if actual is not None:
        lecturas.append(actual)
    return lecturas


def derivar_eventos(lecturas: list[dict]) -> list[dict]:
    """Regla de negocio confirmada por Green Mills, en tres pasos
    EXCLUYENTES (si / sino-si / sino -- no dos condicionales
    independientes):

      1. Si `Entrada 1` cambió respecto de la lectura anterior -> 1
         bandeja de 5 panes (Canal A).
      2. Si no, y `Salida 1` cambió -> 1 bandeja de 4 panes (Canal B).
      3. Si ninguna cambió -> se ignora la lectura (ciclo en curso o
         parada real).

    El orden si/sino-si importa: en algún log de prueba, Salida 1
    basculaba en espejo de Entrada 2 en CADA lectura, incluso durante
    producción normal en Canal A. Con dos `if` independientes eso
    contaría una bandeja de 5 Y una de 4 en la misma lectura -- con
    prioridad a Entrada 1, sólo cuenta la de 5, que es la real.

    La primera lectura del archivo nunca genera evento: no hay lectura
    anterior contra la cual detectar un cambio.
    """
    eventos: list[dict] = []
    prev_e1 = prev_s1 = None
    for lec in lecturas:
        e1, s1 = lec.get("Entrada 1"), lec.get("Salida 1")
        if prev_e1 is not None:  # hay lectura anterior con la cual comparar
            if e1 != prev_e1:
                eventos.append({"timestamp": lec["timestamp"], "canal": "A", "unidades": 5})
            elif s1 != prev_s1:
                eventos.append({"timestamp": lec["timestamp"], "canal": "B", "unidades": 4})
        prev_e1, prev_s1 = e1, s1
    return eventos


def desplazar_a_reciente(eventos: list[dict], ahora: datetime | None = None) -> list[dict]:
    """Corre todos los timestamps para que el último evento caiga justo
    antes de `ahora`, preservando exactos los deltas relativos entre
    eventos (los huecos de parada quedan intactos). Necesario porque
    /api/lite/scans rechaza timestamps de más de 7 días de antigüedad
    -- estos logs de prueba son de julio."""
    if not eventos:
        return eventos
    ahora = ahora or datetime.utcnow()
    offset = ahora - eventos[-1]["timestamp"] - timedelta(seconds=2)
    return [{**e, "timestamp": e["timestamp"] + offset} for e in eventos]


def construir_payload(evento: dict, id_estacion: str, fuente: str) -> dict:
    """event_id determinístico (uuid5) sobre fuente+timestamp original
    (pre-desplazamiento no importa, alcanza con que sea estable): correr
    el script dos veces con el mismo log no duplica eventos, cae en el
    camino idempotente de /api/lite/scans.

    El timestamp SIEMPRE va con offset UTC explícito (+00:00): los
    timestamps que produce este módulo ya están en UTC (parten de
    `datetime.utcnow()` en desplazar_a_reciente). Si se manda naive
    (sin tzinfo), /api/lite/scans lo interpreta como hora LOCAL de la
    planta (America/Buenos_Aires por defecto, Fase E1) y le suma el
    offset de nuevo al convertir a UTC -- corriendo el evento 3 horas
    para adelante, lo suficiente para chocar contra el rechazo de
    "timestamp más de 5 minutos en el futuro" si el evento ya estaba
    cerca de "ahora"."""
    seed = f"plc-green-mills:{fuente}:{evento['timestamp'].isoformat()}:{evento['canal']}"
    return {
        "event_id": str(uuid.uuid5(uuid.NAMESPACE_URL, seed)),
        "id_estacion": id_estacion,
        "timestamp": evento["timestamp"].replace(tzinfo=timezone.utc).isoformat(),
        "unidades_procesadas": evento["unidades"],
    }


def _main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log-file", required=True, type=Path, help="Ruta al .txt exportado del debug de Node-RED")
    ap.add_argument("--api-url", required=True, help="Base URL del backend (ej. https://api-dev.tymeo.inspectia.ai)")
    ap.add_argument("--device-key", help="Credencial M2M (X-Device-Key). Requerida salvo --dry-run")
    ap.add_argument("--estacion-id", required=True, help="UUID de la estación destino en ese backend")
    ap.add_argument("--dry-run", action="store_true", help="Sólo muestra los payloads, no manda nada")
    args = ap.parse_args()

    texto = args.log_file.read_text(encoding="utf-8")
    lecturas = parse_nodered_log(texto)
    eventos = derivar_eventos(lecturas)
    eventos = desplazar_a_reciente(eventos)

    print(f"{args.log_file.name}: {len(lecturas)} lecturas -> {len(eventos)} eventos "
          f"({sum(1 for e in eventos if e['canal']=='A')} canal A / "
          f"{sum(1 for e in eventos if e['canal']=='B')} canal B)")

    if not eventos:
        print("Nada para mandar.")
        return 0

    payloads = [construir_payload(e, args.estacion_id, args.log_file.name) for e in eventos]

    if args.dry_run:
        for p in payloads:
            print(p)
        return 0

    if not args.device_key:
        print("ERROR: falta --device-key (o usá --dry-run).", file=sys.stderr)
        return 1

    import requests  # import diferido: no hace falta para --dry-run

    ok = fallidos = 0
    for p in payloads:
        r = requests.post(
            f"{args.api_url.rstrip('/')}/api/lite/scans",
            json=p,
            headers={"X-Device-Key": args.device_key},
            timeout=10,
        )
        if r.status_code in (200, 201):
            ok += 1
        else:
            fallidos += 1
            print(f"  [{r.status_code}] {p['timestamp']} canal={'A' if p['unidades_procesadas']==5 else 'B'}: {r.text}")
    print(f"Listo: {ok} ok, {fallidos} fallidos.")
    return 1 if fallidos else 0


if __name__ == "__main__":
    raise SystemExit(_main())
