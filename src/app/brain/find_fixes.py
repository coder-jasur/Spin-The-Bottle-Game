
import os
import json

constants_path = r'c:\Users\dhikm\PycharmProjects\SpinTheBootleGame\src\app\api\ws\constants.py'
assets_json_path = r'c:\Users\dhikm\PycharmProjects\SpinTheBootleGame\src\app\site\assets.json'
bundle_path = r'c:\Users\dhikm\PycharmProjects\SpinTheBootleGame\src\app\site\bottle\bundle\300'

with open(constants_path, 'r', encoding='utf-8') as f:
    content = f.read()
    start = content.find('GIFT_TYPES = [')
    end = content.find(']', start)
    list_str = content[start:end+1]
    items = list_str.replace('GIFT_TYPES = [', '').replace(']', '').replace('\n', '').replace(' ', '').replace('"', '').replace("'", "").split(',')
    gift_types = [it.strip() for it in items if it.strip()]

with open(assets_json_path, 'r', encoding='utf-8') as f:
    root_assets = json.load(f)

assets = {}
for cat_name, cat_items in root_assets.items():
    if isinstance(cat_items, dict):
        assets.update(cat_items)

s_files = [f for f in os.listdir(bundle_path) if f.startswith('s_') and f.endswith('.json')]
animation_ids = [f[:-5] for f in s_files]

print(f"Total animations found: {len(animation_ids)}")
print(f"Sample animations: {animation_ids[:5]}")

fixes = {}

for gt in gift_types:
    if gt not in assets: continue
    asset_info = assets[gt]
    
    current_spine = asset_info.get('spine', [])
    
    potentials = [f"s_{gt}"]
    for img in asset_info.get('flyImages', []):
        name = img.replace('g_', 's_')
        potentials.append(name)
        potentials.append(name.replace('_v2', '').replace('_v3', ''))

    if gt == 'valentine':
        print(f"Valentine potentials: {potentials}")
        print(f"Valentine current spine: {current_spine}")

    found_missing = []
    for p in potentials:
        if p in animation_ids and p not in current_spine:
            found_missing.append(p)
            
    if found_missing:
        fixes[gt] = list(set(found_missing))

print("\nPotential fixes for GIFT_TYPES:")
for gt, missing in fixes.items():
    print(f" - Gift '{gt}': Add spine {missing}")
