"""Fase FA (PRD Demo/Partners/Marketplace/Soporte/Planes): Ambiente Demo.

Decisión de diseño central del PRD, preservada acá: el simulador NO es
un sistema aparte con su propia lógica de "datos falsos" -- llama al
MISMO endpoint POST /api/lite/scans que usa cualquier PLC/ESP32 real,
con una credencial M2M propia de cada estación demo (ver
DemoCredencialSimulador, domain.py). Así lo que se ve en una demo es
exactamente el comportamiento real del producto (OEE, paradas,
dashboard) -- nunca se toca la lógica de negocio de scans.py, que es
además la más crítica y testeada de todo este backend.

Contrapartida de esa decisión: el simulador NO fuerza directamente
"este evento es LENTO" -- eso lo deduce scans.py solo, a partir del
timing real entre eventos consecutivos (igual que con un sensor real).
El simulador sólo decide CUÁNDO disparar el próximo evento; la
variación realista sale de espaciar esos disparos de forma no uniforme
(a veces al ritmo ideal, a veces más lento, a veces con un hueco que
scans.py va a detectar como parada -- exactamente como pasaría con
producción real).

Dos jobs en un único BackgroundScheduler (arrancado desde main.py):
  - _tick_simulacion: cada TICK_SEGUNDOS, dispara eventos para
    estaciones de tenants con demo_simulando_desde != NULL. También
    aplica el autofrenado (MAX_HORAS_SIMULACION).
  - _limpiar_demos_expiradas: una vez por hora, borra tenants demo con
    demo_expira_at vencido -- performance/costo de infra (pedido
    explícito del usuario: nada de tenants fantasma acumulándose).

Caveat documentado (no resuelto acá, no bloqueante para el MVP): sin
lock distribuido -- si Cloud Run corre más de 1 instancia (hoy
--min-instances 1 en dev y prod, ver .github/workflows/deploy-*.yml)
cada instancia tendría su propio scheduler, duplicando eventos de
demo. Datos 100% descartables, no productivos -- aceptable para este
MVP, pero hay que tenerlo presente si algún día min-instances cambia.
"""
import logging
import os
import random
import secrets
import uuid
from datetime import datetime, time, timedelta
from typing import Optional

import bcrypt
import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from sqlmodel import Session, delete, select

from app.core.database import engine
from app.core.demo_industrias import INDUSTRIAS_DEMO
from app.models.domain import (
    ApiKeyDispositivo, AsignacionTurno, DemoCredencialSimulador, Estacion,
    LiteEventoProduccion, Linea, MaestroSKU, ParadaDetectada, Planta,
    SesionOperario, Tenant, TipoProduccion, Turno,
)

logger = logging.getLogger(__name__)

TICK_SEGUNDOS = 10
MAX_HORAS_SIMULACION = 4
DIAS_RETENCION_DEMO = 7
VELOCIDAD_MULTIPLICADOR = {"lenta": 0.5, "normal": 1.0, "rapida": 2.0}

_scheduler: Optional[BackgroundScheduler] = None


def _base_url_interna() -> str:
    # Self-loopback dentro del mismo contenedor de Cloud Run -- ver
    # Dockerfile (uvicorn siempre escucha en 0.0.0.0:8080 acá).
    return f"http://127.0.0.1:{os.environ.get('PORT', '8080')}"


