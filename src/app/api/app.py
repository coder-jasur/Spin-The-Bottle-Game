"""
Main FastAPI ilovasi — to'liq konfiguratsiya.
"""
import os
import pathlib
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from src.app.core.config import load_config
from src.app.database.base import Database

# Barcha modellar import — create_all ishlashi uchun shart
from src.app.database.models.achievement import Achievement, UserAchievement  # noqa
from src.app.database.models.admin       import AdminActionLog, Admins, BroadcastMessage  # noqa
from src.app.database.models.booster     import UserBooster  # noqa
from src.app.database.models.relation    import UserRelation  # noqa
from src.app.database.models.stats       import UserStats  # noqa
from src.app.database.models.story       import Story, StoryLike, StoryView  # noqa
from src.app.database.models.table       import TableRoom  # noqa
from src.app.database.models.transaction import Transaction  # noqa
from src.app.database.models.user        import User  # noqa
from src.app.database.models.wallet      import Wallet  # noqa


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────
    settings = load_config()
    dsn      = settings.construct_postgresql_url()
    db       = Database(dsn)

    from src.app.database.base import Base
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # DB ni GameManager ga ulash
    from src.app.api.ws.game_manager import manager as ws_manager
    ws_manager.set_db_factory(db.session_factory)

    # Bazaviy xonalarni yaratish
    async with db.session_factory() as session:
        from src.app.database.repositories.game import GameRepository
        repo = GameRepository(session)
        for country in ["UZBEKISTAN", "KAZAKHSTAN", "RUSSIA", "ALL"]:
            try:
                await repo.ensure_base_rooms(country, min_count=5)
            except Exception as e:
                print(f"Room yaratishda xato [{country}]: {e}", flush=True)

    app.state.db         = db
    app.state.user_cache = {}
    print("✅ Database ulandi, jadvallar va xonalar tayyor.", flush=True)

    yield

    # ── Shutdown ─────────────────────────────────────────────────────
    await db.engine.dispose()
    print("Database ulanishi yopildi.", flush=True)


