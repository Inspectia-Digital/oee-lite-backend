"""Fase AB (revisión completa de la cascada de umbrales/tolerancias a
pedido de Green Mills): ni Linea/Estacion (Create y Update) ni Tenant
(PATCH /mi-empresa/tenant) validaban que 'lento' fuera menor que
'alerta'. Si quedan invertidos, scans.py evalúa "delta_t > t_alerta"
ANTES que "delta_t > t_lento" -- con alerta más chico que lento, el
nivel LENTO queda matemáticamente inalcanzable, en silencio.

Fase AC (rediseño completo): Línea pasa a ser el ÚNICO nivel con un
perfil de tiempos propio (tiempo_ideal_seg/tiempo_lento_seg/
tiempo_alerta_seg, SIEMPRE los 3 juntos, con default 240/280/300).
Estación y Tenant/Empresa YA NO tienen ningún campo de umbral/tolerancia
-- ver test_fase_ac_perfil_tiempos.py para la cascada completa
SKU×Estación > SKU > Línea.
"""
from app.models.domain import Planta
from tests.conftest import autenticar_como


def _crear_planta(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta AB Umbrales")
    db.add(planta)
    db.commit()
    db.refresh(planta)
    return planta


def test_crear_linea_lento_mayor_que_alerta_devuelve_400(client, db, tenant_a, gerente_a):
    planta = _crear_planta(db, tenant_a)
    autenticar_como(gerente_a.id)
    r = client.post(
        "/config/lineas/",
        json={"nombre": "Línea Invertida", "tiempo_ideal_seg": 100, "tiempo_lento_seg": 300, "tiempo_alerta_seg": 200},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 400
    assert "lento" in r.json()["detail"].lower()


def test_crear_linea_lento_igual_a_alerta_devuelve_400(client, db, tenant_a, gerente_a):
    """Igual también es inválido -- '> t_alerta' y '> t_lento' con el
    mismo valor numérico deja a LENTO sin ningún delta_t que lo alcance."""
    planta = _crear_planta(db, tenant_a)
    autenticar_como(gerente_a.id)
    r = client.post(
        "/config/lineas/",
        json={"nombre": "Línea Igual", "tiempo_ideal_seg": 100, "tiempo_lento_seg": 250, "tiempo_alerta_seg": 250},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 400


def test_crear_linea_ideal_mayor_que_lento_devuelve_400(client, db, tenant_a, gerente_a):
    """Nuevo en Fase AC: ideal <= lento también se valida -- antes sólo se
    comparaba lento vs. alerta, ideal quedaba fuera de la relación."""
    planta = _crear_planta(db, tenant_a)
    autenticar_como(gerente_a.id)
    r = client.post(
        "/config/lineas/",
        json={"nombre": "Línea Ideal Roto", "tiempo_ideal_seg": 250, "tiempo_lento_seg": 200, "tiempo_alerta_seg": 300},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 400


def test_crear_linea_perfil_correcto_funciona(client, db, tenant_a, gerente_a):
    planta = _crear_planta(db, tenant_a)
    autenticar_como(gerente_a.id)
    r = client.post(
        "/config/lineas/",
        json={"nombre": "Línea OK", "tiempo_ideal_seg": 100, "tiempo_lento_seg": 200, "tiempo_alerta_seg": 300},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["tiempo_ideal_seg"] == 100
    assert body["tiempo_lento_seg"] == 200
    assert body["tiempo_alerta_seg"] == 300


def test_crear_linea_sin_especificar_nada_usa_default_240_280_300(client, db, tenant_a, gerente_a):
    """Fase AC: los 240/280/300 que antes eran una constante hardcodeada
    en clasificacion.py ahora son el default editable de Línea -- una
    línea nueva ya clasifica sin que nadie configure nada."""
    planta = _crear_planta(db, tenant_a)
    autenticar_como(gerente_a.id)
    r = client.post("/config/lineas/", json={"nombre": "Línea Default"}, headers={"X-Sub-Tenant-Id": str(planta.id)})
    assert r.status_code == 201
    body = r.json()
    assert body["tiempo_ideal_seg"] == 240.0
    assert body["tiempo_lento_seg"] == 280.0
    assert body["tiempo_alerta_seg"] == 300.0


def test_crear_linea_con_un_campo_combina_con_defaults_de_los_otros_dos(client, db, tenant_a, gerente_a):
    """Mandar sólo UN campo del perfil no bloquea -- los otros dos caen a
    su default (240/280), y se valida el TRIO resultante completo."""
    planta = _crear_planta(db, tenant_a)
    autenticar_como(gerente_a.id)
    r = client.post(
        "/config/lineas/",
        json={"nombre": "Línea Parcial OK", "tiempo_alerta_seg": 500},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["tiempo_ideal_seg"] == 240.0
    assert body["tiempo_lento_seg"] == 280.0
    assert body["tiempo_alerta_seg"] == 500


def test_crear_linea_con_un_campo_incoherente_con_los_defaults_devuelve_400(client, db, tenant_a, gerente_a):
    """Contraparte: si el único campo mandado rompe la relación con los
    defaults de los otros dos (240 <= lento), se rechaza igual -- se
    valida el trío completo, no sólo el campo que llegó en el payload."""
    planta = _crear_planta(db, tenant_a)
    autenticar_como(gerente_a.id)
    r = client.post(
        "/config/lineas/",
        json={"nombre": "Línea Parcial Rota", "tiempo_lento_seg": 200},  # 200 < default ideal (240)
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 400


def test_actualizar_linea_a_perfil_invertido_devuelve_400(client, db, tenant_a, gerente_a):
    planta = _crear_planta(db, tenant_a)
    autenticar_como(gerente_a.id)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    r = client.post(
        "/config/lineas/",
        json={"nombre": "Línea a Invertir", "tiempo_ideal_seg": 100, "tiempo_lento_seg": 200, "tiempo_alerta_seg": 300},
        headers=headers,
    )
    linea_id = r.json()["id"]

    r = client.patch(f"/config/lineas/{linea_id}", json={"tiempo_lento_seg": 350}, headers=headers)
    assert r.status_code == 400


def test_crear_estacion_ignora_campos_de_perfil_de_tiempos_legacy(client, db, tenant_a, gerente_a):
    """Fase AC: Estación ya no tiene NINGÚN campo de umbral/tolerancia --
    mandarlos igual (ej. un cliente API viejo) no debe romper nada, se
    ignoran como cualquier campo extra no reconocido por el schema."""
    planta = _crear_planta(db, tenant_a)
    autenticar_como(gerente_a.id)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    linea = client.post("/config/lineas/", json={"nombre": "Línea E"}, headers=headers).json()
    r = client.post(
        "/config/estaciones/",
        json={
            "nombre": "Estación Legacy", "tipo": "sensor", "linea_id": linea["id"],
            "umbral_lento": 300, "umbral_alerta": 200, "tolerancia_lento_pct": 1.3, "tolerancia_alerta_pct": 1.15,
        },
        headers=headers,
    )
    assert r.status_code == 201
    assert "umbral_lento" not in r.json()
    assert "tolerancia_lento_pct" not in r.json()


def test_actualizar_tenant_ignora_tolerancia_legacy_pero_sigue_actualizando_lo_demas(client, db, tenant_a, gerente_a):
    """Fase AC: Empresa/Tenant ya no es un nivel de la cascada -- ni
    siquiera existe el campo para validar 'invertido'. Un PATCH viejo que
    todavía mande tolerancia_lento/alerta_pct no debe romper, sólo ignora
    esos campos y aplica el resto normalmente."""
    autenticar_como(gerente_a.id)
    r = client.patch(
        "/accesos/mi-empresa/tenant",
        json={"tolerancia_lento_pct": 1.30, "tolerancia_alerta_pct": 1.15, "oee_objetivo_pct": 90.0},
    )
    assert r.status_code == 200
    assert r.json()["tenant"]["oee_objetivo_pct"] == 90.0


# ---------- Fase AS (auditoría QA, QA-14): valores positivos ----------

def test_crear_linea_ideal_negativo_devuelve_400(client, db, tenant_a, gerente_a):
    """QA-14: antes sólo se validaba el ORDEN relativo -- ideal=-10,
    lento=-5, alerta=0 cumplía ideal<=lento<alerta y pasaba sin
    problema, pero convertía todos los eventos en ALERTA."""
    planta = _crear_planta(db, tenant_a)
    autenticar_como(gerente_a.id)
    r = client.post(
        "/config/lineas/",
        json={"nombre": "Línea Negativa", "tiempo_ideal_seg": -10, "tiempo_lento_seg": -5, "tiempo_alerta_seg": 0},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 400
    assert "cero" in r.json()["detail"].lower()


def test_crear_linea_ideal_cero_devuelve_400(client, db, tenant_a, gerente_a):
    planta = _crear_planta(db, tenant_a)
    autenticar_como(gerente_a.id)
    r = client.post(
        "/config/lineas/",
        json={"nombre": "Línea Cero", "tiempo_ideal_seg": 0, "tiempo_lento_seg": 100, "tiempo_alerta_seg": 200},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 400


def test_crear_linea_lento_o_alerta_negativos_devuelve_400(client, db, tenant_a, gerente_a):
    planta = _crear_planta(db, tenant_a)
    autenticar_como(gerente_a.id)
    r = client.post(
        "/config/lineas/",
        json={"nombre": "Línea Lento Negativo", "tiempo_ideal_seg": 100, "tiempo_lento_seg": -50, "tiempo_alerta_seg": 300},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 400
