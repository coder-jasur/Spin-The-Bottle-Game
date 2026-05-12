"""
O'yin konstantalari — barcha item turlari, narxlar, holat kodlari.
"""
from __future__ import annotations

from datetime import datetime

# ── Boosterlar ────────────────────────────────────────────────────────────
BOOSTER_TYPES = {
    "kiss_fire",       # O'pishda olov animatsiyasi
    "refuse_slap",     # Rad etishda shapalak
    "league_kiss2x",   # League ballar 2x
    "league_kiss_lim10",  # Limit +10
    "league5",         # Liga bonusi
}

# ── Butilkalar ────────────────────────────────────────────────────────────
BOTTLE_TYPES = ['baby', 'champagnebot', 'cognac', 'cola', 'jackdaniels', 'ketchup', 'lemonade', 'martinibot', 'milkbot', 'nybottle', 'pirate', 'ship', 'skeleton', 'skewer', 'sprite', 'standart', 'vipbottle', 'vodkabot', 'yacht']

# Yangi stol — doim Coca-Cola butilka (asset nomi: cola)
DEFAULT_BOTTLE_TYPE = "cola"

BOTTLE_PRICES = {
    "standart": 0, "cola": 5, "baby": 5, "milkbot": 5, "ketchup": 5, "lemonade": 5,
    "champagnebot": 15, "vodkabot": 15, "martinibot": 15,
    "jackdaniels": 25, "nybottle": 25,
    "ship": 30, "skeleton": 40, "pirate": 40,
    "vipbottle": 50, "yacht": 50,
}

# ── Sovg'alar ─────────────────────────────────────────────────────────────
# Bepul (barcha uchun)
GIFT_TYPES_FREE = [
    "air_kiss", "strawberry", "tomato", "flower", "milk", "cola",
    "gem", "teddybear", "icecream", "champagne", "wine", "cognac",
    "vodka", "vanilla", "rosep", "roser", "rosew", "candle",
    "squirrel", "tarantula", "trout",
]
# VIP uchun ochiladi
GIFT_TYPES_VIP = [
    "crown1", "bosscap", "tequilashot", "weddingring1", "sexonthebeach",
    "romanticcandle", "valentine", "birthdaycake", "christmasball",
]
GIFT_TYPES = list(set(GIFT_TYPES_FREE + GIFT_TYPES_VIP + ['air_kiss', 'air_kiss_premium', 'astronaut', 'bear1', 'bear4', 'bear6', 'bear7', 'bikinibottom', 'bikinitop', 'blackrose', 'bomb', 'boxingglove', 'bra', 'bretzel', 'brokenheart', 'candy', 'clover', 'comet', 'dollar', 'donut', 'easteregg', 'egg', 'flower', 'flower_premium', 'football', 'frog', 'frog_premium', 'gem', 'gem_premium', 'ghost', 'gingy', 'goldstar', 'handcuffs', 'heartangel', 'heartdevil', 'holigrenade', 'iloveyou', 'kick', 'leaf', 'leatherbra', 'leathergag', 'leninbadge', 'loveis', 'maracas', 'medicalmask', 'meteorit', 'notes', 'olympmedal', 'orange', 'pancake', 'pants', 'pepper', 'pillow', 'piratespot', 'polaroid', 'prankcake', 'pumpkin', 'random', 'rosep', 'roser', 'rosew', 'salute', 'seafish', 'seashell', 'seastar', 'sheriffstar', 'smile', 'snowball', 'sponge', 'strawberry', 'superbowlball', 'sweet', 'sweet_premium', 'swimmers', 'theatremask', 'tomato', 'trout', 'valenok', 'valentine', 'vipcock', 'voodoo', 'whip', 'whistle']))

GIFT_PRICES = {
    "air_kiss": 1, "strawberry": 2, "tomato": 3, "flower": 5,
    "milk": 5, "cola": 5, "gem": 10, "teddybear": 10, "icecream": 10,
    "champagne": 15, "wine": 15, "rosep": 10, "roser": 10, "rosew": 10,
    "cognac": 20, "vodka": 20, "vanilla": 25, "candle": 15,
    "squirrel": 30, "tarantula": 30, "trout": 20,
    # VIP
    "crown1": 50, "bosscap": 40, "tequilashot": 30, "weddingring1": 100,
    "sexonthebeach": 50, "romanticcandle": 35, "valentine": 45,
    "birthdaycake": 60, "christmasball": 55,
}

