"""
fix_animations.py  (v2 - with manual overrides)
================================================
assets.json dagi gift/hat elementlarga spine animatsiyasini qo'shadi.
Qo'lda ko'rsatilgan moslashlar (overrides) eng ustuvor hisoblanadi.

Ishlatish:
    python src/app/brain/fix_animations.py
"""

import json
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

ASSETS_JSON = os.path.join(os.path.dirname(__file__), '..', 'site', 'assets.json')
BUNDLE_DIR  = os.path.join(os.path.dirname(__file__), '..', 'site', 'bottle', 'bundle', '300')

# ── 1) Diskdagi spine fayllar ─────────────────────────────────
available_spines = set()
for fname in os.listdir(BUNDLE_DIR):
    if fname.endswith('.json') and fname.startswith('s_'):
        available_spines.add(fname[:-5])

print(f"[OK] Disk: {len(available_spines)} spine fayli topildi.")

# ── 2) assets.json o'qi ───────────────────────────────────────
with open(ASSETS_JSON, encoding='utf-8-sig') as f:
    assets = json.load(f)

# ── 3) Qo'lda moslashlar (item_type → spine_name) ─────────────
MANUAL_MAP = {
    # Tojlar / Crowns
    "crown1":           "s_vipcrown",
    "crown2":           "s_vipcrown",
    "crown3":           "s_vipcrown",
    "crown4":           "s_vipcrown",
    "blackcrown":       "s_vipcrown",

    # VIP
    "viphorseshoe":     "s_viphorseshoe",
    "vipgold":          "s_vipgold",
    "viplegbox":        "s_viplegbox",
    "viproses":         "s_viproses",

    # Sovg'alar – eski nomlar / renamed
    "air_kiss":         "s_ges_airkiss",
    "air_kiss_premium": "s_ges_airkiss",

    # Gullar (flowers)
    "flowerspink":      "s_flowerspink",
    "flowersviolet":    "s_flowersviolet",
    "flowerswhitered":  "s_flowerswhitered",
    "flowersyellow":    "s_flowersyellow",

    # Ichimliklar
    "beer":             "s_beer_v2",
    "champagne":        "s_champagne",
    "vodka":            "s_vodka",
    "wine":             "s_wine",
    "whiskey":          "s_whiskey",
    "martini":          "s_martini",
    "mojito":           "s_mojito",
    "pinacolada":       "s_pinacolada",
    "daiquiri":         "s_daiquiri",
    "cognac":           "s_cognacglass",
    "bloodymary":       "s_bloodymary",
    "bluelagoon":       "s_bluelagoon",
    "glintwein":        "s_glintwein",
    "grog":             "s_grog",
    "tequila":          "s_tequilashot",
    "sexonthebeach":    "s_sexonthebeach",
    "spacecocktail":    "s_spacecocktail",
    "springcocktail":   "s_springcocktail",
    "b52":              "s_b52",
    "babycocktail":     "s_babycocktail",
    "tarhun":           "s_tarhun",
    "rassol":           "s_rassol",
    "healthdrink":      "s_healthdrink",
    "mineralka":        "s_mineralka",
    "juice":            "s_juice",
    "latte":            "s_latte",
    "coffee":           "s_coffee",
    "tea":              "s_tea",
    "rzdtea":           "s_rzdtea",
    "milkcoctail":      "s_milkcoctail",
    "milk":             "s_milk",
    "turkishcoffee":    "s_turkishcoffee",
    "maroccantea":      "s_maroccantea",
    "starbucks":        "s_starbucks",

    # Taomlar
    "hamburger":        "s_hamburger",
    "doshirak":         "s_doshirak",
    "friedpotatoes":    "s_friedpotatoes",
    "peach":            "s_peach",
    "ananas":           "s_ananas",
    "mandarin":         "s_mandarin",
    "banana":           "s_banana_v2",
    "eggplant":         "s_eggplant",
    "bbq":              "s_bbq",
    "spaghetti":        "s_spaghetti",
    "cherrypie":        "s_cherrypie",
    "cherry":           "s_cherry",
    "tomato":           "s_cherry",
    "strawberry":       "s_cherry",

    # Hayvonlar
    "corgidog":         "s_corgidog",
    "funnyanimal":      "s_funnyanimal",
    "pig":              "s_pig",
    "owl":              "s_owl",
    "elephant":         "s_elephant",
    "orangutan":        "s_orangutan",
    "frog":             "s_frog_v2",
    "panda":            "s_panda_v2",
    "petcat":           "s_petcat_v2",
    "petdog":           "s_petdog_v2",
    "squirrel":         "s_squirrel_v2",
    "reindeer":         "s_reindeer_v2",
    "pinguin":          "s_pinguin",
    "lionet":           "s_lionet",
    "pirateparrot":     "s_pirateparrot",
    "firehorse":        "s_firehorse",
    "groundhog":        "s_groundhog",
    "tarantula":        "s_tarantula",
    "dinosaur":         "s_dinosaur",

    # Sovg'alar – boshqa
    "brokenheart":      "s_brokenheart",
    "camera":           "s_camera",
    "chess":            "s_chess",
    "compass":          "s_compass",
    "cufflinks":        "s_cufflinks",
    "diamond":          "s_diamond",
    "dynamite":         "s_dynamite",
    "eggskiss":         "s_eggskiss",
    "faberge":          "s_faberge",
    "fan":              "s_fan",
    "fern":             "s_fern",
    "fire":             "s_fire_v2",
    "gem":              "s_gem5",
    "gem2":             "s_gem5",
    "gem3":             "s_gem5",
    "genielamp":        "s_genielamp_v2",
    "glassshoe":        "s_glassshoe",
    "globe":            "s_globe",
    "goldenapple":      "s_goldenapple",
    "goldpot":          "s_goldpot",
    "gramophone":       "s_gramophone",
    "grandpiano":       "s_grandpiano",
    "hamburger":        "s_hamburger",
    "handcuffs":        "s_handcuffs",
    "harp":             "s_harp",
    "heart":            "s_heart1",
    "heart1":           "s_heart1",
    "heart2":           "s_heart2",
    "heart3":           "s_heart3",
    "heartangel":       "s_heartangel",
    "heartdevil":       "s_heartdevil",
    "heartlock":        "s_heartlock",
    "heineken":         "s_heineken",
    "hockeycup":        "s_hockeycup",
    "honeybarrel":      "s_honeybarrel",
    "hookah":           "s_hookah_v2",
    "iwatch":           "s_iwatch",
    "jam":              "s_jam",
    "japanesefan":      "s_japanesefan",
    "kinder":           "s_kinder2_v2",
    "kinder2":          "s_kinder2_v2",
    "kinder3":          "s_kinder3_v2",
    "kinder4":          "s_kinder4_v2",
    "kinder5":          "s_kinder5_v2",
    "kokoshnik":        "s_kokoshnik",
    "liberty":          "s_liberty",
    "lifestyle":        "s_lifestyle",
    "lighter":          "s_lighter",
    "lipstick":         "s_lipstick",
    "londonphone":      "s_londonphone",
    "love":             "s_love",
    "lovepotion":       "s_lovepotion_v2",
    "magichat":         "s_magichat",
    "manekineko":       "s_manekineko",
    "matreshka":        "s_matreshka",
    "meldonium":        "s_meldonium",
    "menorah":          "s_menorah",
    "mobile":           "s_mobile",
    "notes":            "s_notes1",
    "notes1":           "s_notes1",
    "notes2":           "s_notes2",
    "notes3":           "s_notes3",
    "notes4":           "s_notes4",
    "oscar":            "s_oscar_v2",
    "pearl":            "s_pearl_v2",
    "perfume":          "s_perfume",
    "pipe":             "s_pipe",
    "pirateswords":     "s_pirateswords_v2",
    "pisatower":        "s_pisatower",
    "plunger":          "s_plunger",
    "poison":           "s_poison",
    "polaroid":         "s_polaroid1",
    "polaroid1":        "s_polaroid1",
    "polaroid2":        "s_polaroid2",
    "polaroid3":        "s_polaroid3",
    "polaroid4":        "s_polaroid4",
    "polaroid5":        "s_polaroid5",
    "poop":             "s_poop",
    "rocket":           "s_rocket",
    "romanticcandle":   "s_romanticcandle",
    "romcola":          "s_romcola_v2",
    "rose":             "s_rose_v2",
    "roseinglass":      "s_roseinglass",
    "rubyearrings":     "s_rubyearrings_v2",
    "rubyheart":        "s_rubyheart_v2",
    "rubyrose":         "s_rubyrose_v2",
    "rubyshoes":        "s_rubyshoes_v2",
    "salute":           "s_salute2",
    "salute2":          "s_salute2",
    "salute3":          "s_salute3",
    "salute4":          "s_salute4",
    "samovar":          "s_samovar_v2",
    "sheriffstar":      "s_sheriffstar",
    "sigara":           "s_sigara",
    "skull":            "s_skull",
    "smilecup":         "s_smilecup",
    "snakepot":         "s_snakepot",
    "snowdrops":        "s_snowdrops_v2",
    "sponge":           "s_sponge_green",
    "sponge_green":     "s_sponge_green",
    "sponge_violet":    "s_sponge_violet",
    "sponge_yellow":    "s_sponge_yellow",
    "springwreath":     "s_springwreath",
    "starbucks":        "s_starbucks",
    "submarine":        "s_submarine",
    "sunflower":        "s_sunflower_v2",
    "surprizebox":      "s_surprizebox_v3",
    "teapot":           "s_teapot",
    "tearssnake":       "s_tearssnake",
    "teddybear":        "s_teddybear",
    "trash":            "s_trash",
    "trumpet":          "s_trumpet",
    "tub":              "s_tub",
    "ufo":              "s_ufo",
    "valenok":          "s_valenok",
    "valenok2":         "s_valenok2",
    "valenok3":         "s_valenok3",
    "valentine":        "s_valentine2",
    "valentine2":       "s_valentine2",
    "valentine3":       "s_valentine3",
    "valentine4":       "s_valentine4",
    "valentine5":       "s_valentine5",
    "vanilla":          "s_vanilla",
    "venus":            "s_venus",
    "voodoo":           "s_voodoo",
    "bouquet":          "s_bouquet",
    "bouquet2":         "s_bouquet2_v2",
    "blackrose":        "s_blackrose",
    "birds":            "s_birds",
    "alarmclock":       "s_alarmclock",
    "amurchik":         "s_amurchik",
    "astronaut":        "s_astronaut",
    "astronautch":      "s_astronaut_ch",
    "astronauteu":      "s_astronaut_eu",
    "astronautru":      "s_astronaut_ru",
    "astronauts":       "s_astronaut_us",
    "baloon":           "s_baloon1",
    "baloon1":          "s_baloon1",
    "baloon2":          "s_baloon2",
    "baloon3":          "s_baloon3",
    "birthdaycake":     "s_birthdaycake_v2",
    "birthdaycake2":    "s_birthdaycake2_v2",
    "birthdaycake3":    "s_birthdaycake3_v2",
    "3september":       "s_3september",
    "candle":           "s_candle_v2",
    "chemistry":        "s_chemistry_v2",
    "chertovka":        "s_chertovka",
    "chinese":          "s_chinese",
    "christmasball":    "s_christmasball",
    "colamentos":       "s_colamentos",
    "cooler":           "s_cooler_v2",
    "cuckooclock":      "s_cuckooclock_v2",
    "easterrabbit":     "s_easterrabbit_v2",
    "football":         "s_football_v2",

    # Shapkalar (hats)
    "magichat":         "s_magichat",
    "minerhat":         "s_minerhat",
    "migalka":          "s_migalka",
    "ololosh":          "s_ololosh_v2",
    "spaghettih":       "s_spaghetti",
    "taxi":             "s_taxi",
    "ledglasses":       "s_ledglasses_android",
    "ledglasses_android": "s_ledglasses_android",
    "kokoshnikhat":     "s_kokoshnik",
    "vipcrown":         "s_vipcrown",
    "viphorseshoe":     "s_viphorseshoe",
    "bowidle":          "s_bowidle",

    # Mamlakat spirtli ichimliklari
    "drinkam":          "s_drinkam",
    "drinkaz":          "s_drinkaz",
    "drinkby":          "s_drinkby",
    "drinkge":          "s_drinkge",
    "drinkkg":          "s_drinkkg",
    "drinkkz":          "s_drinkkz",
    "drinkru":          "s_drinkru",
    "drinkua":          "s_drinkua",
    "drinkus":          "s_drinkus",
    "drinkuz":          "s_drinkuz",
}

