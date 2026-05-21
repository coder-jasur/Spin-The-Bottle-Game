"""
Main FastAPI ilovasi — to'liq konfiguratsiya.
"""
import os
import pathlib
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from src.app.core.config import load_config
from src.app.core.room_policy import DEFAULT_SEED_COUNTRY_CODES
from src.app.database.base import Database

# Barcha modellar import — create_all ishlashi uchun shart
from src.app.database.models.achievement import Achievement, UserAchievement  # noqa
from src.app.database.models.admin       import AdminActionLog, Admins, BroadcastMessage  # noqa
from src.app.database.models.booster     import UserBooster  # noqa
from src.app.database.models.relation    import UserRelation  # noqa
from src.app.database.models.stats       import UserStats  # noqa
from src.app.database.models.story       import Story, StoryLike, StoryView  # noqa
from src.app.database.models.table       import TableRoom  # noqa
from src.app.database.models.table_chat  import TableChatMessage  # noqa
from src.app.database.models.transaction import Transaction  # noqa
from src.app.database.models.user        import User  # noqa
from src.app.database.models.wallet      import Wallet  # noqa
from src.app.database.models.user_music  import UserMusicFolder  # noqa
from src.app.database.models.music_track import MusicTrack  # noqa
from src.app.database.models.user_music_gallery import UserMusicGalleryItem  # noqa
from src.app.database.models.partner import Partner  # noqa
from src.app.database.models.referral_bonus import (  # noqa
    ReferralBonusSettings,
    ReferralDailyEarnings,
)