app = FastAPI(lifespan=lifespan, title="SpinBottle API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Yo'llar ──────────────────────────────────────────────────────────────
base_dir          = pathlib.Path(__file__).parent.parent.resolve()
site_dir          = base_dir / "site"
bottle_bundle_dir = site_dir / "bottle" / "bundle"
media_dir         = site_dir / "media"

MAPPING = {
    "g_":    "gifts",
    "a_":    "achievements",
    "b_":    "bottles",
    "f_":    "frames",
    "fp_":   "frames",
    "ges_":  "gestures",
    "s_":    "gestures",
    "cup_":  "cups",
    "boost_":"boosters",
    "n_":    "names",
}

# ── Static fayllar ────────────────────────────────────────────────────────
for folder in ["photos", "images", "media", "table", "stories"]:
    path = media_dir / folder
    os.makedirs(path, exist_ok=True)
    app.mount(f"/{folder}", StaticFiles(directory=str(path)), name=folder)

sfx_path = bottle_bundle_dir / "sfx"
if sfx_path.exists():
    app.mount("/sfx", StaticFiles(directory=str(sfx_path)), name="sfx")

for folder in ["static", "libs", "assets", "favicon"]:
    path = site_dir / folder
    if path.exists():
        app.mount(f"/{folder}", StaticFiles(directory=str(path)), name=folder)


# ── JSON fayllar ─────────────────────────────────────────────────────────
@app.get("/assets.json")
async def get_assets_json():
    return FileResponse(site_dir / "assets.json")


@app.get("/server.json")
async def get_server_json():
    return FileResponse(site_dir / "server.json")


@app.get("/favicon.ico")
async def favicon():
    return FileResponse(site_dir / "favicon" / "favicon.ico")


# ── Routerlar ─────────────────────────────────────────────────────────────
from src.app.api.admin.router        import router as admin_router    # noqa
from src.app.api.auth.init           import router as init_router     # noqa
from src.app.api.auth.login          import router as login_router    # noqa
from src.app.api.auth.logout.router  import router as logout_router   # noqa
from src.app.api.auth.me             import router as me_router       # noqa
from src.app.api.auth.refresh.router import router as refresh_router  # noqa
from src.app.api.auth.register       import router as register_router # noqa
from src.app.api.auth.verify         import router as verify_router   # noqa
from src.app.api.config              import router as config_router   # noqa
from src.app.api.frames              import router as frames_router   # noqa
from src.app.api.live                import router as live_router     # noqa
from src.app.api.proxy               import router as proxy_router    # noqa
from src.app.api.stories.router      import router as stories_router  # noqa
from src.app.api.tables              import router as tables_router   # noqa
from src.app.api.web                 import router as web_router      # noqa
from src.app.api.ws                  import router as ws_router       # noqa

app.include_router(web_router)
app.include_router(config_router)
app.include_router(admin_router)
app.include_router(init_router)
app.include_router(login_router)
app.include_router(register_router)
app.include_router(me_router)
app.include_router(logout_router)
app.include_router(stories_router)
app.include_router(verify_router)
app.include_router(frames_router)
app.include_router(live_router)
app.include_router(tables_router)   # ← yangi qo'shildi
app.include_router(proxy_router)
app.include_router(ws_router)
app.include_router(refresh_router)


# ── Cloudflare RUM ────────────────────────────────────────────────────────
@app.post("/cdn-cgi/rum")
async def rum_handler():
    return {"status": "ok"}


# ── Smart asset handler ───────────────────────────────────────────────────
@app.get("/{folder}/{filename}")
@app.get("/{folder}/{subfolder}/{filename}")
@app.get("/bottle/bundle/{folder}/{filename}")
@app.get("/bottle/bundle/{folder}/{subfolder}/{filename}")
async def smart_asset_handler(folder: str, filename: str, subfolder: str = None):
    if folder in ["api", "ws"]:
        return Response(status_code=404)

    actual_subfolder = subfolder or "others"
    if not subfolder:
        for prefix, sub in MAPPING.items():
            if filename.startswith(prefix):
                actual_subfolder = sub
                break

    path1 = bottle_bundle_dir / folder / actual_subfolder / filename
    if path1.exists():
        return FileResponse(str(path1))

    path2 = bottle_bundle_dir / folder / filename
    if path2.exists():
        return FileResponse(str(path2))

    # Alternativ kengaytmalar
    alt = None
    if filename.endswith(".webp"):
        alt = filename.replace(".webp", ".png")
    elif filename.endswith(".png"):
        alt = filename.replace(".png", ".webp")

    if alt:
        for p in [
            bottle_bundle_dir / folder / actual_subfolder / alt,
            bottle_bundle_dir / folder / alt,
        ]:
            if p.exists():
                return FileResponse(str(p))

    # Global qidirish
    path_global = bottle_bundle_dir / filename
    if path_global.exists():
        return FileResponse(str(path_global))

    return Response(status_code=404)


# ── Ban tekshiruvi ────────────────────────────────────────────────────────
@app.middleware("http")
async def ban_check_middleware(request: Request, call_next):
    path = request.url.path

    skip = [
        "/banned", "/static", "/assets", "/favicon",
        "/photos", "/images", "/media", "/table", "/stories",
        "/sfx", "/assets.json", "/server.json",
        "/auth/login", "/api/auth/login",
        "/api/auth/register", "/auth/register",
        "/api/admin", "/admin", "/cdn-cgi",
    ]
    if any(path.startswith(s) for s in skip) or path == "/":
        return await call_next(request)

    token = request.cookies.get("device_user_ids")
    if not token:
        return await call_next(request)

    import json, urllib.parse
    from src.app.core.jwt import verify_access_token
    from src.app.database.repositories.user import UserRepository

    payload = verify_access_token(token)
    if not payload:
        try:
            decoded = urllib.parse.unquote(token)
            if decoded.startswith("["):
                ids = json.loads(decoded)
                if ids:
                    payload = {"id": int(ids[0])}
        except Exception:
            pass

    if payload and payload.get("id"):
        async with request.app.state.db.session_factory() as session:
            user_repo = UserRepository(session)
            user      = await user_repo.get_user_by_id(payload["id"])
            if user and user.is_banned:
                from datetime import datetime
                now = datetime.now()
                if user.ban_expires_at and now > user.ban_expires_at:
                    user.is_banned         = False
                    user.ban_expires_at    = None
                    user.number_of_complaints = 0
                    await session.commit()
                else:
                    ban_time = (
                        user.ban_expires_at.strftime("%Y-%m-%d %H:%M")
                        if user.ban_expires_at
                        else "umrbod"
                    )
                    from fastapi.responses import RedirectResponse
                    return RedirectResponse(
                        url=f"/banned?expires_at={urllib.parse.quote(ban_time)}"
                    )

    return await call_next(request)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    if not request.url.path.startswith("/static"):
        print(f">>> {request.method} {request.url.path}", flush=True)
    return await call_next(request)