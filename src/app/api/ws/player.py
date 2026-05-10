"""
Player — bir foydalanuvchining to'liq holati.
DB dan yuklangan haqiqiy ma'lumotlar bilan ishlaydi.
"""
from __future__ import annotations

from typing import Optional, Dict, Any, TYPE_CHECKING

from src.app.api.ws.constants import DEFAULT_USER_ITEMS, FRAME_TYPES, STONE_TYPES, HAT_TYPES, GIFT_TYPES, DRINK_TYPES, BOTTLE_TYPES, GESTURE_TYPES

if TYPE_CHECKING:
    from src.app.database.models.user   import User
    from src.app.database.models.wallet import Wallet


class Player:
    def __init__(
        self,
        ws,
        user_id: str,
        username: str,
        photo_url: str = "/photos/no_img.png",
        male: bool = True,
        seat: int = 0,
    ):
        self.ws           = ws
        self.id           = user_id          # string (WS protokoli uchun)
        self.db_id: Optional[int] = None     # int (DB uchun)
        self.username     = username
        self.photo_url    = photo_url
        self.male         = male
        self.seat         = seat
        self.table_id: Optional[str] = None
        self.session_token: Optional[str] = None

        # ── Moliya (DB dan yuklanadi) ──────────────────────────────────
        self.hearts       = 500   # game ichida "gold" sifatida ishlatiladi
        self.stars        = 50    # tokenlar
        self.hearts_real  = 0
        self.gift_tokens  = 0
        self.daily_streak = 0
        self.can_claim_bonus = False

        # ── O'yin statistikasi ────────────────────────────────────────
        self.kisses       = 0
        self.total_kisses = 0
        self.league_score = 0
        self.dj_score     = 0

        # ── Profil ────────────────────────────────────────────────────
        self.vip          = False
        self.verified     = True
        self.country      = "UZBEKISTAN"
        self.locale       = "en_UZ"
        self.language     = "ru"
        self.age          = 0
        self.level        = 1
        self.xp           = 0
        self.gender       = "male"
        self.is_new       = 0
        self.status       = ""
        self.joined_at    = 0

        # ── Dekoratsiyalar ────────────────────────────────────────────
        self.stone        = ""
        self.frame        = ""
        self.hat          = ""
        self.hat_random   = ""
        self.drink        = ""
        self.drink_count  = 0
        self.drink_random = ""
        self.ava_gift     = ""
        self.ava_gift_random = ""

        # ── O'yin holati ──────────────────────────────────────────────
        self.boosters: list = []
        self.items: dict    = dict(DEFAULT_USER_ITEMS)
        self.harem_owner_id: int = 0
        self.harem_price: int    = 1
        self.friends_privacy: str = "everyone"

    # ════════════════════════════════════════════════════════════════════════
    # Factory
    # ════════════════════════════════════════════════════════════════════════
    @classmethod
    def from_db(cls, ws, db_user: "User", seat: int = 0) -> "Player":
        """DB User modeli asosida Player yaratadi."""
        wallet: "Wallet | None" = getattr(db_user, "wallet", None)

        p = cls(
            ws=ws,
            user_id=str(db_user.id),
            username=db_user.display_name or db_user.username or f"user_{db_user.id}",
            photo_url=db_user.avatar_url or "/photos/no_img.png",
            male=(db_user.gender != "female"),
            seat=seat,
        )
        p.db_id    = db_user.id
        p.level    = db_user.level   or 1
        p.xp       = db_user.xp      or 0
        p.age      = db_user.age     or 0
        p.country  = db_user.country or "UZBEKISTAN"
        p.locale   = db_user.language_code or "en_UZ"
        p.language = db_user.language_code or "ru"
        p.gender   = db_user.gender  or "male"
        p.status   = db_user.status_text or ""
        p.vip      = (db_user.vip_status is True) or (getattr(db_user, "vip_status", "") == "active")
        
        p.is_admin = getattr(db_user, "role", "") in ["admin", "superadmin"]
        
        # Admin uchun barcha sovg'alar va narsalarni ochish
        if p.is_admin:
            p.vip = True
            for frame in FRAME_TYPES: p.items[frame] = 1
            for stone in STONE_TYPES: p.items[stone] = 1
            for hat in HAT_TYPES: p.items[hat] = 1
            for gift in GIFT_TYPES: p.items[gift] = 1
            for drink in DRINK_TYPES: p.items[drink] = 1
            for bottle in BOTTLE_TYPES: p.items[bottle] = 1
            for gesture in GESTURE_TYPES: p.items[gesture] = 1
        p.verified = getattr(db_user, "is_verified", False)

        if wallet:
            p.hearts      = wallet.hearts or 0
            p.stars       = wallet.stars  or 0
            p.hearts_real = wallet.hearts or 0
            p.gift_tokens = getattr(wallet, "gift_tokens", 0) or 0

        p.daily_streak = db_user.daily_streak or 0
        # can_claim_bonus ni aniqlash
        if not db_user.last_bonus_claimed_at:
            p.can_claim_bonus = True
        else:
            from datetime import datetime
            p.can_claim_bonus = datetime.now().date() > db_user.last_bonus_claimed_at.date()

        try:
            p.joined_at = int(db_user.created_at.timestamp() * 1000)
        except Exception:
            p.joined_at = 0

        # Uxajivat ma'lumotlari (DB dan)
        p.harem_owner_id = getattr(db_user, "harem_owner_id", 0) or 0
        p.harem_price    = getattr(db_user, "harem_price", 1) or 1
        p.friends_privacy = getattr(db_user, "friends_privacy", None) or "everyone"

        return p

    # ════════════════════════════════════════════════════════════════════════
    # Serialization (Strict Model Mapped)
    # ════════════════════════════════════════════════════════════════════════
    def to_short(self) -> Dict[str, Any]:
        """Chat/event xabarlarida ishlatiladi (Rasmdagi model + Legacy)."""
        return {
            "id":          self.id,
            "userId":      self.id,
            "name":        self.username,
            "username":    self.username,
            "male":        self.male,
            "photo_url":   self.photo_url,
            "image":       self.photo_url,
            "locale":      self.locale,
            "userProfile": {
                "name":    self.username,
                "image":   self.photo_url,
                "gender":  self.gender,
                "level":   self.level,
            },
            "gender":      self.gender,
            "seat":        self.seat,
            "premium":     self.vip,
            "harem_owner_id": self.harem_owner_id,
            "harem_price":    self.harem_price,
            "harem_owner":    None, # Manager tomonidan to'ldiriladi
        }

    def to_participant(self) -> Dict[str, Any]:
        """game_enter participants ro'yxati (Strict Model + Legacy)."""
        return {
            "id":          self.id,
            "userId":      self.id,
            "name":        self.username,
            "username":    self.username,
            "male":        self.male,
            "photo_url":   self.photo_url,
            "image":       self.photo_url,
            "locale":      self.locale,
            "pass_premium": 0,
            "vip":         self.vip,
            "age":         self.age,
            "city":        "",
            "country":     self.country,
            "is_new":      self.is_new,
            "top":         False,
            "verified":    self.verified,
            "frame":       self.frame,
            "stone":       self.stone,
            "drink":       self.drink,
            "hat":         self.hat,
            "ava_gift":    self.ava_gift,
            "bottle_pass": False,
            "userProfile": {
                "name":    self.username,
                "image":   self.photo_url,
                "gender":  self.gender,
                "level":   self.level,
            },
            "level":       self.level,
            "gold":        self.hearts,
            "goldReal":    self.hearts_real,
            "premium":     self.vip,
            "rating":      self.league_score,
            "rank":        1,
            "score":       self.total_kisses,
            "total_kisses":self.total_kisses,
            "dj_score":    self.dj_score,
            "gestures":    0,
            "price":       self.harem_price,
            "harem_price": self.harem_price,
            "harem_owner_id": self.harem_owner_id,
            "harem_owner": None, # Manager tomonidan to'ldiriladi
            "gender":      self.gender,
            "seat":        self.seat,
            "kisses":      self.kisses,
        }

    def to_login_payload(self, table_id: str, ts: int) -> Dict[str, Any]:
        """Server → Client: login paketi (Strict Model + Legacy)."""
        fv = "all" if self.friends_privacy == "everyone" else self.friends_privacy
        return {
            "type":               "login",
            "ok":                 True,
            "id":                 self.id,
            "userId":             self.id,
            "name":               self.username,
            "username":           self.username,
            "male":               self.male,
            "photo_url":          self.photo_url,
            "image":              self.photo_url,
            "locale":             self.locale,
            "vip":                self.vip,
            "age":                self.age,
            "city":               "",
            "country":            self.country,
            "is_new":             self.is_new,
            "top":                False,
            "verified":           self.verified,
            "frame":              self.frame,
            "stone":              self.stone,
            "userProfile": {
                "name":           self.username,
                "image":          self.photo_url,
                "gender":         self.gender,
                "level":          self.level,
            },
            "level":              self.level,
            "xp":                 self.xp,
            "gold":               self.hearts,
            "goldReal":           self.hearts_real,
            "gold_real":          self.hearts_real,
            "goldPremium":        0,
            "tokens":             self.stars,
            "tokensVipTs":        0,
            "tokens_vip_ms":      0,
            "tokens_vip":         0,
            "hearts":             self.hearts,
            "premium":            self.vip,
            "rating":             self.league_score,
            "rank":               1,
            "score":              self.total_kisses,
            "total_kisses":       self.total_kisses,
            "gestures":           0,
            "price":              0,
            "gender":             self.gender,
            "gift_tokens":        self.gift_tokens,
            "daily_login_streak": self.daily_streak,
            "daily_bonus_available": 1 if self.can_claim_bonus else 0,
            "timestamp":          ts,
            "language":           self.language,
            "room_id":            table_id,
            "tableId":            table_id,
            "packet":             21,
            "ts":                 ts,
            "pass_state":         "running",
            "pass_premium":       0,
            "welcome_bonus_upto": 0,
            "rr_available":       0,
            "dj_score_rank":      1,
            "dj_score":           self.dj_score,
            "social":             "web",
            "harem_price":        self.harem_price,
            "harem_owner_id":     self.harem_owner_id,
            "login_mobile_once":  0,
            "login_pc_once":      1,
            "travel_count":       1,
            "message":            "Login muvaffaqiyatli",
            "assign": {
                "gold":         self.hearts,
                "kisses":       self.kisses,
                "league_score": self.league_score,
            },
            # Qf / _recv_login kutilgan snake_case va ixtiyoriy maydonlar
            "created_at":         self.joined_at or ts,
            "prev_login":         0,
            "referrals":          0,
            "achievements":       [],
            "is_admin":           getattr(self, "is_admin", False),
            "abtest":             {},
            "ip_country":         "UZ",
            "compliments_available": 0,
            "clients":            [],
            "purchase_bonus_upto": 0,
            "league_kiss2x_ms":   0,
            "gifts":              [],
            "scheduled":          [],
            "achievements_ms":    0,
            "ih_flags":           0,
            "friends_visibility": fv,
            "block_user_ids":     [],
            "friend_user_ids":    [],
            "inbox":              [],
            "kickout_info":       {"price": 10, "refresh_ms": ts + 60_000},
            "rewarded_video_ms":  0,
            "league_state":       "",
            "league":             0,
            "max_league":         16,
            "profile_update_ms":  0,
        }