async def startup_application(
    app: FastAPI,
    settings=None,
    *,
    start_bot_polling: bool = False,
) -> None:
    """DB, WS, ixtiyoriy Telegram bot (polling). `main.py` yoki lifespan chaqiradi."""
    import asyncio

    settings = settings or load_config()
    app.state.settings = settings

    from src.app.services.telegram_bot_info import warm_bot_username_cache

    try:
        await warm_bot_username_cache(app)
    except Exception as e:
        print(f"[WARN] Telegram getMe (bot username): {e}", flush=True)

    dsn = settings.construct_postgresql_url()
    db = Database(dsn)

    from src.app.database.base import Base

    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from src.app.database.schema_patches import apply_schema_patches

    try:
        await apply_schema_patches(db.engine)
    except Exception as e:
        print(f"[WARN] DB schema patches: {e}", flush=True)

    from src.app.api.ws.game_manager import manager as ws_manager

    ws_manager.set_db_factory(db.session_factory)

    async with db.session_factory() as session:
        from src.app.database.repositories.game import GameRepository
        from src.app.database.repositories.referral import ReferralRepository

        await ReferralRepository(session).ensure_default_settings()
        await session.commit()

        repo = GameRepository(session)
        for country in DEFAULT_SEED_COUNTRY_CODES:
            try:
                await repo.ensure_base_rooms(country, min_count=5)
            except Exception as e:
                print(f"Room yaratishda xato [{country}]: {e}", flush=True)

    try:
        from src.app.services.telegram_profile import (
            ensure_no_img_placeholder,
            purge_invalid_local_avatars,
        )

        n = purge_invalid_local_avatars()
        if n:
            print(f"[OK] Noto'g'ri avatar fayllari o'chirildi: {n}", flush=True)
        await ensure_no_img_placeholder()
    except Exception as e:
        print(f"[WARN] Avatar tozalash: {e}", flush=True)

    try:
        from src.app.api.music.service import redis_cache_status

        rc = await redis_cache_status()
        if rc.get("redis_connected"):
            print("[OK] Musiqa cache: Redis", flush=True)
        elif rc.get("enabled"):
            print("[WARN] Musiqa cache: Redis ulanmadi, RAM fallback", flush=True)
    except Exception as e:
        print(f"[WARN] Musiqa Redis: {e}", flush=True)

    try:
        from src.app.core.geo import _get_reader, geoip_status

        st = geoip_status()
        if st.get("valid"):
            _get_reader()
            print(f"[OK] GeoIP: {st['path']} ({st['size'] // 1024} KB)", flush=True)
        else:
            print(
                f"[WARN] GeoIP DB yo'q yoki bo'sh: {st['path']} — "
                "mamlakat ip-api.com orqali aniqlanadi; "
                "tavsiya: MaxMind GeoLite2-Country.mmdb ni VPS ga yuklang",
                flush=True,
            )
    except Exception as e:
        print(f"[WARN] GeoIP: {e}", flush=True)

    try:
        from src.app.api.music.router import warm_music_catalog_cache

        n_tracks = await warm_music_catalog_cache()
        if n_tracks:
            print(f"[OK] Musiqa katalogi yuklandi: {n_tracks} trek", flush=True)
    except Exception as e:
        print(f"[WARN] Musiqa katalogi: {e}", flush=True)

    try:
        from src.app.api.music.service import (
            rapidapi_yt_enabled,
            rapidapi_yt_runtime_status,
            ytdlp_log_startup_config,
        )

        if rapidapi_yt_enabled():
            st = await rapidapi_yt_runtime_status()
            print(
                f"[OK] Musiqa (audio): RapidAPI YT MP3 ({st.get('host')}); "
                f"to'liq trek. Status: /api_music/rapidapi_yt_status",
                flush=True,
            )
        else:
            ytdlp_log_startup_config()
    except Exception as e:
        print(f"[WARN] Musiqa provayder config: {e}", flush=True)

    app.state.db = db
    app.state.user_cache = {}
    app.state.bot = None
    app.state.dp = None
    app.state.bot_poll_task = None
    app.state.bot_shutdown_event = asyncio.Event()
    app.state.shutting_down = False

    if getattr(settings, "bot_token", None):
        try:
            from src.app.bot.setup import create_bot_and_dispatcher
            from src.app.services.telegram_payments import set_telegram_bot

            from src.app.bot.admin_access import list_admin_telegram_chat_ids
            from src.app.bot.commands import register_bot_commands, refresh_admin_commands_for_chat

            bot, dp = create_bot_and_dispatcher(settings, db.session_factory)
            try:
                from src.app.bot.handlers.admin import panel_keyboard

                _n = len(panel_keyboard().inline_keyboard)
                print(
                    f"[OK] Admin panel: {_n} tugma "
                    f"(broadcast={'yoq' if _n < 4 else 'ha'})",
                    flush=True,
                )
            except Exception as e:
                print(f"[WARN] Admin panel tekshiruvi: {e}", flush=True)
            app.state.bot = bot
            app.state.dp = dp
            set_telegram_bot(bot)
            try:
                await register_bot_commands(bot)
            except Exception as e:
                print(f"[WARN] Bot buyruqlari ro'yxati: {e}", flush=True)
            try:
                from src.app.bot.admin_access import get_user_by_telegram_id
                from src.app.core.language import bot_lang_from_db_user

                async with db.session_factory() as session:
                    admin_chats = await list_admin_telegram_chat_ids(session)
                    for chat_id in admin_chats:
                        u = await get_user_by_telegram_id(session, chat_id)
                        lang = bot_lang_from_db_user(u)
                        await refresh_admin_commands_for_chat(
                            bot, chat_id, lang
                        )
            except Exception as e:
                print(f"[WARN] Admin bot buyruqlari: {e}", flush=True)
            webapp_url = getattr(settings, "telegram_webapp_url", "") or ""
            if webapp_url:
                try:
                    from src.app.bot.webapp_menu import configure_miniapp_menu

                    await configure_miniapp_menu(bot, webapp_url)
                except Exception as e:
                    print(f"[WARN] Mini App menu sozlanmadi: {e}", flush=True)
            if start_bot_polling and settings.telegram_use_polling:
                from src.app.bot.polling_runner import run_bot_polling

                app.state.bot_shutdown_event.clear()
                app.state.bot_poll_task = asyncio.create_task(
                    run_bot_polling(
                        bot,
                        dp,
                        shutdown_event=app.state.bot_shutdown_event,
                    ),
                    name="telegram-polling",
                )
                print("[OK] Telegram bot polling ishga tushdi (main.py).", flush=True)
            elif start_bot_polling:
                print("[OK] Telegram bot webhook rejimi (polling o'chiq).", flush=True)
            else:
                print("[OK] Telegram bot tayyor (polling keyinroq).", flush=True)
            if app.state.bot:
                try:
                    from src.app.services.scheduled_backup import start_scheduled_backup

                    start_scheduled_backup(
                        app.state.bot,
                        db.session_factory,
                        interval_hours=getattr(
                            settings, "scheduled_backup_interval_hours", 24.0
                        ),
                        enabled=getattr(
                            settings, "scheduled_backup_enabled", True
                        ),
                    )
                except Exception as e:
                    print(f"[WARN] Scheduled backup: {e}", flush=True)
        except Exception as e:
            print(f"[WARN] Telegram bot ishga tushmadi: {e}", flush=True)

    _site_root = pathlib.Path(__file__).resolve().parent.parent / "site"
    for page, route in (
        ("login.html", "/"),
        ("index.html", "/index"),
        ("welcome.html", "/welcome"),
        ("admin.html", "/admin"),
    ):
        if (_site_root / page).is_file():
            print(f"[OK] Sayt: {route} ({page})", flush=True)
        else:
            print(f"[WARN] Sayt topilmadi: {page} → {route}", flush=True)
    if (_site_root / "server.json").is_file():
        print("[OK] Sayt: /server.json", flush=True)

    app.state.bootstrapped = True
    print("[OK] Database ulandi, jadvallar va xonalar tayyor.", flush=True)