# ── Ichimliklar ───────────────────────────────────────────────────────────
DRINK_TYPES = ['3september', 'abcbook', 'aircraft', 'alarmclock', 'amurchik', 'ananas', 'angelgift', 'apple', 'armytea', 'b52', 'babycocktail', 'baloon', 'banana', 'bbq', 'bed', 'beer', 'berries', 'birds', 'birthdaycake', 'blackring', 'bloodymary', 'bluelagoon', 'bouquet', 'bouquet2', 'brainicecream', 'bugatti', 'cactus', 'cake', 'cakeforher', 'cakeforhim', 'camera', 'candle', 'candybox', 'candyheartcup', 'canteen', 'castetmug', 'caviar', 'champagne', 'chemistry', 'cherry', 'cherrypie', 'chertovka', 'chess', 'chinese', 'chocolate', 'christmasball', 'clutch', 'cocoscocktail', 'coffee', 'cognacglass', 'colamentos', 'compass', 'cooler', 'corgidog', 'cuckooclock', 'cufflinks', 'daiquiri', 'diamond', 'diamondgift', 'dinosaur', 'djmixer1', 'djmixer2', 'djmixer3', 'djmixer4', 'doshirak', 'dove', 'drinkam', 'drinkaz', 'drinkby', 'drinkge', 'drinkkg', 'drinkkz', 'drinkru', 'drinkua', 'drinkus', 'drinkuz', 'drumkit1', 'drumkit2', 'drumkit3', 'drumkit4', 'dynamite', 'eastercake', 'easterrabbit', 'eggplant', 'eggsbasket', 'eggskiss', 'eiffeltower', 'elephant', 'extinguisher', 'faberge', 'fan', 'fern', 'fire', 'firehorse', 'flagam', 'flagaz', 'flagby', 'flagca', 'flagde', 'flages', 'flagge', 'flaghr', 'flagkg', 'flagkz', 'flagru', 'flagtr', 'flagua', 'flagus', 'flaguz', 'flamingo', 'flowerspink', 'flowersviolet', 'flowerswhitered', 'flowersyellow', 'footballcup', 'friedpotatoes', 'fruitbasket1', 'fruitbasket2', 'fruitbasket3', 'fruitbasket4', 'funnyanimal', 'furhandcuffs', 'genielamp', 'glassshoe', 'glintwein', 'globe', 'goldenapple', 'goldenmic', 'goldpot', 'gramophone', 'grandpiano', 'grog', 'groundhog', 'guitar', 'gwatch', 'hamburger', 'hammer', 'harp', 'healthdrink', 'heart1', 'heart2', 'heart3', 'heartlock', 'heineken', 'helicopter', 'hockeycup', 'honeybarrel', 'hookah', 'hotdog', 'icecream', 'iwatch', 'jam', 'japanesefan', 'japaneseroll', 'japaneseset', 'japanesesushi', 'joystick', 'juice', 'kinder', 'latte', 'leathershoes', 'liberty', 'lifestyle', 'lighter', 'lionet', 'lipstick', 'londonphone', 'louboutin', 'love', 'lovepotion', 'magichat', 'makarov', 'mandarin', 'manekineko', 'manygifts1', 'manygifts2', 'manygifts3', 'manygifts4', 'maroccantea', 'martini', 'matreshka', 'meldonium', 'menorah', 'microphone', 'milk', 'milkcoctail', 'mineralka', 'mobile', 'mojito', 'mosque', 'motorcycle', 'mushroomsbasket', 'nipple', 'nytree1', 'nytree10', 'nytree11', 'nytree2', 'nytree3', 'nytree4', 'nytree5', 'nytree6', 'nytree7', 'nytree8', 'nytree9', 'oilbarrel', 'orangutan', 'orchid', 'oscar', 'owl', 'palette', 'pancakes', 'panda', 'peach', 'pearl', 'perfume', 'petcat', 'petdog', 'picnic', 'pig', 'pinacolada', 'pinguin', 'pipe', 'piratecanon', 'pirateflag', 'pirateparrot', 'pirateswords', 'pisatower', 'plunger', 'pocketclock', 'poison', 'pomegranate', 'poop', 'popcorn', 'pumpkinpie', 'rassol', 'reindeer', 'retrocar', 'revolver', 'ringblack', 'ringflower', 'ringiy', 'ringwhite', 'rocket', 'rolexblack', 'rolexwhite', 'romanticcandle', 'romcola', 'rose', 'roseinglass', 'rubyearrings', 'rubyheart', 'rubyrose', 'rubyshoes', 'rzdtea', 'sakura1', 'sakura2', 'sakura3', 'sakura4', 'samovar', 'seeds', 'sexonthebeach', 'shashlik', 'sigara', 'skateboard', 'skull', 'smilecup', 'smoothie', 'snakepot', 'snowdrops', 'snowman1', 'snowman2', 'snowman3', 'snowman4', 'snowman5', 'snowman6', 'snowman7', 'snowman8', 'sofa', 'spacecocktail', 'sphinx', 'springcocktail', 'squirrel', 'starbucks', 'submarine', 'suitcase', 'sunflower', 'surprizebox', 'tank', 'tarantula', 'tarhun', 'tea', 'teapot', 'tearssnake', 'teddybear', 'tequilashot', 'thermos', 'toiletpaper1', 'toiletpaper2', 'toiletpaper3', 'toiletpaper4', 'toiletpaper5', 'toiletpaper6', 'touristmug', 'trashcan1', 'trashcan2', 'trashcan3', 'trashcan4', 'trumpet', 'tsarscup', 'tub', 'turkey', 'turkishcoffee', 'ufo', 'unicorn', 'universal', 'vanilla', 'venus', 'vipchampagne', 'vipgold', 'viphorseshoe', 'vipkalash', 'viplegbox', 'vipring', 'viproses', 'vodka', 'watchman', 'watchwoman', 'watermelon', 'weddingring1', 'weddingring2', 'weddingring3', 'weddingring4', 'weight', 'whiskey', 'wine']

