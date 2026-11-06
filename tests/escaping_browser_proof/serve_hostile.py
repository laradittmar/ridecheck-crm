"""Serves the real app with synthetic hostile rows on a Unix socket, for run_proof.py.

Runs inside `docker run --network none`, so the server has no network at all. SQLite, in memory.
The wrapper adds an operator session and injects the recorder and driver into HTML responses.
"""
import json
import os
import sys
import types
from datetime import date, datetime, time, timedelta, timezone

SOCKET, DRIVER = sys.argv[1], sys.argv[2]
sys.path.insert(0, "/work/backend")
for name in ("resend", "openai", "anthropic", "boto3", "botocore", "botocore.exceptions"):
    sys.modules.setdefault(name, types.ModuleType(name))
os.environ.update(AUTH_SECRET_KEY="proof-signing-key", ADMIN_PASSWORD="proof-admin-password")

import sqlalchemy
import sqlalchemy.dialects.postgresql as _pg
import sqlalchemy.dialects.postgresql.json as _pgj
_pg.JSONB = sqlalchemy.JSON
_pgj.JSONB = sqlalchemy.JSON
import uvicorn
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.db as app_db
import app.models as models
from app.auth import sign_session
from app.main import SESSION_COOKIE, app as fastapi_app
from app.models import (Agencia, Lead, Profesional, Revision, Vendedor, ViaticosZone,
                        WhatsAppContact, WhatsAppMessage, WhatsAppThread)

ATTACKS = {
    "img": "<img src=x onerror=__pwn.push('@')>",
    "script": "<script>__pwn.push('@')</script>",
    "dq": '" onmouseover="__pwn.push(`@`)" x="',
    "sq": "' onfocus='__pwn.push(`@`)' autofocus='",
    "attrtag": "\"><img src=x onerror=__pwn.push('@')>",
    "jsq": "');__pwn.push('@');//",
}

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
event.listen(engine, "connect", lambda c, r: c.execute("PRAGMA foreign_keys=OFF"))
models.Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
db = Session()
app_db.SessionLocal = Session


def get_session():
    session = Session()
    try:
        yield session
    finally:
        session.close()


def overrides(dependant):
    for dep in dependant.dependencies:
        if getattr(dep.call, "__name__", "").endswith("get_db") or \
                getattr(dep.call, "__name__", "") == "_get_db_gen":
            fastapi_app.dependency_overrides[dep.call] = get_session
        overrides(dep)


for route in fastapi_app.routes:
    if getattr(route, "dependant", None) is not None:
        overrides(route.dependant)
fastapi_app.dependency_overrides[app_db.get_db] = get_session