async def shutdown_application(app: FastAPI) -> None:
    """Bot polling va DB ulanishini yopish."""
    import asyncio
    from contextlib import suppress

    app.state.shutting_down = True

    try:
        from src.app.services.scheduled_backup import stop_scheduled_backup

        await stop_scheduled_backup()
    except Exception:
        pass

    shutdown_ev = getattr(app.state, "bot_shutdown_event", None)
    if shutdown_ev:
        shutdown_ev.set()

    dp = getattr(app.state, "dp", None)
    try:
        from src.app.bot.polling_runner import stop_bot_polling

        await stop_bot_polling(dp)
    except Exception:
        pass

    bot_poll_task = getattr(app.state, "bot_poll_task", None)
    if bot_poll_task and not bot_poll_task.done():
        bot_poll_task.cancel()
        with suppress(asyncio.CancelledError):
            await bot_poll_task

    bot = getattr(app.state, "bot", None)
    if bot:
        with suppress(Exception):
            await bot.session.close()

    db = getattr(app.state, "db", None)
    if db:
        with suppress(Exception):
            await db.engine.dispose()
    print("[OK] Server to'xtatildi (DB yopildi).", flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # `python -m src.app.main` — startup allaqachon main.py da
    if not getattr(app.state, "bootstrapped", False):
        await startup_application(app, start_bot_polling=True)
    try:
        yield
    finally:
        if not getattr(app.state, "bootstrapped_from_main", False):
            await shutdown_application(app)


app = FastAPI(lifespan=lifespan, title="SpinBottle API")

_settings_boot = load_config()
_trusted = _settings_boot.trusted_hosts
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=_trusted)

from src.app.core.security.headers import SecurityHeadersMiddleware
from src.app.core.security.rate_limit import RateLimitMiddleware

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    RateLimitMiddleware,
    enabled=_settings_boot.rate_limit_enabled,
    redis_url=_settings_boot.redis_url,
)

# `allow_origins=["*"]` + `allow_credentials=True` — brauzer cookie yubormasligi mumkin (CORS).
# Regex orqali kelgan Origin'ni aks ettiramiz — `/api/auth/refresh` cookie bilan ishlashi uchun.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://[\w.:-]+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def media_embed_headers(request: Request, call_next):
    """YouTube iframe va MP3 uchun kerakli ruxsatlar (localhost dev)."""
    response = await call_next(request)
    path = request.url.path or ""
    if path.endswith((".html", ".js", ".css")) or path in ("/", "/index.html", "/welcome.html"):
        csp = (
            "frame-src 'self' https://www.youtube.com https://www.youtube-nocookie.com "
            "https://*.google.com https://*.doubleclick.net; "
            "connect-src 'self' https: wss: blob:; "
            "media-src 'self' https: blob:; "
            "img-src 'self' https: data: blob:;"
        )
        response.headers.setdefault("Content-Security-Policy", csp)
        response.headers.setdefault(
            "Permissions-Policy",
            "autoplay=(self), encrypted-media=(self), fullscreen=(self)",
        )
    return response

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

for folder in ["static", "libs", "assets", "favicon", "data"]:
    path = site_dir / folder
    if path.exists():
        app.mount(f"/{folder}", StaticFiles(directory=str(path)), name=folder)


# ── JSON fayllar (katalog o'zgarganda brauzer eski assets.json ni ushlab qolmasin) ──
_ASSETS_NO_CACHE = {"Cache-Control": "no-store, max-age=0, must-revalidate"}


@app.get("/assets.json")
async def get_assets_json():
    return FileResponse(site_dir / "assets.json", headers=_ASSETS_NO_CACHE)


@app.get("/server.json")
async def get_server_json(request: Request):
    from src.app.api.config.server_json import build_server_json

    settings = getattr(request.app.state, "settings", None) or load_config()
    return JSONResponse(
        build_server_json(request, settings),
        headers=_ASSETS_NO_CACHE,
    )