DRINK_PRICES = {k: GIFT_PRICES.get(k, 10) for k in DRINK_TYPES}

# ── Shapkalar ─────────────────────────────────────────────────────────────
HAT_TYPES_FREE = ["clownhat", "winterhat", "alarmclock"]
HAT_TYPES_VIP  = ["bosscap", "budenovka", "tankhelmet", "witch", "wreath", "zodiac"]
HAT_TYPES = list(set(HAT_TYPES_FREE + HAT_TYPES_VIP + ['astronomy', 'babecap', 'baranki', 'baseball1', 'baseball2', 'baseball3', 'baseball4', 'baseball5', 'baseball6', 'baseballam', 'baseballaz', 'baseballde', 'baseballtr', 'baseballua', 'baseballus', 'batmanhat', 'bayanist', 'bezkoz', 'blackcrown', 'blackoverlordcap', 'bosscap', 'bow1', 'bow2', 'bow3', 'bow4', 'bowidle', 'boybyecap', 'budenovka', 'builderhat', 'cappy', 'carnivalmask1', 'carnivalmask2', 'christmasbell', 'christmassock', 'classycap', 'clown', 'clownhat', 'colander', 'cook', 'cosmohelmet', 'cowboy', 'crown1', 'crown2', 'crown3', 'crown4', 'cylinder', 'cylinderw', 'darthvader', 'deerhorns', 'djcap', 'ears1', 'ears2', 'ears3', 'ears4', 'ears5', 'ears6', 'ears7', 'ears8', 'ears9a', 'ears9b', 'ears9c', 'elvis', 'fireman', 'flowerwreath', 'goodenuffcap', 'goodvibescap', 'hatservice', 'hatvdv', 'helmet', 'hockey1', 'hockey2', 'hockey3', 'hockey4', 'horseheadhat', 'icecrown', 'indianhat', 'kingcap', 'knight', 'kokoshnik', 'kotelok', 'leathercathat', 'leathermousehat', 'leatherzipmask', 'ledglasses', 'lenincap', 'lepricon', 'mardigrasmask', 'migalka', 'minerhat', 'monomachhat', 'mrcoolcap', 'musketeer', 'napoleon', 'noteshatb', 'noteshatw', 'nurse', 'ololosh', 'olympichat', 'olympwreath', 'omgcap', 'painterhat', 'paperhat', 'partycap', 'partyhat', 'pharaoh', 'phdhat', 'pilotka', 'pirat', 'queencap', 'rabbitcap', 'rasta', 'redwig', 'royalslug', 'rubyglasses', 'safari', 'santa', 'savagecap', 'shitcap', 'sleephat', 'slug', 'snegurka', 'sombrero', 'spaghetti', 'sparta', 'springwreath', 'strawhat', 'sturmtruppen', 'sultan', 'superbowlcap', 'supermecap', 'tankhelmet', 'taxi', 'tyrolhat', 'unclesam', 'ushanka', 'viking', 'vipcrown', 'winterhat', 'witch', 'wreath', 'wreathorange', 'wreathroses', 'wreathviolets']))

