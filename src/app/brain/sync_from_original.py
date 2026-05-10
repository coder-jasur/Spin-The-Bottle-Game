"""
sync_from_original.py
=====================
asset_original.json faylidagi spine (va ixtiyoriy vfx) ma'lumotlarini
assets.json ga ko'chiradi. Original faylda bo'lgan spine qiymatlari
assets.json dagi mos elementlarni yangilaydi.

Ishlatish:
    python src/app/brain/sync_from_original.py
"""

import json
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR     = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
ORIGINAL_JSON = os.path.join(BASE_DIR, 'asset_original.json')
ASSETS_JSON   = os.path.join(os.path.dirname(__file__), '..', 'site', 'assets.json')

print(f"[INFO] Original: {ORIGINAL_JSON}")
print(f"[INFO] Target  : {ASSETS_JSON}")

# ── 1) Fayllarni o'qi ─────────────────────────────────────────
def load_json(path):
    try:
        with open(path, encoding='utf-8-sig') as f:
            return json.load(f)
    except UnicodeDecodeError:
        with open(path, encoding='utf-8') as f:
            return json.load(f)

original = load_json(ORIGINAL_JSON)
assets   = load_json(ASSETS_JSON)

print(f"[OK]  Fayllar yuklandi.")

# ── 2) Original fayldan (type → spine) xaritasini yasaymiz ───
# Barcha top-level section larni ko'rib chiqamiz
spine_map = {}  # item_type → { 'spine': ..., 'vfx': ... }

for section_key, section in original.items():
    if not isinstance(section, dict):
        continue
    for item_key, item in section.items():
        if not isinstance(item, dict):
            continue
        if 'spine' not in item:
            continue

        item_type = item.get('type', item_key)
        entry = {'spine': item['spine']}
        if 'vfx' in item:
            entry['vfx'] = item['vfx']

        # item_key va item_type ikkalasini ham kalit sifatida saqlays
        spine_map[item_key]  = entry
        spine_map[item_type] = entry

print(f"[OK]  Original dan {len(spine_map)} ta noyob spine xaritasi topildi.")

# ── 3) assets.json ni yangilaymiz ─────────────────────────────
TARGET_CATEGORIES = {'gift', 'hat', 'gesture'}
SYNC_FIELDS = ['spine', 'vfx']  # Ko'chiriladigan maydonlar

fixed_count   = 0
skipped_count = 0
missing_count = 0
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

        # Allaqachon spine bormi? (ixtiyoriy: skip qilmasdan override ham qilish mumkin)
        # Bu yerda: faqat spine YO'Q bo'lsa yangilaymiz
        if 'spine' in item:
            skipped_count += 1
            continue

        # Original dan spine qidirish
        found = spine_map.get(item_key) or spine_map.get(item_type)

        if found:
            for field in SYNC_FIELDS:
                if field in found:
                    item[field] = found[field]
            fixed_count += 1
            print(f"  SYNC [{section_key}] {item_key}  ->  spine={found['spine']}")
        else:
            missing_count += 1
            missing_list.append(f"{item_key} (type={item_type}, cat={category})")

# ── 4) Saqlash ─────────────────────────────────────────────────
with open(ASSETS_JSON, 'w', encoding='utf-8') as f:
    json.dump(assets, f, ensure_ascii=False, indent=2)

print()
print("=" * 60)
print(f"  SYNCED  : {fixed_count}   (original dan spine ko'chirildi)")
print(f"  SKIPPED : {skipped_count}  (spine allaqachon mavjud edi)")
print(f"  MISSING : {missing_count}  (original da ham topilmadi)")
print("=" * 60)

if missing_list:
    print("\n--- Original da ham spine yo'q elementlar ---")
    for m in missing_list[:60]:
        print(f"  {m}")
    if len(missing_list) > 60:
        print(f"  ... va yana {len(missing_list)-60} ta")

print("\n[DONE] assets.json original bilan sinxronlashtirildi!")
