from __future__ import annotations

import logging
import os
import random
import time
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .api.leads import router as leads_router
from .api.excluded_phones import router as excluded_phones_router
from .api.pricing import router as pricing_router
from .api.revision_items import router as revision_items_router
from .api.revisions import router as revisions_router
from .api.schedule import router as schedule_router
from .api.settings import router as settings_router
from .api.thread_revisions import router as thread_revisions_router
from .api.whatsapp import router as whatsapp_api_router, thread_router as whatsapp_thread_router
from .auth import (
    SESSION_COOKIE,
    build_session,
    hash_password,
    login_ok,
    sign_session,
    validate_password_rules,
    verify_password,
    verify_session,
)
from .db import Base, engine, get_db
from .models import User
from .routes.whatsapp import router as whatsapp_router
from .settings import get_settings
from .ui.kanban import router as ui_router
from .ui.whatsapp_ui import router as whatsapp_ui_router

logger = logging.getLogger(__name__)

app = FastAPI()
templates = Jinja2Templates(directory="app/templates")

CAPTCHA_TTL_SECONDS = 5 * 60
RESET_TTL_SECONDS = 15 * 60

# create tables (for now; later we'll migrate to Alembic)
Base.metadata.create_all(bind=engine)

# static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse("app/static/favicon.ico")


def _is_protected_path(path: str) -> bool:
    protected_prefixes = (
        "/kanban",
        "/table",
        "/calendar",
        "/profesionales",
        "/agencias",
        "/whatsapp",
        "/integrations/whatsapp",
        "/ui/",
    )
    return path.startswith(protected_prefixes)


def _is_public_path(path: str) -> bool:
    public_paths = ("/integrations/whatsapp/webhook",)
    return path in public_paths


def _now_ts() -> int:
    return int(time.time())


def _new_captcha() -> tuple[str, str]:
    a = random.randint(1, 9)
    b = random.randint(1, 9)
    question = f"What is {a} + {b}?"
    token = sign_session(
        {
            "kind": "captcha",
            "a": str(a),
            "b": str(b),
            "exp": str(_now_ts() + CAPTCHA_TTL_SECONDS),
        }
    )
    return question, token


def _verify_captcha_token(captcha_token: str | None) -> tuple[bool, int, int]:
    payload = verify_session(captcha_token)
    if not payload or payload.get("kind") != "captcha":
        return False, 0, 0
    try:
        exp = int(str(payload.get("exp", "")).strip())
        a = int(str(payload.get("a", "")).strip())
        b = int(str(payload.get("b", "")).strip())
    except ValueError:
        return False, 0, 0
    if _now_ts() > exp:
        return False, 0, 0
    return True, a, b


def _validate_captcha(captcha_token: str | None, captcha_answer: str | None) -> bool:
    ok, a, b = _verify_captcha_token(captcha_token)
    if not ok:
        return False
    provided = (captcha_answer or "").strip()
    if provided == "":
        return False
    try:
        return int(provided) == (a + b)
    except ValueError:
        return False


def _new_reset_token(email: str) -> str:
    return sign_session(
        {
            "kind": "reset_password",
            "email": email.strip().lower(),
            "exp": str(_now_ts() + RESET_TTL_SECONDS),
        }
    )


def _verify_reset_token(token: str | None) -> str | None:
    payload = verify_session(token)
    if not payload or payload.get("kind") != "reset_password":
        return None
    email = str(payload.get("email", "")).strip().lower()
    if not email:
        return None
    try:
        exp = int(str(payload.get("exp", "")).strip())
    except ValueError:
        return None
    if _now_ts() > exp:
        return None
    return email


def _render_login(
    request: Request,
    error: str | None = None,
    message: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    question, token = _new_captcha()
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "error": error,
            "message": message,
            "captcha_question": question,
            "captcha_token": token,
        },
        status_code=status_code,
    )


def _render_signup(request: Request, error: str | None = None, status_code: int = 200) -> HTMLResponse:
    question, token = _new_captcha()
    return templates.TemplateResponse(
        "signup.html",
        {
            "request": request,
            "error": error,
            "captcha_question": question,
            "captcha_token": token,
        },
        status_code=status_code,
    )


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path or "/"
    if _is_public_path(path):
        return await call_next(request)

    if _is_protected_path(path):
        payload = verify_session(request.cookies.get(SESSION_COOKIE))
        if not payload or not payload.get("email"):
            return RedirectResponse(url="/login", status_code=303)
        request.state.user_email = payload.get("email", "")
    else:
        payload = verify_session(request.cookies.get(SESSION_COOKIE))
        request.state.user_email = payload.get("email", "") if payload else ""
    return await call_next(request)


@app.get("/", include_in_schema=False)
def root(request: Request):
    if getattr(request.state, "user_email", ""):
        return RedirectResponse(url="/kanban", status_code=303)
    return RedirectResponse(url="/login", status_code=303)


@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_page(request: Request):
    if getattr(request.state, "user_email", ""):
        return RedirectResponse(url="/kanban", status_code=303)
    message = "Password updated. Please log in." if request.query_params.get("msg") == "reset_ok" else None
    return _render_login(request, message=message)