HAT_PRICES = {k: (20 if k in HAT_TYPES_FREE else 35) for k in HAT_TYPES}

# ── Imo-ishoralar (tokenlar bilan) ────────────────────────────────────────
GESTURE_TYPES = ['agree', 'airkissges', 'angelges', 'anger', 'applause', 'artistges', 'bandit', 'barmanges', 'bayanistges', 'beback', 'beerges', 'binocularsges', 'bla', 'bouqetges', 'bragging', 'braggingvip', 'bunchofmoney', 'bye', 'camouflageges', 'cannotspeak', 'chocolateges', 'contempt', 'cool', 'crackerges', 'crazy', 'cry', 'dance', 'defenderges', 'devil', 'dikaprio', 'disagree', 'disco', 'dj', 'dragonges', 'driving', 'drunk', 'elvisges', 'explode', 'facepalm', 'famousges', 'fearful', 'fk', 'gentleman', 'gratitude', 'grinning', 'grunt', 'guitaristges', 'handround', 'happy', 'heartface', 'heartges', 'hello', 'jediges', 'jewelerges', 'jugglerges', 'kiss', 'laugh', 'little', 'lookthroughges', 'loveges', 'michael', 'mimistges', 'monkey', 'muerteges', 'music', 'nohearts', 'nurseges', 'ok', 'orangutanges', 'parrot', 'party', 'rap', 'rich', 'rock', 'sad', 'scream', 'selfie', 'shy', 'sleepges', 'smoking', 'snowballges', 'sparklerges', 'star', 'stereoglassesges', 'strictges', 'sun', 'thumbdown', 'thumbup', 'tongue', 'vomiting', 'wall', 'watermelonges', 'wink', 'yawn']

GESTURE_PRICES = {k: 5 for k in GESTURE_TYPES}

# ── Ramkalar va toshlar ───────────────────────────────────────────────────
FRAME_TYPES_FREE = ["silver", "gold"]
FRAME_TYPES_VIP  = [
    "diamond", "lovedecor", "demon", "emerald", "ruby",
    "sapphire", "topaz", "pearls", "platinum", "icedecor", "lavadecor",
]
FRAME_TYPES = list(set(FRAME_TYPES_FREE + FRAME_TYPES_VIP + ['amber', 'amethyst', 'angel', 'carameldecor', 'cardsdecor', 'chipsdecor', 'cinemadecor', 'daydecor', 'demon', 'diamond', 'discodecor', 'eastdecor', 'egyptdecor', 'emerald', 'fruitjellydecor', 'gold', 'greendecor', 'hatreddecor', 'icedecor', 'lavadecor', 'lovedecor', 'marsdecor', 'naturedecor', 'nightdecor', 'pearls', 'platinum', 'rockdecor', 'romedecor', 'ruby', 'sapphire', 'silver', 'stonedecor', 'theatredecor', 'topaz', 'venusdecor', 'violetdecor', 'westdecor']))