@app.get("/favicon.ico")
async def favicon():
    return FileResponse(site_dir / "favicon" / "favicon.ico")


@app.get("/favicon-96x96.png")
async def favicon_96_png():
    """welcome.html — ildizda /favicon-96x96.png; fayl `site/favicon/` ichida bo‘lishi kerak."""
    for name in ("favicon-96x96.png", "favicon.png"):
        path = site_dir / "favicon" / name
        if path.is_file():
            return FileResponse(path)
    raise HTTPException(
        status_code=404,
        detail="site/favicon/ ichida favicon-96x96.png yoki favicon.png qo‘ying",
    )


@app.get("/favicon.svg")
async def favicon_svg_root():
    path = site_dir / "favicon" / "favicon.svg"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="site/favicon/favicon.svg topilmadi")
    return FileResponse(path)


# ── Routerlar ─────────────────────────────────────────────────────────────
from src.app.api.admin.router        import router as admin_router    # noqa
from src.app.api.auth.init           import router as init_router     # noqa
from src.app.api.auth.login          import router as login_router    # noqa
from src.app.api.auth.logout.router  import router as logout_router   # noqa
from src.app.api.auth.me             import router as me_router       # noqa
from src.app.api.auth.refresh.router import router as refresh_router  # noqa
from src.app.api.auth.register       import router as register_router # noqa
from src.app.api.auth.telegram.router import router as telegram_router # noqa
from src.app.api.auth.verify         import router as verify_router   # noqa
from src.app.api.config              import router as config_router   # noqa
from src.app.api.frames              import router as frames_router   # noqa
from src.app.api.music               import router as music_router     # noqa
from src.app.api.live                import router as live_router     # noqa
from src.app.api.proxy               import router as proxy_router    # noqa
from src.app.api.stories.router      import router as stories_router  # noqa
from src.app.api.tables              import router as tables_router   # noqa
from src.app.api.web                 import router as web_router      # noqa
from src.app.api.ws                  import router as ws_router       # noqa
from src.app.api.payments.router     import router as payments_router # noqa
from src.app.api.telegram_webhook.router import router as tg_webhook_router # noqa

app.include_router(config_router)
app.include_router(music_router)
app.include_router(web_router)
app.include_router(admin_router)
app.include_router(init_router)
app.include_router(login_router)
app.include_router(telegram_router)
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
app.include_router(payments_router)
app.include_router(tg_webhook_router)


def _prioritize_router_routes(application: FastAPI, router) -> None:
    """`/{folder}/{filename}` asset catch-all dan oldin api_music ishlashi uchun."""
    for route in reversed(router.routes):
        application.router.routes.insert(0, route)


_prioritize_router_routes(app, music_router)
_prioritize_router_routes(app, frames_router)


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
    if folder in ["api", "ws"] or (folder and folder.startswith("api_")):
        return Response(status_code=404)

    # dlg100 / 80 / 120 — fayllar papka ichida tekis; gifts/ ostida emas
    if folder.startswith("dlg") or folder.isdigit():
        flat = bottle_bundle_dir / folder / filename
        if flat.exists():
            return FileResponse(str(flat))
        if filename.endswith(".webp"):
            alt = flat.with_suffix(".png")
            if alt.exists():
                return FileResponse(str(alt))
        elif filename.endswith(".png"):
            alt = flat.with_suffix(".webp")
            if alt.exists():
                return FileResponse(str(alt))

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
        "/banned", "/stars-support", "/static", "/assets", "/favicon",
        "/photos", "/images", "/media", "/table", "/stories",
        "/sfx", "/assets.json", "/server.json",
        "/auth/login", "/api/auth/login",
        "/api/auth/register", "/auth/register",
        "/api/auth/telegram",
        "/api/auth/game-entry",
        "/api/admin", "/admin", "/cdn-cgi",
        "/api_music",
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
                    from src.app.core.language import resolve_user_lang
                    from src.app.core.stars_support import build_banned_path

                    settings = getattr(request.app.state, "settings", None)
                    support_user = (
                        getattr(settings, "telegram_support_username", None)
                        if settings
                        else None
                    )
                    lang = resolve_user_lang(
                        cookie_lang=request.cookies.get("language"),
                        db_language_code=getattr(user, "language_code", None),
                    )
                    return RedirectResponse(
                        url=build_banned_path(
                            expires_at=ban_time,
                            lang=lang,
                            support_user=support_user,
                        )
                    )

    return await call_next(request)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    if not request.url.path.startswith("/static"):
        print(f">>> {request.method} {request.url.path}", flush=True)
    return await call_next(request)