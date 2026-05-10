import httpx
from fastapi import APIRouter, Response

router = APIRouter(tags=["Proxy"])

async def proxy_request(url: str):
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, follow_redirects=True)
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                media_type=resp.headers.get("content-type"),
            )
        except Exception as e:
            return Response(content=str(e), status_code=500)

@router.get("/photos/{path:path}")
async def proxy_photos(path: str):
    return await proxy_request(f"https://bottle.tgspinbotlle.com/photos/{path}")

@router.get("/sticker/{path:path}")
async def proxy_stickers(path: str):
    return await proxy_request(f"https://bottle.tgspinbotlle.com/sticker/{path}")