STONE_TYPES_FREE = []
STONE_TYPES_VIP  = ["diamond", "ruby", "emerald", "sapphire", "topaz"]
STONE_TYPES = ['amber', 'amethyst', 'angel', 'bronze', 'carameldecor', 'cardsdecor', 'chipsdecor', 'cinemadecor', 'daydecor', 'demon', 'diamond', 'discodecor', 'eastdecor', 'egyptdecor', 'emerald', 'fruitjellydecor', 'gold', 'greendecor', 'hatreddecor', 'icedecor', 'iron', 'lavadecor', 'lovedecor', 'marble', 'marsdecor', 'naturedecor', 'nightdecor', 'pearls', 'platinum', 'rockdecor', 'romedecor', 'ruby', 'sapphire', 'silver', 'steel', 'stonedecor', 'theatredecor', 'topaz', 'venusdecor', 'violetdecor', 'westdecor']
# ── O'yin holatlari ───────────────────────────────────────────────────────
STATE_WAIT     = "wait"
STATE_SPINNING = "spinning"
STATE_OFFER    = "wait_offer"
STATE_SELECT   = "wait_select"

# ── Stol ──────────────────────────────────────────────────────────────────
MAX_SEATS = 12

# ── League ────────────────────────────────────────────────────────────────
LEAGUE_STATES = ["welcome", "running", "finished"]
LEAGUE_KISS_LIMIT = 500

# ── Kickout (stoldan haydash) narxlari ────────────────────────────────────
# Oxirgi kickdan 30 daqiqa o'tsa zanjir uziladi (keyingi kick yana bepul dan boshlanadi).
KICKOUT_STREAK_RESET_SECONDS = 30 * 60

# uses = joriy zanjirda allaqachon muvaffaqiyatli boshlangan kicklar soni (0 → keyingi kick bepul)
KICKOUT_PRICE_LADDER: tuple[int, ...] = (0, 15, 30, 50, 80, 120, 170, 240)
# Ladder dan keyin har qadamda oldingi narxga ko'paytiruv (million/milliardgacha o'sishi mumkin)
KICKOUT_PRICE_GROWTH_MULT = 1.62
KICKOUT_PRICE_MAX = 10**15


def kickout_streak_effective_uses(
    streak_count: int,
    last_at: datetime | None,
    now: datetime,
) -> int:
    """DB dagi zanjir: oxirgi kick vaqti 30 d dan eski bo'lsa yoki hech kick bo'lmasa → 0."""
    if last_at is None:
        return 0
    elapsed = (now - last_at).total_seconds()
    if elapsed > KICKOUT_STREAK_RESET_SECONDS:
        return 0
    return max(0, int(streak_count or 0))


def kickout_price_for_use_index(uses_completed: int) -> int:
    """Keyingi kick narxi: 0 bepul, keyin ladder, keyin eksponensial (chegara KICKOUT_PRICE_MAX)."""
    if uses_completed < 0:
        uses_completed = 0
    ladder = KICKOUT_PRICE_LADDER
    if uses_completed < len(ladder):
        return ladder[uses_completed]
    price = float(ladder[-1])
    extra_steps = uses_completed - (len(ladder) - 1)
    for _ in range(extra_steps):
        price *= KICKOUT_PRICE_GROWTH_MULT
    p = int(price)
    return min(max(p, ladder[-1]), KICKOUT_PRICE_MAX)

# ── Default items (user yaratilganda beriladi) ────────────────────────────
DEFAULT_USER_ITEMS = {
    "kiss_fire": 0,
    "refuse_slap": 0,
    "league_kiss2x": 0,
    "league_kiss_lim10": 0,
    "league5": 0,
}

# Admin o'yinda: RAM minimal balans (server tekshiruvlari uchun)
ADMIN_DISPLAY_HEARTS = 999_999_999
ADMIN_DISPLAY_STARS = 999_999_999

# ── Bonus miqdorlari ──────────────────────────────────────────────────────
WELCOME_BONUS_HEARTS    = 10
DAILY_BONUS_HEARTS      = {1: 5, 2: 7, 3: 10, 4: 12, 5: 15, 6: 20, 7: 30}  # streak → hearts
KISS_BONUS_HEARTS       = 2    # har 5 ta kissda
RETENTION_BONUS_HEARTS  = 15
REWARDED_VIDEO_HEARTS   = 10
REFERRAL_BONUS_HEARTS   = 20   # har bir taklif uchun
ACHIEVEMENT_MILESTONES  = {    # N ta taklif → bonus
    1: 20, 5: 50, 10: 100, 25: 200, 50: 350, 100: 500
}