# ==========================================
# CREACIÓN DE LA ESTRUCTURA DEMO
# ==========================================
def crear_estructura_demo(db: Session, nombre: str, industria: str, tamano: str = "chica") -> Tenant:
    """Crea un Tenant es_demo=true completo (planta/línea/turno/SKUs/
    estaciones) según el template de la industria elegida (ver
    demo_industrias.py) + una credencial M2M interna por estación para
    que el simulador pueda operarla. `tamano="mediana"` duplica
    líneas/estaciones -- sigue siendo la MISMA industria/SKUs, sólo
    más escala."""
    if industria not in INDUSTRIAS_DEMO:
        raise ValueError(f"Industria desconocida: {industria}. Válidas: {list(INDUSTRIAS_DEMO.keys())}")
    template = INDUSTRIAS_DEMO[industria]

    tenant = Tenant(
        id=f"demo-{uuid.uuid4().hex[:10]}",
        nombre=nombre,
        es_demo=True,
        industria_demo=industria,
        demo_expira_at=datetime.utcnow() + timedelta(days=DIAS_RETENCION_DEMO),
        modulos_contratados="tymeo",
        origen_maestros="MANUAL",
        origen_maestros_planes="MANUAL",
    )
    db.add(tenant)
    db.flush()

    planta = Planta(tenant_id=tenant.id, nombre=f"Planta {template.label}")
    db.add(planta)
    db.flush()

    sku_base = template.skus[0]
    n_lineas = 2 if tamano == "mediana" else 1
    n_estaciones_por_linea = 2 if tamano == "mediana" else 1

    for i in range(n_lineas):
        sufijo_linea = f" {i + 1}" if n_lineas > 1 else ""
        linea = Linea(
            tenant_id=tenant.id, planta_id=planta.id,
            nombre=f"{template.nombre_linea}{sufijo_linea}",
            tipo_produccion=TipoProduccion(template.tipo_produccion),
            # Piso de línea = cadencia real del SKU principal del rubro --
            # simplificación deliberada del MVP: los eventos generados no
            # se atan a una Orden/Plan real (no hace falta para que la
            # demo "cobre vida"), así que clasifican contra este piso,
            # no contra MaestroSKU. Los SKUs igual se cargan (ver abajo)
            # para que el catálogo de Configuración se vea poblado.
            tiempo_ideal_seg=sku_base.tiempo_ideal_seg,
            tiempo_lento_seg=sku_base.tiempo_ideal_seg * 1.3,
            tiempo_alerta_seg=sku_base.tiempo_ideal_seg * 1.6,
        )
        db.add(linea)
        db.flush()

        db.add(Turno(
            tenant_id=tenant.id, linea_id=linea.id, nombre="Turno Único",
            hora_inicio=time(0, 0), hora_fin=time(23, 59),
        ))

        # El catálogo de SKUs es del TENANT, no se duplica por línea --
        # con tamano="mediana" (2+ líneas) generaría codigo_sku
        # repetido (PK) si se recreara en cada vuelta del loop. Se
        # cargan una sola vez, contra la primera línea creada (sólo
        # para completar linea_id, no se usan para resolver SKU activo
        # en este MVP -- ver comentario del piso de línea arriba).
        if i == 0:
            for sku in template.skus:
                codigo_unico = f"{sku.codigo}-{tenant.id[-6:]}"
                db.add(MaestroSKU(
                    tenant_id=tenant.id, codigo_sku=codigo_unico, descripcion=sku.descripcion,
                    linea_id=linea.id, tiempo_ideal_seg=sku.tiempo_ideal_seg,
                    tiempo_lento_seg=sku.tiempo_ideal_seg * 1.3, tiempo_alerta_seg=sku.tiempo_ideal_seg * 1.6,
                    unidades_por_ciclo=sku.unidades_por_ciclo,
                ))

        for k in range(n_estaciones_por_linea):
            sufijo_est = f" {k + 1}" if n_estaciones_por_linea > 1 else ""
            estacion = Estacion(
                tenant_id=tenant.id, linea_id=linea.id,
                nombre=f"{template.nombre_estacion}{sufijo_est}", tipo="sensor",
                posicion_linea=k + 1,
            )
            db.add(estacion)
            db.flush()
            _emitir_credencial_interna(db, tenant.id, estacion.id)

    db.commit()
    db.refresh(tenant)
    logger.info(f"[demo] Tenant demo creado: id={tenant.id} industria={industria} tamano={tamano}")
    return tenant