# ── 4) Kategoriya bo'yicha fallback qoidalar ──────────────────
# Pattern → fallback spine
# Tartib muhim: birinchi mos kelgan ishlatiladi
CATEGORY_PATTERNS = [
    # Shapkalar (hats) — prefix/suffix asosida
    (["cap", "hat", "crown", "helmet", "budenovka", "beret",
      "kokoshnik", "clown", "cylinder", "cappy", "classi",
      "babecap", "bosscap", "boybye", "builderhat", "batmanhat",
      "blackoverload", "bezkoz", "cosmo", "colander", "cook",
      "cowboy", "carnivalmask", "baseball", "bayanist"],
     "s_vipcrown"),

    # Qishki / Rojdestvo sovg'alari
    (["christmas", "christmasbell", "christmassock", "valenok",
      "snow", "reindeer", "winter"],
     "s_christmasball"),

    # Kiyimlar / Aksessuarlar
    (["bow", "bra", "bikini", "bikinitop", "bikinibottom", "boxing"],
     "s_heart1"),

    # Hayvonlar
    (["bear", "cat", "dog", "bunny", "rabbit", "pet",
      "monkey", "parrot", "horse", "bird"],
     "s_petdog_v2"),

    # Taomlar / Shirinliklar
    (["candy", "baranki", "donut", "bretzel", "cake",
      "food", "fruit", "berry", "clover", "comet"],
     "s_cherry"),

    # Gullar / O'simliklar
    (["flower", "rose", "bouquet", "wreath", "blossom", "fern"],
     "s_flowerspink"),

    # Portlash / Kutilmagan
    (["bomb", "dyna", "rocket", "fire", "astro", "ufo",
      "astronomy"],
     "s_surprizebox_v3"),

    # Umumiy fallback (hat kategoriyasi)
    (["__hat__"],
     "s_vipcrown"),

    # Umumiy fallback (gift kategoriyasi)
    (["__gift__"],
     "s_surprizebox_v3"),
]

