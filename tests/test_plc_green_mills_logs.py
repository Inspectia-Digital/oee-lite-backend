"""Fase P: reproduce los 3 logs reales de la prueba de Node-RED de Green
Mills (Armadora de Pan, PLC Siemens LOGO!) contra el backend real -- vía
el mismo TestClient/Postgres que usa el resto de la suite, no un mock.

Esto es la validación "subir a dev antes de tocar la planta" que pidió
el cliente, hecha reproducible y automática: si algún día cambia la
lógica de OEE/paradas y deja de leer estos logs de la misma forma, esta
suite se rompe y avisa -- en vez de que alguien tenga que volver a mirar
un dashboard a mano.

Los .txt están en tests/fixtures/plc_green_mills/ (copia exacta de los
logs de prueba, sin editar)."""
from pathlib import Path

from sqlmodel import select

from app.models.domain import (
    Estacion, EstadoParada, Linea, LiteEventoProduccion,
    ParadaDetectada, Planta, TipoProduccion,
)
from scripts.plc_green_mills_transform import (
    construir_payload, derivar_eventos, desplazar_a_reciente, parse_nodered_log,
)
from tests.conftest import autenticar_como

FIXTURES = Path(__file__).parent / "fixtures" / "plc_green_mills"


def _preparar_armadora_de_pan(db, tenant_id):
    """Umbrales calcados de las anotaciones del propio log-3
    (>15s = LENTO, >60s = ALERTA/parada) -- así el test valida
    exactamente el escenario que Green Mills diseñó."""
    planta = Planta(tenant_id=tenant_id, nombre="Planta Armadora de Pan")
    db.add(planta)
    db.commit()
    db.refresh(planta)

    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Armadora", tipo_produccion=TipoProduccion.POR_LOTES)
    db.add(linea)
    db.commit()
    db.refresh(linea)

    estacion = Estacion(
        tenant_id=tenant_id, nombre="PLC Siemens LOGO!", tipo="sensor", linea_id=linea.id,
        umbral_optimo=10, umbral_lento=15, umbral_alerta=60, activa=True,
    )
    db.add(estacion)
    db.commit()
    db.refresh(estacion)

    return planta, linea, estacion


def _emitir_credencial(client, gerente, estacion_id):
    autenticar_como(gerente.id)
    r = client.post("/config/api-keys/", json={"estacion_id": str(estacion_id)})
    assert r.status_code == 201
    return r.json()["credencial_completa"]


def _reproducir_log(client, credencial, estacion_id, nombre_archivo):
    texto = (FIXTURES / nombre_archivo).read_text(encoding="utf-8")
    lecturas = parse_nodered_log(texto)
    eventos = derivar_eventos(lecturas)
    eventos_desplazados = desplazar_a_reciente(eventos)

    for original, desplazado in zip(eventos, eventos_desplazados):
        payload = construir_payload(desplazado, str(estacion_id), nombre_archivo)
        r = client.post("/api/lite/scans", json=payload, headers={"X-Device-Key": credencial})
        assert r.status_code in (200, 201), f"{nombre_archivo} @ {original['timestamp']}: {r.status_code} {r.text}"

    return lecturas, eventos


def test_parser_reconoce_todas_las_lecturas_de_los_tres_logs():
    """Guardrail del parser en sí, sin red: si el formato de captura de
    Node-RED cambia (otro cliente, otra versión), esto avisa acá en vez
    de fallar en silencio con 0 eventos derivados. Conteo real medido de
    los 3 .txt de fixtures -- si cambia, o se tocaron los fixtures o el
    parser dejó de reconocer algo."""
    conteos = {}
    for nombre in ("log-1.txt", "log-2.txt", "log-3.txt"):
        texto = (FIXTURES / nombre).read_text(encoding="utf-8")
        lecturas = parse_nodered_log(texto)
        assert len(lecturas) > 0, f"{nombre}: el parser no reconoció ninguna lectura"
        conteos[nombre] = len(lecturas)
    assert conteos == {"log-1.txt": 70, "log-2.txt": 70, "log-3.txt": 14}