now = datetime.now(timezone.utc)
for a, attack in ATTACKS.items():
    def v(field, attack=attack, a=a):
        return attack.replace("@", f"{a}:{field}")

    prof = Profesional(nombre=v("prof.nombre"), apellido=v("prof.apellido"), email=v("prof.email"),
                       telefono=v("prof.telefono"), cargo=v("prof.cargo"))
    vend = Vendedor(nombre=v("vend.nombre"))
    lead = Lead(estado="AGENDADO", flag=v("lead.flag"), nombre=v("lead.nombre"),
                apellido=v("lead.apellido"), telefono=v("lead.telefono"), email=v("lead.email"))
    db.add_all([prof, vend, lead,
                ViaticosZone(zone_group=v("zone.group"), zone_detail=v("zone.detail"), viaticos=1)])
    db.flush()
    agencia = Agencia(nombre_agencia=v("ag.nombre"), direccion=v("ag.direccion"),
                      gmaps="https://m.invalid/" + v("ag.gmaps"), mail=v("ag.mail"),
                      telefono=v("ag.telefono"), vendedor_id=vend.id, file_name=v("ag.file"),
                      file_path="/uploads/x.xlsx", fecha_subido=datetime(2026, 11, 4, 9, 30))
    db.add(agencia)
    db.flush()
    db.add(Revision(lead_id=lead.id, profesional_id=prof.id, agencia_id=agencia.id,
                    turno_fecha=date(2026, 11, 4), turno_hora=time(10, 0), precio_total=130000,
                    estado_revision=v("rev.estado"), marca=v("rev.marca"), modelo=v("rev.modelo"),
                    anio=2020, tipo_vehiculo=v("rev.tipo"), zone_group=v("zone.group"),
                    zone_detail=v("zone.detail"), direccion_texto=v("rev.direccion")))
    contact = WhatsAppContact(wa_id=v("wa.id"), display_name=v("wa.name"))
    db.add(contact)
    db.flush()
    thread = WhatsAppThread(contact_id=contact.id, lead_id=lead.id, last_message_at=now,
                            unread_count=1)
    db.add(thread)
    db.flush()
    db.add(WhatsAppMessage(thread_id=thread.id, wa_message_id=f"in-{a}", direction="in",
                           timestamp=now, message_type="text", text=v("msg.in"), status="received"))
    db.add(WhatsAppMessage(thread_id=thread.id, wa_message_id=f"out-{a}", direction="out",
                           timestamp=now, message_type="text", text=v("msg.out"), status="sent",
                           automated=True, path_id=v("path")[:80], deployment_id=v("dep")[:80],
                           blocked_reason=v("blocked")))
    db.add(WhatsAppMessage(
        thread_id=thread.id, wa_message_id=f"flow-{a}", direction="in", timestamp=now,
        message_type="flow_response", text="-", status="received",
        raw_payload={"interactive": {"nfm_reply": {"response_json": json.dumps(
            {"zona_general": v("flow.zona"), "marca": v("flow.marca"), "modelo": v("flow.modelo")})}}}))
# A lead with no thread: its WhatsApp button opens the picker instead of the conversation.
db.add(Lead(estado="AGENDADO", nombre="SINTHREAD", apellido="Prueba", telefono="1100000000"))
db.commit()
THREAD_ID = db.query(WhatsAppThread.id).order_by(WhatsAppThread.id).first()[0]
db.close()

RECORDER = (b'<script id="__seed">window.__pwn=[];'
            b'window.__Pwn={Push:function(v){window.__pwn.push("title:"+v)}};</script>')
SCRIPT = b'<script id="__driver">' + open(DRIVER, "rb").read() + b"</script>"
COOKIE = f"{SESSION_COOKIE}={sign_session({'email': 'operator@ridecheck.invalid'})}".encode()


async def wrapped(scope, receive, send):
    if scope["type"] != "http":
        return await fastapi_app(scope, receive, send)
    scope = dict(scope)
    scope["headers"] = [h for h in scope["headers"] if h[0] != b"cookie"] + [(b"cookie", COOKIE)]
    if scope["path"] == "/__thread":
        body = str(THREAD_ID).encode()
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-length", str(len(body)).encode())]})
        return await send({"type": "http.response.body", "body": body})
    start, chunks = {}, []

    async def capture(message):
        if message["type"] == "http.response.start":
            start.update(message)
        else:
            chunks.append(message.get("body", b""))
            if not message.get("more_body"):
                body = b"".join(chunks)
                headers = dict(start.get("headers", []))
                if b"text/html" in headers.get(b"content-type", b""):
                    if body[:9].lower() == b"<!doctype":
                        cut = body.index(b">") + 1
                        body = body[:cut] + RECORDER + body[cut:]
                    else:
                        body = b"<!doctype html>" + RECORDER + body
                    body += SCRIPT
                out = [(k, v) for k, v in start.get("headers", []) if k != b"content-length"]
                out.append((b"content-length", str(len(body)).encode()))
                await send({"type": "http.response.start", "status": start["status"], "headers": out})
                await send({"type": "http.response.body", "body": body})

    await fastapi_app(scope, receive, capture)


uvicorn.run(wrapped, uds=SOCKET, lifespan="off", log_level="warning")