# ── Xona diapazoni ────────────────────────────────────────────────────────
ROOM_RANGES = {
    "UZBEKISTAN": (1001, 1150),
    "KAZAKHSTAN": (2501, 2650),
    "RUSSIA":     (4001, 4150),
    "USA":        (7001, 7150),
    "AMERICA":    (7001, 7150),
    "TURKEY":     (10001, 10150),
    "TURKISTAN":  (10001, 10150),
    "AZERBAIJAN": (11501, 11650),
    "TAJIKISTAN": (14501, 14650),
    "ALL":        (5501, 5520),   # 20 ta global (room_policy bilan mos)
    "VIP":        (9001, 9150),   # 150 VIP slot
}
COUNTRY_ROOMS_MIN = 5   # Har davlat uchun minimal stol soni
GLOBAL_ROOMS_MIN  = 10  # "ALL" uchun minimal stol soni

# ── Achievement kalitlari ─────────────────────────────────────────────────
ACHIEVEMENT_KEYS = {
    "first_kiss":       {"name": "Birinchi o'pish",    "threshold": 1,    "category": "kisses",  "reward_hearts": 10},
    "kiss_10":          {"name": "10 ta o'pish",        "threshold": 10,   "category": "kisses",  "reward_hearts": 20},
    "kiss_100":         {"name": "100 ta o'pish",       "threshold": 100,  "category": "kisses",  "reward_hearts": 50},
    "kiss_500":         {"name": "O'pish ustasi",       "threshold": 500,  "category": "kisses",  "reward_hearts": 150},
    "first_gift":       {"name": "Birinchi sovg'a",     "threshold": 1,    "category": "expense", "reward_hearts": 10},
    "gift_100":         {"name": "Saxiy sovg'achi",     "threshold": 100,  "category": "expense", "reward_hearts": 40},
    "dj_first":         {"name": "Birinchi musiqa",     "threshold": 5,    "category": "dj",      "reward_hearts": 15},
    "dj_100":           {"name": "DJ yulduzi",          "threshold": 100,  "category": "dj",      "reward_hearts": 50},
    "referral_1":       {"name": "Do'st taklif etdi",   "threshold": 1,    "category": "referral","reward_hearts": 20},
    "referral_10":      {"name": "Eng yaxshi taklif",   "threshold": 10,   "category": "referral","reward_hearts": 100},
    "vip_member":       {"name": "VIP a'zo",            "threshold": 1,    "category": "vip",     "reward_stars": 10},
    "daily_7":          {"name": "Haftalik streak",     "threshold": 7,    "category": "streak",  "reward_hearts": 50},
}

HAT_PRICES.update({
    "crown1": 50, "crown2": 50, "crown3": 50, "crown4": 50, "vipcrown": 100,
    "bosscap": 30, "witch": 25, "knight": 40, "darthvader": 35,
})


# ─── Retention/Bonus narxlari ─────────────────────────────────────────────────
WELCOME_BONUS_GOLD     = 10
DAILY_BONUS_GOLD       = 5
KISS_BONUS_GOLD        = 2
RETENTION_BONUS_GOLD   = 15
REWARDED_VIDEO_GOLD    = 10
# --- Iqtisodiy model (klient: F2 paketlari, hs VIP narxlari) -------------------
# VIP: dlg week/month — USD matnda 200 / 650 STARS (index-*.js `hs`)
VIP_PLAN_STARS = {"week": 200, "month": 650}
VIP_PLAN_DAYS = {"week": 7, "month": 30}
VIP_BONUS_STARS = 50
VIP_PRICE_STARS = VIP_PLAN_STARS["month"]  # eski importlar uchun default

# Hearts paketlari: kalit = klient `gm_hearts_purchase` dagi STARS (`F2.USD[].real`)
HEARTS_PACKAGES = {
    250: 500,
    500: 2200,
    1350: 6000,
    2500: 12500,
    10: 10,
    2000: 2200,
    5000: 6000,
    10000: 12500,
}

# ─── Bank komplimentlari (klient: compliment_next / compliment_send) ───────
COMPLIMENTS_TO_REWARD = 3
COMPLIMENT_GOLD_REWARD = 50
