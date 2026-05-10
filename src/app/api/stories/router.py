from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
import os
import shutil
import time
from pathlib import Path
from typing import List, Optional

from src.app.core.jwt import verify_access_token
from src.app.database.repositories.story import StoryRepository
from src.app.database.repositories.user import UserRepository

router = APIRouter(tags=["Stories"])


async def get_db(request: Request) -> AsyncSession:
    async with request.app.state.db.session_factory() as session:
        yield session


async def perform_lazy_cleanup(session: AsyncSession):
    """So'rov paytida eskirgan storylarni va fayllarni tozalash"""
    try:
        from src.app.database.repositories.story import StoryRepository
        import time
        from pathlib import Path

        story_repo = StoryRepository(session)
        
        # 1. Bazadagi muddati o'tganlarni o'chirish
        expired_stories = await story_repo.get_expired_stories()
        for story in expired_stories:
            await story_repo.force_delete_story(story)

        # 2. Papkadagi etim fayllarni o'chirish
        base_dir = Path(__file__).parent.parent.parent.parent.parent.resolve()
        stories_dir = base_dir / "src" / "app" / "site" / "media" / "stories"
        
        if stories_dir.exists():
            now = time.time()
            for file_path in stories_dir.glob("*"):
                if file_path.is_file():
                    # 24 soatdan o'tgan bo'lsa
                    if (now - file_path.stat().st_mtime) > 86400:
                        try:
                            os.remove(file_path)
                        except:
                            pass
    except Exception as e:
        print(f">>> LAZY CLEANUP ERROR: {e}", flush=True)


@router.post("/api/auth/add-status")
@router.post("/api/stories/upload")
@router.post("/stories/upload")
async def upload_story(
    request: Request,
    file: UploadFile = File(..., alias="media"),
    text: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_db)
):
    # Tozalashni amalga oshiramiz
    await perform_lazy_cleanup(session)
    # Tokenni Authorization header'dan yoki cookie'dan olamiz
    auth_header = request.headers.get("Authorization")
    token = None
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
    if not token:
        token = request.cookies.get("device_user_ids")

    payload = verify_access_token(token) if token else None
    if not payload:
        raise HTTPException(status_code=401, detail="Unauthorized")

    user_id = payload.get("id")
    
    # Fayl turi (image yoki video)
    content_type = file.content_type
    if content_type.startswith("image"):
        media_type = "image"
        ext = ".png"
    elif content_type.startswith("video"):
        media_type = "video"
        ext = ".mp4"
    else:
        raise HTTPException(status_code=400, detail="Faqat rasm yoki video yuklash mumkin")

    # Saqlash yo'li
    base_dir = Path(__file__).parent.parent.parent.parent.parent.resolve()
    stories_dir = base_dir / "src" / "app" / "site" / "media" / "stories"
    
    if not stories_dir.exists():
        os.makedirs(stories_dir, exist_ok=True)
        
    file_name = f"story_{user_id}_{int(time.time())}{ext}"
    file_path = stories_dir / file_name
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    # Bazaga qo'shish
    story_repo = StoryRepository(session)
    story = await story_repo.add_story(
        user_id=user_id,
        media_url=f"/stories/{file_name}",
        media_type=media_type,
        caption=text
    )
    
    return {"success": True, "story_id": story.id, "media_url": story.media_url}


@router.get("/api/stories/all")
@router.get("/stories/all")
async def get_all_stories(request: Request, session: AsyncSession = Depends(get_db)):
    # Tozalashni amalga oshiramiz
    await perform_lazy_cleanup(session)
    
    story_repo = StoryRepository(session)
    stories = await story_repo.get_active_stories()
    
    result = []
    for s in stories:
        result.append({
            "id": s.id,
            "user_id": s.user_id,
            "username": s.user.username or s.user.display_name or f"user_{s.user.id}",
            "profile_picture": s.user.avatar_url or "/photos/no_img.png",
            "media_url": s.media_url,
            "media_type": s.media_type,
            "caption": s.caption,
            "views_count": len(s.views),
            "likes_count": len(s.likes),
            "created_at": s.created_at.isoformat(),
            "viewers": [v.user_id for v in s.views],
            "likers": [l.user_id for l in s.likes]
        })
    
    return {"success": True, "stories": result}


@router.post("/api/stories/{story_id}/view")
@router.post("/stories/{story_id}/view")
async def view_story(
    story_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db)
):
    auth_header = request.headers.get("Authorization")
    token = None
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
    if not token:
        token = request.cookies.get("device_user_ids")
        
    payload = verify_access_token(token) if token else None
    if not payload:
        raise HTTPException(status_code=401, detail="Unauthorized")

    story_repo = StoryRepository(session)
    user_id = payload.get("id")
    print(f">>> DEBUG STORY: User {user_id} is viewing story {story_id}", flush=True)
    await story_repo.add_view(story_id, user_id)
    return {"success": True}


@router.post("/api/stories/{story_id}/like")
@router.post("/stories/{story_id}/like")
async def like_story(
    story_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db)
):
    auth_header = request.headers.get("Authorization")
    token = None
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
    if not token:
        token = request.cookies.get("device_user_ids")
        
    payload = verify_access_token(token) if token else None
    if not payload:
        raise HTTPException(status_code=401, detail="Unauthorized")

    story_repo = StoryRepository(session)
    is_liked = await story_repo.toggle_like(story_id, payload.get("id"))
    return {"success": True, "is_liked": is_liked}