def _emitir_credencial_interna(db: Session, tenant_id: str, estacion_id: uuid.UUID) -> None:
    key_id = secrets.token_hex(8)
    secret = secrets.token_urlsafe(32)
    db.add(ApiKeyDispositivo(
        tenant_id=tenant_id, key_id=key_id,
        secret_hash=bcrypt.hashpw(secret.encode("utf-8"), bcrypt.gensalt()).decode("utf-8"),
        estacion_id=estacion_id, activo=True,
        expires_at=datetime.utcnow() + timedelta(days=3650),
    ))
    db.add(DemoCredencialSimulador(
        tenant_id=tenant_id, estacion_id=estacion_id,
        credencial_completa=f"{key_id}.{secret}",
    ))


# ==========================================
# LIMPIEZA DE EVENTOS/PARADAS GENERADOS (reiniciar demo)
# ==========================================
def reiniciar_datos_generados(db: Session, tenant_id: str) -> None:
    """Borra eventos/paradas/sesiones generados por el simulador (o
    cargados a mano durante la demo) -- vuelve a la config seedeada
    (plantas/líneas/estaciones/SKUs quedan intactos, sólo se limpia lo
    transaccional). Usado por POST /admin/demo/{id}/reiniciar."""
    estaciones_ids = [
        row for row in db.exec(select(Estacion.id).where(Estacion.tenant_id == tenant_id)).all()
    ]
    estaciones_ids_str = [str(e) for e in estaciones_ids]
    if estaciones_ids_str:
        db.exec(delete(LiteEventoProduccion).where(LiteEventoProduccion.id_estacion.in_(estaciones_ids_str)))
    db.exec(delete(ParadaDetectada).where(ParadaDetectada.tenant_id == tenant_id))
    db.exec(delete(SesionOperario).where(SesionOperario.tenant_id == tenant_id))
    db.exec(delete(AsignacionTurno).where(AsignacionTurno.tenant_id == tenant_id))
    db.commit()
    logger.info(f"[demo] Datos generados reiniciados para tenant={tenant_id}")


# ==========================================
# SCHEDULER
# ==========================================
def _enviar_scan(credencial: str, id_estacion: uuid.UUID) -> None:
    try:
        httpx.post(
            f"{_base_url_interna()}/api/lite/scans",
            json={
                "event_id": str(uuid.uuid4()),
                "id_estacion": str(id_estacion),
                "codigo_pieza": None,
                "unidades_procesadas": 1,
            },
            headers={"X-Device-Key": credencial},
            timeout=5.0,
        )
    except Exception as e:
        # Best-effort: un tick fallido no debe tumbar el scheduler ni
        # los ticks siguientes -- se pierde ese evento de demo, nada más.
        logger.warning(f"[demo] Falló el envío de un scan simulado (estacion={id_estacion}): {e}")


def _tick_simulacion() -> None:
    with Session(engine) as db:
        tenants = db.exec(
            select(Tenant).where(Tenant.es_demo == True, Tenant.demo_simulando_desde.is_not(None))  # noqa: E712
        ).all()
        if not tenants:
            return

        for tenant in tenants:
            if datetime.utcnow() - tenant.demo_simulando_desde > timedelta(hours=MAX_HORAS_SIMULACION):
                tenant.demo_simulando_desde = None
                db.add(tenant)
                db.commit()
                logger.info(f"[demo] Autofrenado tras {MAX_HORAS_SIMULACION}h: tenant={tenant.id}")
                continue

            multiplicador = VELOCIDAD_MULTIPLICADOR.get(tenant.demo_velocidad, 1.0)
            credenciales = db.exec(
                select(DemoCredencialSimulador).where(DemoCredencialSimulador.tenant_id == tenant.id)
            ).all()
            lineas_por_id = {
                linea.id: linea for linea in
                db.exec(select(Linea).where(Linea.tenant_id == tenant.id)).all()
            }
            estaciones_por_id = {
                est.id: est for est in
                db.exec(select(Estacion).where(Estacion.tenant_id == tenant.id, Estacion.activa == True)).all()  # noqa: E712
            }
            for cred in credenciales:
                estacion = estaciones_por_id.get(cred.estacion_id)
                if not estacion:
                    continue
                linea = lineas_por_id.get(estacion.linea_id)
                tiempo_ideal = linea.tiempo_ideal_seg if linea else 60.0

                # Probabilidad de disparar un evento este tick, calibrada
                # para que en promedio salga uno cada tiempo_ideal_seg
                # (ajustado por velocidad). 15% de las veces se "atrasa"
                # a propósito (x2-x4 el tiempo esperado) -- variación
                # real de ritmo, no cada evento a velocidad constante.
                factor_atraso = random.choices([1.0, random.uniform(2.0, 4.0)], weights=[0.85, 0.15])[0]
                probabilidad = min(1.0, (TICK_SEGUNDOS * multiplicador) / (tiempo_ideal * factor_atraso))
                if random.random() < probabilidad:
                    _enviar_scan(cred.credencial_completa, cred.estacion_id)


