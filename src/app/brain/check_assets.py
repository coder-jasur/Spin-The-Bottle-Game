
import os
import json

constants_path = r'c:\Users\dhikm\PycharmProjects\SpinTheBootleGame\src\app\api\ws\constants.py'
assets_json_path = r'c:\Users\dhikm\PycharmProjects\SpinTheBootleGame\src\app\site\assets.json'
bundle_path = r'c:\Users\dhikm\PycharmProjects\SpinTheBootleGame\src\app\site\bottle\bundle\300'

# Extract GIFT_TYPES from constants.py
gift_types = []
with open(constants_path, 'r', encoding='utf-8') as f:
    content = f.read()
    start = content.find('GIFT_TYPES = [')
    if start == -1:
        # Try finding it without GIFT_
        start = content.find('GIFT_TYPES = [')
    
    end = content.find(']', start)
    list_str = content[start:end+1]
    items = list_str.replace('GIFT_TYPES = [', '').replace(']', '').replace('\n', '').replace(' ', '').replace('"', '').replace("'", "").split(',')
    gift_types = [it.strip() for it in items if it.strip()]

print(f"Checking {len(gift_types)} gift types...")

with open(assets_json_path, 'r', encoding='utf-8') as f:
    root_assets = json.load(f)

# Flatten all items from all categories
assets = {}
for cat_name, cat_items in root_assets.items():
    if isinstance(cat_items, dict):
        assets.update(cat_items)
    else:
        # Some categories might be lists or other things
        pass

missing_in_assets = []
missing_files = []

for gt in gift_types:
    if gt not in assets:
        missing_in_assets.append(gt)
        continue
    
    asset_info = assets[gt]
    if not isinstance(asset_info, dict):
        continue
        
    # Check flyImages
    for img in asset_info.get('flyImages', []):
        if not os.path.exists(os.path.join(bundle_path, img + '.webp')):
            missing_files.append(f"{gt}: flyImage {img}.webp")
            
    # Check stickImages
    for img in asset_info.get('stickImages', []):
        if not os.path.exists(os.path.join(bundle_path, img + '.webp')):
            missing_files.append(f"{gt}: stickImage {img}.webp")
            
    # Check spine
    for spine in asset_info.get('spine', []):
        if not os.path.exists(os.path.join(bundle_path, spine + '.json')):
            missing_files.append(f"{gt}: spine {spine}.json")
        if not os.path.exists(os.path.join(bundle_path, spine + '.webp')):
            missing_files.append(f"{gt}: spine {spine}.webp")

print("\nMissing in assets.json:")
for m in missing_in_assets:
    print(f" - {m}")

print("\nMissing files in bundle/300:")
for m in missing_files:
    print(f" - {m}")
