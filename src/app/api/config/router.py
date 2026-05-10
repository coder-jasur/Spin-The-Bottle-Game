import pathlib
from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(tags=["Config"])

base_dir = pathlib.Path(__file__).resolve().parents[2]
site_dir = base_dir / "site"

@router.get("/server.json")
async def get_server_json():
    return FileResponse(site_dir / "server.json")

@router.get("/assets.json")
async def get_assets_json():
    return FileResponse(site_dir / "assets.json")

@router.get("/client.branches.json")
async def get_branches_json():
    return ["master", "tg", "deploy-client-prod"]
