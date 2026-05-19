"""
Player — bir foydalanuvchining to'liq holati.
DB dan yuklangan haqiqiy ma'lumotlar bilan ishlaydi.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional, TYPE_CHECKING

from src.app.api.ws.constants import (
    ADMIN_DISPLAY_HEARTS,
    ADMIN_DISPLAY_STARS,
    DEFAULT_USER_ITEMS,
    FRAME_TYPES,
    STONE_TYPES,
    HAT_TYPES,
    GIFT_TYPES,
    DRINK_TYPES,
    BOTTLE_TYPES,
    GESTURE_TYPES,
    FRAME_TYPES_FREE,
    STONE_TYPES_FREE,
    FRAME_TYPES_VIP,
    STONE_TYPES_VIP,
    HAT_TYPES_FREE,
    HAT_TYPES_VIP,
    GIFT_TYPES_FREE,
    GIFT_TYPES_VIP,
    GIFT_LOVE_ITEM_ID,
    GIFT_LOVE_UNLIMITED_MIN,
    KICKOUT_STREAK_RESET_SECONDS,
    league_state_for_total_kisses,
    league_tier_from_total_kisses,
    kickout_price_for_use_index,
    kickout_streak_effective_uses,
)

if TYPE_CHECKING:
    from src.app.database.models.user   import User
    from src.app.database.models.wallet import Wallet


def achievement_level_to_client(level: int) -> int:
    """Ichki/DB 1-based daraja → klient 0-based (assets.json image[] indeksi)."""
    n = int(level or 0)
    return max(0, n - 1) if n > 0 else 0


def parse_birth_date_ms(raw: object) -> int:
    """DB/Text tug'ilgan kun → UTC tushlik (ms). Klient zodiak uchun `birthday_ts` sifatida ishlatadi."""
    from datetime import datetime, timezone

    if raw is None:
        return 0
    s = str(raw).strip()
    if not s:
        return 0

    dt: datetime | None = None
    head10 = s[:10] if len(s) >= 10 else s
    for cand in (head10, s.split()[0], s):
        if len(cand) < 8:
            continue
        for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%d.%m.%Y", "%d/%m/%Y"):
            if len(cand) < 10:
                continue
            try:
                dt = datetime.strptime(cand[:10], fmt)
                break
            except ValueError:
                continue
        if dt:
            break
    if dt is None:
        try:
            iso = s.replace("Z", "").split(".")[0]
            if "T" in iso:
                iso = iso.split("T")[0]
            dt = datetime.strptime(iso[:10], "%Y-%m-%d")
        except ValueError:
            return 0

    noon = datetime(dt.year, dt.month, dt.day, 12, 0, 0, tzinfo=timezone.utc)
    return int(noon.timestamp() * 1000)


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
        # Klient (O3) faqat string id ni qabul qiladi
        self.id           = str(user_id)
        self.db_id: Optional[int] = None     # int (DB uchun)
        self.username     = username
        self.photo_url    = photo_url
        self.male         = male
        self.seat         = seat
        self.table_id: Optional[str] = None
        self.session_token: Optional[str] = None
        # plain_ws: server `handle` da yangilanadi; 10 daqiqa harakatsizlik tekshiruvi
        self.last_activity_ms: int = 0

        # ── Moliya (DB dan yuklanadi) ──────────────────────────────────
        self.hearts       = 0   # game ichida "gold" sifatida ishlatiladi
        self.stars        = 0   # tokenlar
        self.hearts_real  = 0
        self.gift_tokens  = 0
        self.stars_coin   = 0
        self.tg_id: Optional[int] = None
        self.daily_streak = 0
        self.can_claim_bonus = False

        # ── O'yin statistikasi ────────────────────────────────────────
        # kisses — faqat joriy stoldagi ko'rsatkich (o'yin boshida 0); total_kisses — DB umumiy
        self.kisses       = 0
        self.total_kisses = 0
        self.league_score = 0
        self.dj_score     = 0
        self.expense      = 0
        self.emotion      = 0
        self.importance   = 0
        self.birthday_ts  = 0

        # ── Profil ────────────────────────────────────────────────────
        self.vip          = False
        self.verified     = True
        self.country      = "UZ"
        from src.app.core.language import DEFAULT_LANG, to_game_locale

        self.locale       = to_game_locale(DEFAULT_LANG)
        self.language     = DEFAULT_LANG
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
        self.harem_courts_received: int = 0
        self.compliments_sent: int = 0
        # Bank komplimentlari uchun sikl (0..COMPLIMENTS_TO_REWARD); yutuq — umumiy
        self.compliments_lifetime: int = 0
        # Butilka aylanishlari (sessiyadan tashqari saqlanadi — UserStats `bottle_spin`)
        self.total_spins: int = 0
        self.friends_privacy: str = "everyone"
        # Referral: kimlar ro'yxatdan `referred_by_id` bilan o'tgani (users.invited_guests)
        self.invited_guests: int = 0
        # Brauzer klienti har xabarda ketma-ket `packet` kutadi (Session.trackedRecv).
        self.out_packet_seq: Optional[int] = None

        # Kickout zanjiri DB da (db_id bor); mehmon uchun faqat sessiya ichida
        self.kickout_streak_count: int = 0
        self.kickout_last_at: datetime | None = None
        self.guest_kickout_streak: int = 0
        self.guest_kickout_last_ms: int = 0
        # welcome/main.be3d9225.js: ochiq JSON matn, login query orqali ham bo‘lishi mumkin.
        self.plain_ws: bool = False
        self.in_queue: bool = False
        self.session_started: bool = False
        # Yutiq (achievement) holati: {achievement_id: level (1-based)}.
        # `to_login_payload` orqali klientga `achievements` ro'yxati uzatiladi.
        self.achievements: Dict[str, int] = {}
        # Mukofot qaysi darajagacha olingan (qayta claim oldini olish).
        self.achievements_bonus_claimed: Dict[str, int] = {}
        self.is_admin: bool = False

    @staticmethod
    def _viewer_snapshot_for_login(player: "Player") -> dict:
        """Login `viewer.viewer` — g_love hisoblagichi DB bilan bir xil bo‘lishi uchun."""
        love_n = int(player.items.get(GIFT_LOVE_ITEM_ID, 0) or 0)
        inner: dict = {"ipCountry": "UZ"}
        if love_n > 0:
            inner["items"] = {GIFT_LOVE_ITEM_ID: love_n}
        return {"viewer": inner}

    def grant_default_owned_items(self) -> None:
        """Klient `items[id] >= 1` bo‘lsa dekor/sovga/stil «ochilgan» deb qabul qiladi.
        Mehmon ham ramka tanlash oynasida bepul ramkalarni ko‘ra olsin; VIP uchun
        VIP to‘plam qo‘shiladi."""
        # Bepul to‘plam
        for k in FRAME_TYPES_FREE:
            self.items[k] = max(int(self.items.get(k, 0) or 0), 1)
        for k in STONE_TYPES_FREE:
            self.items[k] = max(int(self.items.get(k, 0) or 0), 1)
        # STONE_TYPES_FREE bo‘sh — kumush/oltin ramka bilan keladigan toshlar
        for k in ("silver", "gold", "bronze"):
            if k in STONE_TYPES:
                self.items[k] = max(int(self.items.get(k, 0) or 0), 1)

        for k in HAT_TYPES_FREE:
            self.items[k] = max(int(self.items.get(k, 0) or 0), 1)
        for k in GIFT_TYPES_FREE:
            self.items[k] = max(int(self.items.get(k, 0) or 0), 1)
        # Standart butilka (narx 0 — barchaga)
        self.items["standart"] = max(int(self.items.get("standart", 0) or 0), 1)

        if getattr(self, "vip", False):
            for k in FRAME_TYPES_VIP:
                self.items[k] = max(int(self.items.get(k, 0) or 0), 1)
            for k in STONE_TYPES_VIP:
                self.items[k] = max(int(self.items.get(k, 0) or 0), 1)
            for k in HAT_TYPES_VIP:
                self.items[k] = max(int(self.items.get(k, 0) or 0), 1)
            for k in GIFT_TYPES_VIP:
                self.items[k] = max(int(self.items.get(k, 0) or 0), 1)

    def apply_admin_privileges(self) -> None:
        """Admins + asosiy superadmin: barcha sovg'a/dekor, VIP, sessiyada cheksiz gold/token."""
        self.is_admin = True
        self.vip = True
        for frame in FRAME_TYPES:
            self.items[frame] = max(int(self.items.get(frame, 0) or 0), 1)
        for stone in STONE_TYPES:
            self.items[stone] = max(int(self.items.get(stone, 0) or 0), 1)
        for hat in HAT_TYPES:
            self.items[hat] = max(int(self.items.get(hat, 0) or 0), 1)
        for gift in GIFT_TYPES:
            if gift == GIFT_LOVE_ITEM_ID:
                continue
            self.items[gift] = max(int(self.items.get(gift, 0) or 0), 1)
        self.items[GIFT_LOVE_ITEM_ID] = GIFT_LOVE_UNLIMITED_MIN
        for drink in DRINK_TYPES:
            self.items[drink] = max(int(self.items.get(drink, 0) or 0), 1)
        for bottle in BOTTLE_TYPES:
            self.items[bottle] = max(int(self.items.get(bottle, 0) or 0), 1)
        for gesture in GESTURE_TYPES:
            self.items[gesture] = max(int(self.items.get(gesture, 0) or 0), 1)
        for k in DEFAULT_USER_ITEMS:
            self.items[k] = max(int(self.items.get(k, 0) or 0), 99)
        self.hearts = max(int(self.hearts or 0), ADMIN_DISPLAY_HEARTS)
        self.hearts_real = self.hearts
        self.gift_tokens = max(int(getattr(self, "gift_tokens", 0) or 0), ADMIN_DISPLAY_STARS)
        self.stars_coin = max(int(getattr(self, "stars_coin", 0) or 0), ADMIN_DISPLAY_STARS)
        self.sync_token_display()

    def spendable_tokens(self) -> int:
        """Sarflash uchun faqat stars_coin (gift_tokens alohida, yechilmaydi)."""
        return int(getattr(self, "stars_coin", 0) or 0)

    def sync_token_display(self) -> None:
        """Klient `tokens` — faqat stars_coin."""
        self.stars = self.spendable_tokens()

    def apply_wallet_balances(
        self, *, hearts: int, stars_coin: int, gift_tokens: int
    ) -> None:
        self.hearts = int(hearts or 0)
        self.hearts_real = self.hearts
        self.stars_coin = int(stars_coin or 0)
        self.gift_tokens = int(gift_tokens or 0)
        self.sync_token_display()

    def wallet_for_client(self) -> Dict[str, Any]:
        """Klient JSON: gold/tokens int."""
        gt = int(getattr(self, "gift_tokens", 0) or 0)
        sc = int(getattr(self, "stars_coin", 0) or 0)
        return {
            "gold": int(self.hearts or 0),
            "goldReal": int(self.hearts_real or 0),
            "tokens": sc,
            "gift_tokens": gt,
            "stars_coin": sc,
        }

    # ════════════════════════════════════════════════════════════════════════
    # Factory
    # ════════════════════════════════════════════════════════════════════════
    @staticmethod
    def _table_display_name(db_user: "User") -> str:
        from src.app.api.auth.user_payload import game_display_name

        return game_display_name(db_user)

    @classmethod
    def from_db(cls, ws, db_user: "User", seat: int = 0) -> "Player":
        """DB User modeli asosida Player yaratadi."""
        from src.app.services.telegram_profile import NO_IMG, public_avatar_url

        wallet: "Wallet | None" = getattr(db_user, "wallet", None)

        p = cls(
            ws=ws,
            user_id=str(db_user.id),
            username=cls._table_display_name(db_user),
            photo_url=public_avatar_url(db_user.avatar_url) or NO_IMG,
            male=(db_user.gender != "female"),
            seat=seat,
        )
        p.db_id    = db_user.id
        p.tg_id    = getattr(db_user, "tg_id", None)
        from src.app.api.auth.user_payload import telegram_username_label

        p._db_login = db_user.login
        from src.app.api.auth.user_payload import _stored_telegram_handle

        p._db_username = _stored_telegram_handle(db_user) or db_user.username
        p._settings_username = f"user_{db_user.id}"
        p._game_login_name = telegram_username_label(db_user)
        p.level    = db_user.level   or 1
        p.xp       = db_user.xp      or 0
        from src.app.api.ws.profile_setup import effective_profile_age

        p.age = effective_profile_age(db_user)
        p.country  = db_user.country or "UZ"
        from src.app.core.language import normalize_lang, to_game_locale

        p.language = normalize_lang(db_user.language_code)
        p.locale = to_game_locale(p.language)
        p.gender   = db_user.gender  or "male"
        from src.app.api.ws.profile_setup import user_needs_profile_setup

        p.is_new = 1 if user_needs_profile_setup(db_user) else 0
        p.status   = db_user.status_text or ""
        _exp = getattr(db_user, "vip_expires_at", None)
        _vip_flag = (db_user.vip_status is True) or (
            getattr(db_user, "vip_status", "") == "active"
        )
        if _exp is not None and _exp < datetime.now():
            _vip_flag = False
        p.vip = _vip_flag
        
        p.is_admin = False

        p.verified = getattr(db_user, "is_verified", False)

        p.total_kisses = int(getattr(db_user, "kisses", 0) or 0)
        p.kisses = 0
        p.dj_score = int(getattr(db_user, "dj", 0) or 0)
        p.expense = int(getattr(db_user, "expense", 0) or 0)
        p.emotion = int(getattr(db_user, "emotion", 0) or 0)
        p.importance = int(getattr(db_user, "importance", 0) or 0)

        p.birthday_ts = parse_birth_date_ms(getattr(db_user, "birth_date", None))

        if wallet:
            p.hearts      = wallet.hearts or 0
            p.hearts_real = wallet.hearts or 0
            p.apply_wallet_balances(
                hearts=int(wallet.hearts or 0),
                stars_coin=int(getattr(wallet, "stars_coin", 0) or 0),
                gift_tokens=int(getattr(wallet, "gift_tokens", 0) or 0),
            )

        p.daily_streak = db_user.daily_streak or 0
        # can_claim_bonus ni aniqlash
        if not db_user.last_bonus_claimed_at:
            p.can_claim_bonus = True
        else:
            p.can_claim_bonus = datetime.now().date() > db_user.last_bonus_claimed_at.date()

        try:
            p.joined_at = int(db_user.created_at.timestamp() * 1000)
        except Exception:
            p.joined_at = 0

        # Uxajivat ma'lumotlari (DB dan)
        p.harem_owner_id = getattr(db_user, "harem_owner_id", 0) or 0
        p.harem_price    = getattr(db_user, "harem_price", 1) or 1
        p.harem_courts_received = int(
            getattr(db_user, "harem_courts_received", 0) or 0
        )
        p.friends_privacy = getattr(db_user, "friends_privacy", None) or "everyone"
        p.invited_guests = int(getattr(db_user, "invited_guests", 0) or 0)
        p.kickout_streak_count = int(getattr(db_user, "kickout_streak_count", 0) or 0)
        p.kickout_last_at = getattr(db_user, "kickout_last_at", None)

        # Yutiqlar (UserAchievement relationship) — DB dan lazy yuklanmasa
        # bo'sh dict bo'lib qoladi. GameManager keyinchalik refresh qiladi.
        try:
            ach_rel = getattr(db_user, "achievements", None) or []
            for ua in ach_rel:
                key = getattr(getattr(ua, "achievement", None), "key", None)
                if key:
                    p.achievements[key] = int(getattr(ua, "level", 0) or 0)
        except Exception:
            pass

        p.grant_default_owned_items()

        love_stock = int(getattr(db_user, "gift_love_stock", 0) or 0)
        if love_stock > 0:
            p.items[GIFT_LOVE_ITEM_ID] = love_stock
        else:
            p.items.pop(GIFT_LOVE_ITEM_ID, None)

        return p

    def stamp_out_packet(self, msg: dict) -> None:
        if self.out_packet_seq is None:
            self.out_packet_seq = 21
        msg["packet"] = self.out_packet_seq
        self.out_packet_seq += 1

    # ════════════════════════════════════════════════════════════════════════
    # Serialization (Strict Model Mapped)
    # ════════════════════════════════════════════════════════════════════════
    def to_short(self) -> Dict[str, Any]:
        """Chat/event xabarlarida ishlatiladi (Rasmdagi model + Legacy)."""
        sid = str(self.id)
        return {
            "id":          sid,
            "uid":         sid,
            "userId":      sid,
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
            "kisses":      self.kisses,
            "total_kisses": self.total_kisses,
            "level":       self.level,
            "harem_owner_id": self.harem_owner_id,
            "harem_price":    self.harem_price,
            "harem_owner":    None, # Manager tomonidan to'ldiriladi
        }

    def to_participant(self) -> Dict[str, Any]:
        """game_enter participants ro'yxati (Strict Model + Legacy)."""
        sid = str(self.id)
        wf = self.wallet_for_client()
        part = {
            "id":          sid,
            "uid":         sid,
            "userId":      sid,
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
            "birthday_ts": getattr(self, "birthday_ts", 0) or 0,
            "status":      self.status or "",
            "frame":       self.frame,
            "stone":       self.stone,
            "drink":       self.drink,
            "drink_count": int(self.drink_count or 0),
            "drink_random": int(self.drink_random or 0),
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
            "gold":        wf["gold"],
            "goldReal":    wf["goldReal"],
            "premium":     self.vip,
            "rating":      self.league_score,
            "rank":        1,
            "score":       self.total_kisses,
            "total_kisses":self.total_kisses,
            # Ba'zi UI qismlarida unlock shartlari `*_count` nomlari bilan ishlaydi
            "total_kiss_count": self.total_kisses,
            "total_music_count": self.dj_score,
            "total_smile_count": int(getattr(self, "compliments_lifetime", 0) or 0),
            "dj_score":    self.dj_score,
            "gestures":    self.emotion,
            "price":       self.expense,
            "harem_price": self.harem_price,
            "league":      league_tier_from_total_kisses(self.total_kisses),
            "harem_owner_id": self.harem_owner_id,
            "harem_owner": None, # Manager tomonidan to'ldiriladi
            "gender":      self.gender,
            "seat":        self.seat,
            "kisses":      self.kisses,
        }
        return part

    def kickout_effective_uses_sync(self) -> int:
        """Kick narxi uchun zanjir indeksi (login vaqtida RAM ko'zgusi; DB foydalanuvchida to'liq refresh GameManager da)."""
        now = datetime.now()
        if self.db_id:
            return kickout_streak_effective_uses(
                self.kickout_streak_count, self.kickout_last_at, now
            )
        last_ms = int(self.guest_kickout_last_ms or 0)
        streak = int(self.guest_kickout_streak or 0)
        ts_ms = int(now.timestamp() * 1000)
        if last_ms and (ts_ms - last_ms) > KICKOUT_STREAK_RESET_SECONDS * 1000:
            return 0
        return streak

    def to_login_payload(self, table_id: str, ts: int) -> Dict[str, Any]:
        """Server → Client: login paketi (Strict Model + Legacy)."""
        fv = "all" if self.friends_privacy == "everyone" else self.friends_privacy
        sid = str(self.id)
        wf = self.wallet_for_client()
        game_login = getattr(self, "_settings_username", None) or (
            f"user_{self.db_id}" if self.db_id else self.username
        )
        tg_label = str(getattr(self, "_game_login_name", None) or "").lstrip("@")
        pl = {
            "type":               "login",
            "ok":                 True,
            "id":                 sid,
            "userId":             sid,
            "name":               self.username,
            "username":           tg_label or game_login,
            "game_username":      game_login,
            "login":              game_login,
            "telegram_username": getattr(self, "_db_username", None) or "",
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
            "birthday_ts":        getattr(self, "birthday_ts", 0) or 0,
            "status":             self.status or "",
            "frame":              self.frame,
            "stone":              self.stone,
            "userProfile": {
                "name":           self.username,
                "image":          self.photo_url,
                "gender":         self.gender,
                "level":          self.level,
                "birthday_ts":    getattr(self, "birthday_ts", 0) or 0,
            },
            "level":              self.level,
            "xp":                 self.xp,
            "gold":               wf["gold"],
            "goldReal":           wf["goldReal"],
            "gold_real":          wf["goldReal"],
            "goldPremium":        0,
            "tokens":             wf["tokens"],
            "tokensVipTs":        0,
            "tokens_vip_ms":      0,
            "tokens_vip":         0,
            "hearts":             wf["gold"],
            "premium":            self.vip,
            "rating":             self.league_score,
            "rank":               1,
            "score":              self.total_kisses,
            "total_kisses":       self.total_kisses,
            # Unlock/shop uchun mos nomlar (web bundle eski joylarda shuni o'qiydi)
            "total_kiss_count":   self.total_kisses,
            "total_music_count":  self.dj_score,
            "total_smile_count":  int(getattr(self, "compliments_lifetime", 0) or 0),
            # Klient unlock / «Most emotional» va xarajat reytingi — DB users.emotion / expense
            "gestures":           int(getattr(self, "emotion", 0) or 0),
            "price":              int(getattr(self, "expense", 0) or 0),
            "gender":             self.gender,
            "gift_tokens":        wf["gift_tokens"],
            "daily_login_streak": self.daily_streak,
            "daily_bonus_available": 1 if self.can_claim_bonus else 0,
            "timestamp":          ts,
            "language":           self.language,
            "room_id":            table_id,
            "tableId":            table_id,
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
                "gold":         wf["gold"],
                "kisses":       self.total_kisses,
                "league_score": self.league_score,
            },
            # Qf / _recv_login kutilgan snake_case va ixtiyoriy maydonlar
            "created_at":         self.joined_at or ts,
            "prev_login":         0,
            "referrals":          int(getattr(self, "invited_guests", 0) or 0),
            "achievements":       [
                {
                    "achievement_id": k,
                    "level": achievement_level_to_client(v),
                    "timestamp": ts,
                }
                for k, v in (self.achievements or {}).items()
            ],
            "is_admin":           getattr(self, "is_admin", False),
            # Legacy klient: abTest.kickout false bo'lsa profilda kick/save UI umuman chiqmaydi
            "abtest":             {"kickout": True},
            "ip_country":         "UZ",
            "ipCountry":          "UZ",
            "viewer": Player._viewer_snapshot_for_login(self),
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
            "kickout_info":       {
                "price": kickout_price_for_use_index(self.kickout_effective_uses_sync()),
                "refresh_ms": 60_000,
            },
            "rewarded_video_ms":  0,
            "league_state":       league_state_for_total_kisses(self.total_kisses),
            "league":             league_tier_from_total_kisses(self.total_kisses),
            "max_league":         16,
            "profile_update_ms":  0,
            # Login: faqat DB dan yuklangan zaxira (RAM dagi eski 100k+ xato son emas)
            "gift_love_stock": max(
                0, int(self.items.get(GIFT_LOVE_ITEM_ID, 0) or 0)
            ),
        }
        return pl