@app.post("/login", include_in_schema=False)
def login_action(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    captcha_answer: str = Form(""),
    captcha_token: str = Form(""),
    db: Session = Depends(get_db),
):
    captcha_ok = _validate_captcha(captcha_token, captcha_answer)
    email_norm = email.strip().lower()
    if not captcha_ok:
        logger.info("login attempt email=%s captcha_ok=%s admin_ok=%s", email_norm, False, False)
        return _render_login(request, error="Invalid CAPTCHA", status_code=400)

    admin_ok = login_ok(email_norm, password)
    logger.info("login attempt email=%s captcha_ok=%s admin_ok=%s", email_norm, True, admin_ok)

    is_valid = False
    if admin_ok:
        is_valid = True
    else:
        db_user = db.query(User).filter(User.email == email_norm).first()
        if db_user and verify_password(password, db_user.hashed_password):
            is_valid = True

    if not is_valid:
        return _render_login(request, error="Invalid credentials", status_code=401)

    token = sign_session(build_session(email_norm))
    resp = RedirectResponse(url="/kanban", status_code=303)
    resp.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
        max_age=60 * 60 * 12,
    )
    return resp


@app.get("/signup", response_class=HTMLResponse, include_in_schema=False)
def signup_form(request: Request):
    return _render_signup(request)


@app.post("/signup", include_in_schema=False)
def signup(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    captcha_answer: str = Form(""),
    captcha_token: str = Form(""),
    db: Session = Depends(get_db),
):
    if not _validate_captcha(captcha_token, captcha_answer):
        return _render_signup(request, error="Invalid CAPTCHA", status_code=400)

    err = validate_password_rules(password)
    if err:
        return _render_signup(request, error=err, status_code=400)

    email_norm = email.strip().lower()
    existing = db.query(User).filter(User.email == email_norm).first()
    if existing:
        return _render_signup(request, error="Email already exists", status_code=400)

    user = User(email=email_norm, hashed_password=hash_password(password))
    db.add(user)
    db.commit()
    return RedirectResponse("/login", status_code=302)


@app.get("/forgot-password", response_class=HTMLResponse, include_in_schema=False)
def forgot_password_form(request: Request):
    return templates.TemplateResponse(
        "forgot_password.html",
        {"request": request, "error": None, "reset_link": None},
    )


@app.post("/forgot-password", include_in_schema=False)
def forgot_password_action(
    request: Request,
    email: str = Form(...),
    db: Session = Depends(get_db),
):
    email_norm = email.strip().lower()
    user = db.query(User).filter(User.email == email_norm).first()
    if not user:
        return templates.TemplateResponse(
            "forgot_password.html",
            {"request": request, "error": "User not found", "reset_link": None},
            status_code=404,
        )

    token = _new_reset_token(email_norm)
    base = str(request.base_url).rstrip("/")
    reset_link = f"{base}/reset-password?token={quote(token)}"
    return templates.TemplateResponse(
        "forgot_password.html",
        {"request": request, "error": None, "reset_link": reset_link},
        status_code=200,
    )


@app.get("/reset-password", response_class=HTMLResponse, include_in_schema=False)
def reset_password_form(request: Request, token: str = Query(default="")):
    email_from_token = _verify_reset_token(token)
    if not email_from_token:
        return templates.TemplateResponse(
            "reset.html",
            {"request": request, "error": "Invalid or expired reset token", "token": "", "email": ""},
            status_code=400,
        )
    return templates.TemplateResponse(
        "reset.html",
        {"request": request, "error": None, "token": token, "email": email_from_token},
        status_code=200,
    )


@app.post("/reset-password", include_in_schema=False)
def reset_password_submit(
    request: Request,
    token: str = Form(""),
    new_password: str = Form(""),
    confirm_password: str = Form(""),
    db: Session = Depends(get_db),
):
    email_from_token = _verify_reset_token(token)
    if not email_from_token:
        return templates.TemplateResponse(
            "reset.html",
            {"request": request, "error": "Invalid or expired reset token", "token": "", "email": ""},
            status_code=400,
        )

    if new_password != confirm_password:
        return templates.TemplateResponse(
            "reset.html",
            {"request": request, "error": "Passwords do not match", "token": token, "email": email_from_token},
            status_code=400,
        )

    err = validate_password_rules(new_password)
    if err:
        return templates.TemplateResponse(
            "reset.html",
            {"request": request, "error": err, "token": token, "email": email_from_token},
            status_code=400,
        )

    user = db.query(User).filter(User.email == email_from_token).first()
    if not user:
        return templates.TemplateResponse(
            "reset.html",
            {"request": request, "error": "User not found", "token": "", "email": ""},
            status_code=404,
        )

    user.hashed_password = hash_password(new_password)
    db.commit()
    return RedirectResponse("/login?msg=reset_ok", status_code=302)


@app.post("/logout", include_in_schema=False)
def logout_action():
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


@app.on_event("startup")
def validate_whatsapp_settings() -> None:
    settings = get_settings()
    missing = settings.missing_whatsapp_required_vars()
    if missing:
        logger.error(
            "WhatsApp Cloud API integration appears enabled but is missing required env vars: %s. "
            "Set all required vars or leave WhatsApp vars empty to keep integration disabled.",
            ", ".join(missing),
        )

# routers
app.include_router(leads_router)
app.include_router(excluded_phones_router)
app.include_router(pricing_router)
app.include_router(revision_items_router)
app.include_router(revisions_router)
app.include_router(thread_revisions_router)
app.include_router(schedule_router)
app.include_router(settings_router)
app.include_router(whatsapp_api_router)
app.include_router(whatsapp_thread_router)
app.include_router(ui_router)
app.include_router(whatsapp_ui_router)
app.include_router(whatsapp_router)



