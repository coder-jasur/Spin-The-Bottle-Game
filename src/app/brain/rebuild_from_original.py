"""
rebuild_from_original.py
========================
1) asset_original.json dan barcha element ma'lumotlarini o'qiydi
2) assets.json ni BUTUNLAY original asosida spine/vfx bilan yangilaydi
   (allaqachon spine bor bo'lsa ham original ustunlik qiladi)
3) Original da yo'q elementlar uchun hozirgi assets.json dagi spine saqlanadi

Ishlatish:
    python src/app/brain/rebuild_from_original.py
"""

import json
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR      = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
ORIGINAL_JSON = os.path.join(BASE_DIR, 'asset_original.json')
ASSETS_JSON   = os.path.join(os.path.dirname(__file__), '..', 'site', 'assets.json')

print(f"[INFO] Original : {ORIGINAL_JSON}")
print(f"[INFO] Target   : {ASSETS_JSON}")
print()

def load_json(path):
    for enc in ('utf-8-sig', 'utf-8', 'cp1251'):
        try:
            with open(path, encoding=enc) as f:
                return json.load(f)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    raise RuntimeError(f"Cannot parse {path}")

# ── 1) Yuklash ─────────────────────────────────────────────────
original = load_json(ORIGINAL_JSON)
assets   = load_json(ASSETS_JSON)
print("[OK]  Ikkala fayl ham yuklandi.")

# ── 2) Original dan (type, key) → spine+vfx xaritasi ──────────
# Ko'chiriladigan barcha maydonlar:
COPY_FIELDS = ['spine', 'vfx', 'finishSounds', 'startSounds']

orig_map = {}  # key → dict of fields

for sec_key, section in original.items():
    if not isinstance(section, dict):
        continue
    for item_key, item in section.items():
        if not isinstance(item, dict):
            continue
        if 'spine' not in item:
            continue

        entry = {}
        for f in COPY_FIELDS:
            if f in item:
                entry[f] = item[f]

        item_type = item.get('type', item_key)
        orig_map[item_key]  = entry
        orig_map[item_type] = entry

print(f"[OK]  Original dan {len(orig_map)} ta noyob yozuv topildi.")
print()

# ── 3) assets.json ni yangilash ───────────────────────────────
TARGET_CATEGORIES = {'gift', 'hat', 'gesture'}

stats = {'override': 0, 'new': 0, 'kept': 0, 'no_orig': 0}
no_orig_list = []

for sec_key, section in assets.items():
    if not isinstance(section, dict):
        continue
    for item_key, item in section.items():
        if not isinstance(item, dict):
            continue

        category  = item.get('category', '')
        item_type = item.get('type', item_key)

        if category not in TARGET_CATEGORIES:
            continue

        found = orig_map.get(item_key) or orig_map.get(item_type)

        if found:
            had_spine = 'spine' in item
            for f in COPY_FIELDS:
                if f in found:
                    item[f] = found[f]

            if had_spine:
                stats['override'] += 1
                print(f"  OVERRIDE [{sec_key}] {item_key}  ->  {found['spine']}")
            else:
                stats['new'] += 1
                print(f"  NEW      [{sec_key}] {item_key}  ->  {found['spine']}")
        else:
            # Original da yo'q — hozirgi spine ni saqla
            if 'spine' in item:
                stats['kept'] += 1
            else:
                stats['no_orig'] += 1
                no_orig_list.append(f"{item_key} (type={item_type})")

# ── 4) Saqlash ─────────────────────────────────────────────────
with open(ASSETS_JSON, 'w', encoding='utf-8') as f:
    json.dump(assets, f, ensure_ascii=False, indent=2)

print()
print("=" * 65)
print(f"  OVERRIDE (original bilan yangilandi) : {stats['override']}")
print(f"  NEW      (yangi spine qo'shildi)     : {stats['new']}")
print(f"  KEPT     (original yo'q, eskisi qoldi): {stats['kept']}")
print(f"  NO_ORIG  (hech qanday spine yo'q)    : {stats['no_orig']}")
print("=" * 65)

if no_orig_list:
    print(f"\n--- Spine topilmagan {len(no_orig_list)} ta element ---")
    for m in no_orig_list[:40]:
        print(f"  {m}")
    if len(no_orig_list) > 40:
        print(f"  ... va yana {len(no_orig_list)-40} ta")

print("\n[DONE] assets.json original asosida to'liq yangilandi!")
