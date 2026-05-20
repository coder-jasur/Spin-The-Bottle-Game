"""
O'yin konstantalari — barcha item turlari, narxlar, holat kodlari.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

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
GIFT_TYPES = ['air_kiss', 'air_kiss_premium', 'astronaut', 'bear1', 'bear4', 'bear6', 'bear7', 'bikinibottom', 'bikinitop', 'blackrose', 'bomb', 'boxingglove', 'bra', 'bretzel', 'brokenheart', 'candy', 'clover', 'comet', 'dollar', 'donut', 'easteregg', 'egg', 'flower', 'flower_premium', 'football', 'frog', 'frog_premium', 'g_love', 'gem', 'gem_premium', 'ghost', 'gingy', 'goldstar', 'handcuffs', 'heartangel', 'heartdevil', 'holigrenade', 'iloveyou', 'kick', 'leaf', 'leatherbra', 'leathercathat', 'leathergag', 'leathermousehat', 'leatherzipmask', 'leninbadge', 'loveis', 'maracas', 'medicalmask', 'meteorit', 'notes', 'olympmedal', 'orange', 'pancake', 'pants', 'pepper', 'pillow', 'piratespot', 'polaroid', 'prankcake', 'pumpkin', 'random', 'rosep', 'roser', 'rosew', 'salute', 'seafish', 'seashell', 'seastar', 'sheriffstar', 'smile', 'snowball', 'sponge', 'strawberry', 'superbowlball', 'sweet', 'sweet_premium', 'swimmers', 'theatremask', 'tomato', 'trout', 'valenok', 'valentine', 'vipcock', 'voodoo', 'whip', 'whistle']

# «Коктейль Любви» — inventar (`player.items` ↔ DB `gift_love_stock`).
# Aynan 999 = cheksiz (kamaymaydi). Boshqa sonlar: ekranda ko'rinadi, har yuborishda −1.
GIFT_LOVE_ITEM_ID = "g_love"
GIFT_LOVE_UNLIMITED_MIN = 999

# Dynamite (ichimlik) — qurbon faqat chat (SMS) va musiqa qo'ya olmaydi; chat yozuvlari o'chadi.
# Butilka aylantirish, sovg'a va boshqa o'yin harakatlari ruxsat.
DYNAMITE_DRINK_TYPE = "dynamite"
BOMB_GIFT_TYPES: frozenset[str] = frozenset({"bomb", "holigrenade"})

GIFT_PRICES = {'air_kiss': 1, 'air_kiss_premium': 1, 'astronaut': 1, 'bear1': 1, 'bear4': 1, 'bear6': 1, 'bear7': 1, 'bikinibottom': 2, 'bikinitop': 2, 'blackrose': 1, 'bomb': 1, 'boxingglove': 1, 'bra': 3, 'bretzel': 1, 'brokenheart': 1, 'candy': 1, 'clover': 1, 'comet': 1, 'dollar': 1, 'donut': 1, 'easteregg': 1, 'egg': 1, 'flower': 1, 'flower_premium': 1, 'football': 1, 'frog': 2, 'frog_premium': 2, 'gem': 1, 'gem_premium': 1, 'ghost': 1, 'gingy': 1, 'goldstar': 1, 'handcuffs': 1, 'heartangel': 1, 'heartdevil': 1, 'holigrenade': 1, 'iloveyou': 1, 'kick': 2, 'leaf': 1, 'leatherbra': 5, 'leathergag': 5, 'leathermousehat': 5, 'leatherzipmask': 5, 'leninbadge': 1, 'loveis': 1, 'maracas': 1, 'medicalmask': 1, 'meteorit': 1, 'notes': 1, 'olympmedal': 1, 'orange': 1, 'pancake': 1, 'pants': 3, 'pepper': 1, 'pillow': 1, 'piratespot': 1, 'polaroid': 1, 'prankcake': 3, 'pumpkin': 1, 'random': 1, 'rosep': 1, 'roser': 1, 'rosew': 1, 'salute': 1, 'seafish': 1, 'seashell': 1, 'seastar': 1, 'sheriffstar': 1, 'smile': 1, 'snowball': 1, 'sponge': 1, 'strawberry': 1, 'superbowlball': 1, 'sweet': 1, 'sweet_premium': 1, 'swimmers': 2, 'theatremask': 1, 'tomato': 1, 'trout': 1, 'valenok': 1, 'valentine': 1, 'vipcock': 2, 'voodoo': 1, 'whip': 2, 'whistle': 1, 'g_love': 2}

# ── Ichimliklar ───────────────────────────────────────────────────────────
DRINK_TYPES = ['3september', 'abcbook', 'aircraft', 'alarmclock', 'amurchik', 'ananas', 'angelgift', 'apple', 'armytea', 'b52', 'babycocktail', 'baloon', 'banana', 'bbq', 'bed', 'beer', 'berries', 'birds', 'birthdaycake', 'blackring', 'bloodymary', 'bluelagoon', 'bouquet', 'bouquet2', 'brainicecream', 'bugatti', 'cactus', 'cake', 'cakeforher', 'cakeforhim', 'camera', 'candle', 'candybox', 'candyheartcup', 'canteen', 'castetmug', 'caviar', 'champagne', 'chemistry', 'cherry', 'cherrypie', 'chertovka', 'chess', 'chinese', 'chocolate', 'christmasball', 'clutch', 'cocoscocktail', 'coffee', 'cognacglass', 'colamentos', 'compass', 'cooler', 'corgidog', 'cuckooclock', 'cufflinks', 'daiquiri', 'diamond', 'diamondgift', 'dinosaur', 'djmixer1', 'djmixer2', 'djmixer3', 'djmixer4', 'doshirak', 'dove', 'drinkam', 'drinkaz', 'drinkby', 'drinkge', 'drinkkg', 'drinkkz', 'drinkru', 'drinkua', 'drinkus', 'drinkuz', 'drumkit1', 'drumkit2', 'drumkit3', 'drumkit4', 'dynamite', 'eastercake', 'easterrabbit', 'eggplant', 'eggsbasket', 'eggskiss', 'eiffeltower', 'elephant', 'extinguisher', 'faberge', 'fan', 'fern', 'fire', 'firehorse', 'flagam', 'flagaz', 'flagby', 'flagca', 'flagde', 'flages', 'flagge', 'flaghr', 'flagkg', 'flagkz', 'flagru', 'flagtr', 'flagua', 'flagus', 'flaguz', 'flamingo', 'flowerspink', 'flowersviolet', 'flowerswhitered', 'flowersyellow', 'footballcup', 'friedpotatoes', 'fruitbasket1', 'fruitbasket2', 'fruitbasket3', 'fruitbasket4', 'funnyanimal', 'furhandcuffs', 'genielamp', 'glassshoe', 'glintwein', 'globe', 'goldenapple', 'goldenmic', 'goldpot', 'gramophone', 'grandpiano', 'grog', 'groundhog', 'guitar', 'gwatch', 'hamburger', 'hammer', 'harp', 'healthdrink', 'heart1', 'heart2', 'heart3', 'heartlock', 'heineken', 'helicopter', 'hockeycup', 'honeybarrel', 'hookah', 'hotdog', 'icecream', 'iwatch', 'jam', 'japanesefan', 'japaneseroll', 'japaneseset', 'japanesesushi', 'joystick', 'juice', 'kinder', 'latte', 'leathershoes', 'liberty', 'lifestyle', 'lighter', 'lionet', 'lipstick', 'londonphone', 'louboutin', 'love', 'lovepotion', 'magichat', 'makarov', 'mandarin', 'manekineko', 'manygifts1', 'manygifts2', 'manygifts3', 'manygifts4', 'maroccantea', 'martini', 'matreshka', 'meldonium', 'menorah', 'microphone', 'milk', 'milkcoctail', 'mineralka', 'mobile', 'mojito', 'mosque', 'motorcycle', 'mushroomsbasket', 'nipple', 'nytree1', 'nytree10', 'nytree11', 'nytree2', 'nytree3', 'nytree4', 'nytree5', 'nytree6', 'nytree7', 'nytree8', 'nytree9', 'oilbarrel', 'orangutan', 'orchid', 'oscar', 'owl', 'palette', 'pancakes', 'panda', 'peach', 'pearl', 'perfume', 'petcat', 'petdog', 'picnic', 'pig', 'pinacolada', 'pinguin', 'pipe', 'piratecanon', 'pirateflag', 'pirateparrot', 'piratespot', 'pirateswords', 'pisatower', 'plunger', 'pocketclock', 'poison', 'pomegranate', 'poop', 'popcorn', 'pumpkinpie', 'rassol', 'reindeer', 'retrocar', 'revolver', 'ringblack', 'ringflower', 'ringiy', 'ringwhite', 'rocket', 'rolexblack', 'rolexwhite', 'romanticcandle', 'romcola', 'rose', 'roseinglass', 'rubyearrings', 'rubyheart', 'rubyrose', 'rubyshoes', 'rzdtea', 'sakura1', 'sakura2', 'sakura3', 'sakura4', 'salute', 'samovar', 'seeds', 'sexonthebeach', 'shashlik', 'sigara', 'skateboard', 'skull', 'smilecup', 'smoothie', 'snakepot', 'snowdrops', 'snowman1', 'snowman2', 'snowman3', 'snowman4', 'snowman5', 'snowman6', 'snowman7', 'snowman8', 'sofa', 'spacecocktail', 'sphinx', 'springcocktail', 'squirrel', 'starbucks', 'submarine', 'suitcase', 'sunflower', 'surprizebox', 'tank', 'tarantula', 'tarhun', 'tea', 'teapot', 'tearssnake', 'teddybear', 'tequilashot', 'thermos', 'toiletpaper1', 'toiletpaper2', 'toiletpaper3', 'toiletpaper4', 'toiletpaper5', 'toiletpaper6', 'touristmug', 'trashcan1', 'trashcan2', 'trashcan3', 'trashcan4', 'trout', 'trumpet', 'tsarscup', 'tub', 'turkey', 'turkishcoffee', 'ufo', 'unicorn', 'universal', 'v_film', 'v_tiktok', 'vanilla', 'venus', 'vipchampagne', 'vipgold', 'viphorseshoe', 'vipkalash', 'viplegbox', 'vipring', 'viproses', 'vodka', 'watchman', 'watchwoman', 'watermelon', 'weddingring1', 'weddingring2', 'weddingring3', 'weddingring4', 'weight', 'whiskey', 'wine']

DRINK_PRICES = {'3september': 1, 'abcbook': 1, 'aircraft': 1, 'alarmclock': 1, 'amurchik': 2, 'ananas': 1, 'angelgift': 1, 'apple': 1, 'armytea': 1, 'b52': 2, 'babycocktail': 2, 'baloon': 1, 'banana': 1, 'bbq': 1, 'bed': 3, 'beer': 1, 'berries': 1, 'birds': 1, 'birthdaycake': 1, 'bloodymary': 1, 'bluelagoon': 1, 'bouquet': 1, 'bouquet2': 1, 'brainicecream': 1, 'bugatti': 3, 'cactus': 1, 'cake': 1, 'cakeforher': 1, 'cakeforhim': 1, 'camera': 1, 'candle': 1, 'candybox': 1, 'candyheartcup': 1, 'canteen': 1, 'castetmug': 1, 'caviar': 1, 'champagne': 1, 'chemistry': 1, 'cherry': 1, 'cherrypie': 2, 'chertovka': 1, 'chess': 1, 'chinese': 1, 'chocolate': 1, 'christmasball': 1, 'clutch': 1, 'cocoscocktail': 1, 'coffee': 1, 'cognacglass': 1, 'colamentos': 2, 'compass': 1, 'cooler': 1, 'corgidog': 1, 'cuckooclock': 1, 'cufflinks': 1, 'daiquiri': 1, 'diamond': 1, 'diamondgift': 1, 'doshirak': 1, 'dove': 1, 'drinkam': 1, 'drinkaz': 1, 'drinkby': 1, 'drinkge': 1, 'drinkkg': 1, 'drinkkz': 1, 'drinkru': 1, 'drinkua': 1, 'drinkus': 1, 'drinkuz': 1, 'dynamite': 3, 'eastercake': 1, 'easterrabbit': 1, 'eggplant': 1, 'eggsbasket': 1, 'eiffeltower': 1, 'elephant': 1, 'extinguisher': 1, 'faberge': 1, 'fan': 1, 'fern': 1, 'fire': 1, 'firehorse': 3, 'flagam': 1, 'flagaz': 1, 'flagby': 1, 'flagca': 1, 'flagde': 1, 'flages': 1, 'flagge': 1, 'flaghr': 1, 'flagkg': 1, 'flagkz': 1, 'flagru': 1, 'flagtr': 1, 'flagua': 1, 'flagus': 1, 'flaguz': 1, 'flamingo': 1, 'flowerspink': 1, 'flowersviolet': 1, 'flowerswhitered': 1, 'flowersyellow': 1, 'footballcup': 1, 'friedpotatoes': 1, 'fruitbasket1': 2, 'fruitbasket2': 2, 'fruitbasket3': 2, 'fruitbasket4': 2, 'funnyanimal': 1, 'genielamp': 1, 'glassshoe': 3, 'glintwein': 1, 'globe': 1, 'goldenapple': 3, 'goldenmic': 3, 'goldpot': 1, 'grog': 1, 'groundhog': 1, 'guitar': 1, 'gwatch': 1, 'hamburger': 1, 'healthdrink': 1, 'heineken': 1, 'helicopter': 2, 'hockeycup': 1, 'honeybarrel': 1, 'hookah': 1, 'hotdog': 1, 'icecream': 1, 'iwatch': 1, 'jam': 1, 'japanesefan': 1, 'japaneseroll': 1, 'japaneseset': 1, 'japanesesushi': 1, 'joystick': 1, 'juice': 1, 'kinder': 1, 'latte': 1, 'leathershoes': 5, 'liberty': 1, 'lifestyle': 1, 'lighter': 1, 'lipstick': 1, 'londonphone': 1, 'love': 2, 'lovepotion': 1, 'makarov': 1, 'mandarin': 1, 'manekineko': 1, 'manygifts1': 2, 'manygifts2': 2, 'manygifts3': 2, 'manygifts4': 2, 'maroccantea': 1, 'martini': 1, 'matreshka': 1, 'medicalmask': 1, 'meldonium': 1, 'menorah': 1, 'microphone': 1, 'milk': 1, 'milkcoctail': 1, 'mineralka': 1, 'mobile': 2, 'mojito': 1, 'mosque': 1, 'motorcycle': 1, 'mushroomsbasket': 1, 'nipple': 1, 'nytree1': 1, 'nytree10': 1, 'nytree11': 1, 'nytree2': 1, 'nytree3': 1, 'nytree4': 1, 'nytree5': 1, 'nytree6': 1, 'nytree7': 1, 'nytree8': 1, 'nytree9': 1, 'oilbarrel': 1, 'orangutan': 1, 'orchid': 1, 'oscar': 1, 'owl': 1, 'palette': 1, 'pancakes': 1, 'panda': 1, 'peach': 1, 'pearl': 1, 'perfume': 1, 'picnic': 1, 'pig': 1, 'pinacolada': 1, 'pinguin': 1, 'pipe': 1, 'piratecanon': 1, 'pirateflag': 1, 'pirateparrot': 1, 'pirateswords': 1, 'pisatower': 1, 'pocketclock': 1, 'poison': 1, 'pomegranate': 1, 'popcorn': 1, 'pumpkinpie': 1, 'rassol': 1, 'reindeer': 1, 'roseinglass': 1, 'rzdtea': 1, 'sakura1': 1, 'sakura2': 1, 'sakura3': 1, 'sakura4': 1, 'salute': 1, 'samovar': 1, 'seeds': 1, 'shashlik': 1, 'sigara': 1, 'skateboard': 1, 'smilecup': 1, 'smoothie': 1, 'snakepot': 3, 'snowdrops': 1, 'snowman1': 1, 'snowman2': 1, 'snowman3': 1, 'snowman4': 1, 'snowman5': 1, 'snowman6': 1, 'snowman7': 1, 'snowman8': 1, 'sofa': 1, 'spacecocktail': 1, 'sphinx': 1, 'springcocktail': 1, 'squirrel': 1, 'starbucks': 1, 'submarine': 1, 'sunflower': 2, 'surprizebox': 1, 'tank': 1, 'tarhun': 1, 'tea': 1, 'teapot': 1, 'tearssnake': 1, 'teddybear': 1, 'tequilashot': 1, 'thermos': 1, 'toiletpaper1': 1, 'toiletpaper2': 1, 'toiletpaper3': 1, 'toiletpaper4': 1, 'toiletpaper5': 1, 'toiletpaper6': 1, 'tomato': 1, 'touristmug': 1, 'trashcan1': 2, 'trashcan2': 2, 'trashcan3': 2, 'trashcan4': 2, 'trout': 1, 'tsarscup': 3, 'turkey': 1, 'turkishcoffee': 1, 'ufo': 1, 'unicorn': 1, 'universal': 1, 'v_film': 9, 'v_tiktok': 5, 'vanilla': 1, 'venus': 1, 'vipchampagne': 1, 'vipgold': 3, 'viphorseshoe': 1, 'vipkalash': 1, 'viplegbox': 1, 'vipring': 1, 'viproses': 1, 'vodka': 3, 'watchman': 1, 'watchwoman': 1, 'watermelon': 1, 'weddingring1': 1, 'weddingring2': 1, 'weddingring3': 1, 'weddingring4': 1, 'weight': 1, 'whiskey': 1, 'wine': 1}

# Faqat shu ichimlik yuborilganda qabul qiluvchiga +1 gold (boshqa ichimliklarga emas).
DRINK_IDS_RECEIVER_HEART_PLUS_1: frozenset[str] = frozenset({"cocoscocktail"})

# ── Shapkalar ─────────────────────────────────────────────────────────────
HAT_TYPES_FREE = ["clownhat", "winterhat", "alarmclock"]
HAT_TYPES_VIP  = ["bosscap", "budenovka", "tankhelmet", "witch", "wreath", "zodiac"]
HAT_TYPES = ['astronomy', 'babecap', 'baranki', 'baseball1', 'baseball2', 'baseball3', 'baseball4', 'baseball5', 'baseball6', 'baseballam', 'baseballaz', 'baseballde', 'baseballtr', 'baseballua', 'baseballus', 'batmanhat', 'bayanist', 'bezkoz', 'blackcrown', 'blackoverlordcap', 'bosscap', 'bow1', 'bow2', 'bow3', 'bow4', 'bowidle', 'boybyecap', 'budenovka', 'builderhat', 'cappy', 'carnivalmask1', 'carnivalmask2', 'christmasbell', 'christmassock', 'classycap', 'clown', 'clownhat', 'colander', 'cook', 'cosmohelmet', 'cowboy', 'crown1', 'crown2', 'crown3', 'crown4', 'cylinder', 'cylinderw', 'darthvader', 'deerhorns', 'djcap', 'ears1', 'ears2', 'ears3', 'ears4', 'ears5', 'ears6', 'ears7', 'ears8', 'ears9a', 'ears9b', 'ears9c', 'elvis', 'fireman', 'flowerwreath', 'goodenuffcap', 'goodvibescap', 'hatservice', 'hatvdv', 'helmet', 'hockey1', 'hockey2', 'hockey3', 'hockey4', 'horseheadhat', 'icecrown', 'indianhat', 'kingcap', 'knight', 'kokoshnik', 'kotelok', 'leathercathat', 'leathermousehat', 'leatherzipmask', 'ledglasses', 'lenincap', 'lepricon', 'mardigrasmask', 'migalka', 'minerhat', 'monomachhat', 'mrcoolcap', 'musketeer', 'napoleon', 'noteshatb', 'noteshatw', 'nurse', 'ololosh', 'olympichat', 'olympwreath', 'omgcap', 'painterhat', 'paperhat', 'partycap', 'partyhat', 'pharaoh', 'phdhat', 'pilotka', 'pirat', 'queencap', 'rabbitcap', 'rasta', 'redwig', 'royalslug', 'rubyglasses', 'safari', 'santa', 'savagecap', 'shitcap', 'sleephat', 'slug', 'snegurka', 'sombrero', 'spaghetti', 'sparta', 'springwreath', 'strawhat', 'sturmtruppen', 'sultan', 'superbowlcap', 'supermecap', 'tankhelmet', 'taxi', 'tyrolhat', 'unclesam', 'ushanka', 'viking', 'vipcrown', 'winterhat', 'witch', 'wreath', 'wreathorange', 'wreathroses', 'wreathviolets']

HAT_PRICES = {'astronomy': 2, 'babecap': 2, 'baranki': 2, 'baseball1': 2, 'baseball2': 2, 'baseball3': 2, 'baseball4': 2, 'baseball5': 2, 'baseball6': 2, 'baseballam': 2, 'baseballaz': 2, 'baseballde': 2, 'baseballtr': 2, 'baseballua': 2, 'baseballus': 2, 'batmanhat': 2, 'bayanist': 2, 'bezkoz': 2, 'blackcrown': 3, 'blackoverlordcap': 2, 'bosscap': 2, 'bow1': 2, 'bow2': 2, 'bow3': 2, 'bow4': 2, 'bowidle': 3, 'boybyecap': 2, 'budenovka': 2, 'builderhat': 2, 'cappy': 2, 'carnivalmask1': 2, 'carnivalmask2': 2, 'christmasbell': 2, 'christmassock': 2, 'classycap': 2, 'clown': 2, 'clownhat': 2, 'colander': 2, 'cook': 2, 'cosmohelmet': 2, 'cowboy': 2, 'crown1': 3, 'crown2': 3, 'crown3': 3, 'crown4': 3, 'cylinder': 2, 'cylinderw': 2, 'darthvader': 2, 'deerhorns': 2, 'djcap': 2, 'ears1': 2, 'ears2': 2, 'ears3': 2, 'ears4': 2, 'ears5': 2, 'ears6': 2, 'ears7': 2, 'ears8': 2, 'ears9a': 2, 'ears9b': 2, 'ears9c': 2, 'elvis': 2, 'fireman': 2, 'flowerwreath': 2, 'goodenuffcap': 2, 'goodvibescap': 2, 'hatservice': 2, 'hatvdv': 2, 'helmet': 2, 'hockey1': 2, 'hockey2': 2, 'hockey3': 2, 'hockey4': 2, 'horseheadhat': 2, 'icecrown': 2, 'indianhat': 2, 'kingcap': 2, 'knight': 2, 'kokoshnik': 2, 'kotelok': 2, 'leathercathat': 5, 'leathermousehat': 5, 'leatherzipmask': 5, 'ledglasses': 3, 'lenincap': 2, 'lepricon': 2, 'mardigrasmask': 2, 'migalka': 2, 'minerhat': 2, 'monomachhat': 3, 'mrcoolcap': 2, 'musketeer': 2, 'napoleon': 2, 'noteshatb': 2, 'noteshatw': 2, 'nurse': 2, 'ololosh': 2, 'olympichat': 2, 'olympwreath': 2, 'omgcap': 2, 'painterhat': 2, 'paperhat': 2, 'partycap': 2, 'partyhat': 2, 'phdhat': 2, 'pilotka': 2, 'pirat': 2, 'queencap': 2, 'rabbitcap': 2, 'rasta': 2, 'redwig': 2, 'royalslug': 2, 'safari': 2, 'santa': 2, 'savagecap': 2, 'shitcap': 2, 'sleephat': 2, 'slug': 2, 'snegurka': 2, 'sombrero': 2, 'spaghetti': 2, 'sparta': 2, 'springwreath': 2, 'strawhat': 2, 'sturmtruppen': 2, 'sultan': 2, 'superbowlcap': 2, 'supermecap': 2, 'tankhelmet': 2, 'taxi': 2, 'tyrolhat': 2, 'unclesam': 2, 'ushanka': 2, 'viking': 2, 'vipcrown': 3, 'winterhat': 2, 'witch': 2, 'wreath': 2, 'wreathorange': 2, 'wreathroses': 2, 'wreathviolets': 2}

# ── Imo-ishoralar (tokenlar bilan) ────────────────────────────────────────
GESTURE_TYPES = ['agree', 'airkissges', 'angelges', 'anger', 'applause', 'artistges', 'bandit', 'barmanges', 'bayanistges', 'beback', 'beerges', 'binocularsges', 'bla', 'bouqetges', 'bragging', 'braggingvip', 'bunchofmoney', 'bye', 'camouflageges', 'cannotspeak', 'chocolateges', 'contempt', 'cool', 'crackerges', 'crazy', 'cry', 'dance', 'defenderges', 'devil', 'dikaprio', 'disagree', 'disco', 'dj', 'dragonges', 'driving', 'drunk', 'elvisges', 'explode', 'facepalm', 'famousges', 'fearful', 'fk', 'gentleman', 'gratitude', 'grinning', 'grunt', 'guitaristges', 'handround', 'happy', 'heartface', 'heartges', 'hello', 'jediges', 'jewelerges', 'jugglerges', 'kiss', 'laugh', 'little', 'lookthroughges', 'loveges', 'michael', 'mimistges', 'monkey', 'muerteges', 'music', 'nohearts', 'nurseges', 'ok', 'orangutanges', 'parrot', 'party', 'rap', 'rich', 'rock', 'sad', 'scream', 'selfie', 'shy', 'sleepges', 'smoking', 'snowballges', 'sparklerges', 'star', 'stereoglassesges', 'strictges', 'sun', 'thumbdown', 'thumbup', 'tongue', 'vomiting', 'wall', 'watermelonges', 'wink', 'yawn']

_ASSETS_JSON = Path(__file__).resolve().parents[2] / "site" / "assets.json"


def _load_assets_json() -> dict:
    try:
        return json.loads(_ASSETS_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _gesture_prices_from_assets() -> dict[str, int]:
    gestures = (_load_assets_json().get("gestures") or {})
    out: dict[str, int] = {}
    for key, meta in gestures.items():
        if key.startswith("__") or not isinstance(meta, dict):
            continue
        price = meta.get("storePrice")
        if price is not None:
            out[str(key)] = int(price)
    return out


def _gold2tokens_items_from_assets() -> list[dict[str, int]]:
    tokens = (_load_assets_json().get("tokens") or {})
    bank = tokens.get("bank_v2") or tokens.get("bank") or []
    items: list[dict[str, int]] = []
    for row in bank:
        if not isinstance(row, dict):
            continue
        gold = int(row.get("price") or row.get("gold") or 0)
        tok = int(row.get("tokens") or 0)
        if gold > 0 and tok > 0:
            items.append({"gold": gold, "tokens": tok})
    return items


_ASSET_GESTURE_PRICES = _gesture_prices_from_assets()
GESTURE_PRICES = {
    g: _ASSET_GESTURE_PRICES.get(g, 5) for g in GESTURE_TYPES
}
GOLD2TOKENS_ITEMS: list[dict[str, int]] = _gold2tokens_items_from_assets() or [
    {"gold": 180, "tokens": 450},
    {"gold": 30, "tokens": 75},
    {"gold": 10, "tokens": 25},
    {"gold": 5, "tokens": 10},
    {"gold": 2, "tokens": 3},
]
GOLD2TOKENS_BY_GOLD: dict[int, int] = {
    int(row["gold"]): int(row["tokens"]) for row in GOLD2TOKENS_ITEMS
}

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
# Oxirgi ko'rinadigan stol shuncha o'yinchi (yoki navbat) bo'lsa — keyingi stol menyuda ochiladi
TABLE_BUSY_OPEN_NEXT = MAX_SEATS

# HTML5 bottle (plain_ws): har qanday xabardan keyin faollik; shundan oshsa uziladi
BOTTLE_PLAIN_IDLE_DISCONNECT_MS = 10 * 60 * 1000

# ── League ────────────────────────────────────────────────────────────────
LEAGUE_STATES = ["welcome", "running", "finished"]
LEAGUE_KISS_LIMIT = 500
# Klient: liga musobaqalariga kirish uchun umumiy kiss (users.kisses / total_kisses)
LEAGUE_UNLOCK_KISSES = 100
MAX_LEAGUE_TIER = 15


def league_tier_from_total_kisses(total_kisses: int) -> int:
    return min(max(int(total_kisses or 0) // 400, 0), MAX_LEAGUE_TIER)


def league_state_for_total_kisses(total_kisses: int) -> str:
    return "running" if int(total_kisses or 0) >= LEAGUE_UNLOCK_KISSES else "welcome"

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
REFERRAL_BONUS_HEARTS   = 50   # har bir taklif uchun
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

# Hearts paketlari: kalit = `gm_hearts_purchase` STARS (qiymat = olinadigan ❤️ jami).
# Narxlar: 2-rasm (⭐) bo‘yicha 75% chegirma → to‘lov asl narxning 25%.
# 10→25, 500→250, 2200→500, 6000→1250, 12500→2500
HEARTS_PACKAGES = {
    25: 10,
    250: 500,
    500: 2200,
    1250: 6000,
    2500: 12500,
}


def hearts_for_stars_price(stars_price: int) -> int | None:
    """Telegram Stars narxi (XTR) → olinadigan yuraklar (gold+bonus jami)."""
    return HEARTS_PACKAGES.get(int(stars_price))


def is_hearts_package_stars(stars_price: int) -> bool:
    return int(stars_price) in HEARTS_PACKAGES


def validate_hearts_product(stars_price: int, hearts: int) -> bool:
    expected = hearts_for_stars_price(stars_price)
    return expected is not None and int(hearts) == int(expected)


# Bank dovşan (rabbit_gift_send / rabbit_gift_caught)
RABBIT_MIN_PLAYERS = 5
RABBIT_SEND_COST_TOKENS = 20
RABBIT_SEND_COST_HEARTS = 20
RABBIT_CATCH_REWARD_HEARTS = 10
RABBIT_CATCH_REWARD_TOKENS = 10
RABBIT_ACTIVE_DURATION_SEC = 120
RABBIT_GIFT_TYPES = frozenset({"rabbit_gm", "rabbit_heart"})

# ─── Bank komplimentlari (klient: compliment_next / compliment_send) ───────
COMPLIMENTS_TO_REWARD = 3
COMPLIMENT_GOLD_REWARD = 50