TARGET_CATEGORIES = {'gift', 'hat'}

def category_fallback(item_type: str, item_key: str, category: str) -> str | None:
    """Kalit so'z yoki kategoriya asosida fallback spine qaytaradi."""
    name = (item_type + " " + item_key).lower()

    for keywords, spine in CATEGORY_PATTERNS:
        # Maxsus marker lar
        if keywords == ["__hat__"] and category == "hat":
            return spine if spine in available_spines else None
        if keywords == ["__gift__"] and category == "gift":
            return spine if spine in available_spines else None

        # Kalit so'z qidirish
        for kw in keywords:
            if kw in name:
                if spine in available_spines:
                    return spine
                break
    return None

def guess_spine(item_type: str, item_key: str, category: str = "gift"):
    # 1) Qo'lda moslash
    for lookup in (item_type, item_key):
        if lookup in MANUAL_MAP:
            spine = MANUAL_MAP[lookup]
            if spine in available_spines:
                return spine

    # 2) Avtomatik taxmin (to'g'ridan-to'g'ri nom)
    for candidate in [
        f"s_{item_type}",
        f"s_{item_key}",
        f"s_{item_type}_v2",
        f"s_{item_type}_v3",
        f"s_ges_{item_type}",
    ]:
        if candidate in available_spines:
            return candidate

    # 3) Kategoriya bo'yicha fallback
    return category_fallback(item_type, item_key, category)