def test_log3_clasifica_micro_retraso_como_lento_sin_generar_parada(client, db, tenant_a, gerente_a):
    """El hueco de 30s ("MICRO-RETRASO (CICLO LENTO > 15s)" en el propio
    log) supera umbral_lento(15) pero no umbral_alerta(60): debe
    clasificar LENTO y NO debe abrir ninguna ParadaDetectada."""
    planta, linea, estacion = _preparar_armadora_de_pan(db, tenant_a)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)

    lecturas, eventos = _reproducir_log(client, credencial, estacion.id, "log-3.txt")

    eventos_db = db.exec(
        select(LiteEventoProduccion).where(LiteEventoProduccion.id_estacion == str(estacion.id))
        .order_by(LiteEventoProduccion.timestamp)
    ).all()
    assert len(eventos_db) == len(eventos)  # el parser+derivador es la fuente de verdad de "cuántos eventos hay"

    lentos = [e for e in eventos_db if e.estado == "LENTO"]
    assert len(lentos) == 1, f"esperaba exactamente 1 evento LENTO (el de después del hueco de 30s), hubo {len(lentos)}"


def test_log3_clasifica_parada_mayor_como_alerta_y_abre_una_parada_detectada(client, db, tenant_a, gerente_a):
    """El hueco de 240s ("PARADA MAYOR (HUECO > 60s)") supera
    umbral_alerta(60): debe abrir exactamente una ParadaDetectada, con
    duracion_segundos = delta - umbral_alerta = 240 - 60 = 180 (sólo el
    EXCEDENTE sobre la tolerancia cuenta como tiempo perdido, regla de
    Fase E2 -- nunca el hueco completo)."""
    planta, linea, estacion = _preparar_armadora_de_pan(db, tenant_a)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)

    _reproducir_log(client, credencial, estacion.id, "log-3.txt")

    paradas = db.exec(
        select(ParadaDetectada).where(ParadaDetectada.estacion_fk == estacion.id)
    ).all()
    assert len(paradas) == 1, f"esperaba exactamente 1 ParadaDetectada (el hueco de 240s), hubo {len(paradas)}"
    assert paradas[0].estado == EstadoParada.PENDIENTE
    assert paradas[0].duracion_segundos == 180.0


def test_log3_unidades_edge_autoritativas_5_por_bandeja_canal_a(client, db, tenant_a, gerente_a):
    """log-3 nunca cambia de canal (Entrada 1 basculó en cada lectura,
    incluso cuando Salida 1 también cambiaba en espejo) -- todos los
    eventos derivados son Canal A, 5 unidades cada uno."""
    planta, linea, estacion = _preparar_armadora_de_pan(db, tenant_a)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)

    lecturas, eventos = _reproducir_log(client, credencial, estacion.id, "log-3.txt")
    assert all(e["canal"] == "A" for e in eventos)

    total_esperado = 5 * len(eventos)
    total_real = sum(
        e.unidades_procesadas for e in db.exec(
            select(LiteEventoProduccion).where(LiteEventoProduccion.id_estacion == str(estacion.id))
        ).all()
    )
    assert total_real == total_esperado


def test_log1_y_log2_cambian_de_canal_5_a_4_moldes_sin_romper(client, db, tenant_a, gerente_a):
    """log-1 y log-2 simulan el cambio físico de formato (el operario
    gira la perilla de 5 a 4 moldes a mitad de turno): Canal A se
    congela, Canal B despierta. No hay una aserción de negocio fuerte
    acá todavía (¿un cambio de formato debería contar como parada de
    OEE o es un evento de setup aparte? -- pregunta abierta, no resuelta
    en este test) -- lo que se valida es que el pipeline entero (parser
    -> derivador -> /api/lite/scans real) procesa el escenario de punta
    a punta sin excepciones y con conteos consistentes con lo que el
    propio derivador predijo."""
    # Estación nueva por archivo: son dos capturas independientes, no un
    # stream continuo -- reproducirlas sobre la misma estación mezclaría
    # sus desplazamientos temporales (cada uno se calcula contra "ahora"
    # en el momento en que corre ese archivo) y ensuciaría el delta_t del
    # primer evento del segundo archivo contra el último del primero.
    for nombre in ("log-1.txt", "log-2.txt"):
        planta, linea, estacion = _preparar_armadora_de_pan(db, tenant_a)
        credencial = _emitir_credencial(client, gerente_a, estacion.id)
        lecturas, eventos = _reproducir_log(client, credencial, estacion.id, nombre)
        assert any(e["canal"] == "A" for e in eventos), f"{nombre}: se esperaba producción en canal A (5 moldes)"
        assert any(e["canal"] == "B" for e in eventos), f"{nombre}: se esperaba el cambio a canal B (4 moldes)"
