import pathlib

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from src.app.api.config.server_json import build_server_json
from src.app.core.config import load_config

router = APIRouter(tags=["Config"])

base_dir = pathlib.Path(__file__).resolve().parents[2]
site_dir = base_dir / "site"

_ASSETS_NO_CACHE = {"Cache-Control": "no-store, max-age=0, must-revalidate"}


@router.get("/server.json")
async def get_server_json(request: Request):
    settings = getattr(request.app.state, "settings", None) or load_config()
    return JSONResponse(
        build_server_json(request, settings),
        headers=_ASSETS_NO_CACHE,
    )


@router.get("/assets.json")
async def get_assets_json():
    return FileResponse(site_dir / "assets.json", headers=_ASSETS_NO_CACHE)

@router.get("/client.branches.json")
async def get_branches_json():
    return ["master", "tg", "deploy-client-prod"]