def _limpiar_demos_expiradas() -> None:
    with Session(engine) as db:
        expiradas = db.exec(
            select(Tenant).where(Tenant.es_demo == True, Tenant.demo_expira_at < datetime.utcnow())  # noqa: E712
        ).all()
        for tenant in expiradas:
            _borrar_tenant_demo(db, tenant.id)
            logger.info(f"[demo] Tenant demo expirado, borrado: id={tenant.id}")


def _borrar_tenant_demo(db: Session, tenant_id: str) -> None:
    """Borrado real (no soft-delete): datos 100% descartables, no
    aplica ningún criterio de "no borrar, archivar" del resto del
    esquema. Orden explícito por dependencias de FK -- este backend no
    tiene ON DELETE CASCADE configurado a nivel de esquema."""
    estaciones_ids = [str(e) for e in db.exec(select(Estacion.id).where(Estacion.tenant_id == tenant_id)).all()]
    if estaciones_ids:
        db.exec(delete(LiteEventoProduccion).where(LiteEventoProduccion.id_estacion.in_(estaciones_ids)))
    db.exec(delete(ParadaDetectada).where(ParadaDetectada.tenant_id == tenant_id))
    db.exec(delete(SesionOperario).where(SesionOperario.tenant_id == tenant_id))
    db.exec(delete(AsignacionTurno).where(AsignacionTurno.tenant_id == tenant_id))
    db.exec(delete(DemoCredencialSimulador).where(DemoCredencialSimulador.tenant_id == tenant_id))
    db.exec(delete(ApiKeyDispositivo).where(ApiKeyDispositivo.tenant_id == tenant_id))
    db.exec(delete(MaestroSKU).where(MaestroSKU.tenant_id == tenant_id))
    db.exec(delete(Turno).where(Turno.tenant_id == tenant_id))
    db.exec(delete(Estacion).where(Estacion.tenant_id == tenant_id))
    db.exec(delete(Linea).where(Linea.tenant_id == tenant_id))
    db.exec(delete(Planta).where(Planta.tenant_id == tenant_id))
    db.exec(delete(Tenant).where(Tenant.id == tenant_id))
    db.commit()


def eliminar_demo_manual(tenant_id: str) -> None:
    """Borrado anticipado a pedido (DELETE /admin/demo/{tenant_id}) --
    misma rutina que la limpieza automática, sesión propia porque se
    llama desde un request HTTP, no desde el scheduler."""
    with Session(engine) as db:
        _borrar_tenant_demo(db, tenant_id)


def iniciar_scheduler() -> None:
    """Llamado una sola vez desde el lifespan de main.py. Guardado
    (PYTEST_CURRENT_TEST) para que la suite de tests nunca levante un
    scheduler en background -- ver ese chequeo en main.py."""
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(_tick_simulacion, "interval", seconds=TICK_SEGUNDOS, id="demo_tick", max_instances=1)
    _scheduler.add_job(_limpiar_demos_expiradas, "interval", hours=1, id="demo_limpieza", max_instances=1)
    _scheduler.start()
    logger.info("[demo] Scheduler de ambiente demo iniciado.")


def detener_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