# ── 5) Barcha elementlarni yangilaish ──────────────────────────
fixed_count   = 0
skipped_count = 0
missing_list  = []

for section_key, section in assets.items():
    if not isinstance(section, dict):
        continue
    for item_key, item in section.items():
        if not isinstance(item, dict):
            continue
        category  = item.get('category', '')
        item_type = item.get('type', item_key)
        if category not in TARGET_CATEGORIES:
            continue
        if 'spine' in item:
            skipped_count += 1
            continue
        spine_name = guess_spine(item_type, item_key, category)
        if spine_name:
            item['spine'] = [spine_name]
            fixed_count += 1
            print(f"  ADDED [{section_key}] {item_key} -> {spine_name}")
        else:
            missing_list.append(f"{item_key} (type={item_type})")

# ── 6) Saqlash ─────────────────────────────────────────────────
with open(ASSETS_JSON, 'w', encoding='utf-8') as f:
    json.dump(assets, f, ensure_ascii=False, indent=2)

print()
print("=" * 60)
print(f"  FIXED   : {fixed_count}")
print(f"  SKIPPED : {skipped_count}  (already had spine)")
print(f"  MISSING : {len(missing_list)}  (no disk file found)")
print("=" * 60)

if missing_list:
    print("\n--- Still missing spine ---")
    for m in missing_list[:50]:
        print(f"  {m}")
    if len(missing_list) > 50:
        print(f"  ... and {len(missing_list)-50} more")

print("\n[DONE] assets.json updated!")
