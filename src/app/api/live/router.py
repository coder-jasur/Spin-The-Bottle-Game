from fastapi import APIRouter

router = APIRouter(tags=["Live"])

@router.get("/api/live/rankings")
@router.get("/live/rankings")
async def get_rankings():
    return {
        "success": True,
        "rankings": []
    }

@router.get("/api/live/rooms/active")
@router.get("/live/rooms/active")
async def get_active_rooms():
    return {
        "success": True,
        "rooms": []
    }

@router.get("/api/live/top-wins")
@router.get("/live/top-wins")
async def get_top_wins():
    return {
        "success": True,
        "users": []
    }
