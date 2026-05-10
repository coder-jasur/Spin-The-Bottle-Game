
import os
import json

assets_json_path = r'c:\Users\dhikm\PycharmProjects\SpinTheBootleGame\src\app\site\assets.json'
bundle_path = r'c:\Users\dhikm\PycharmProjects\SpinTheBootleGame\src\app\site\bottle\bundle\300'

with open(assets_json_path, 'r', encoding='utf-8') as f:
    root_assets = json.load(f)

# Flatten all items from all categories
assets = {}
for cat_name, cat_items in root_assets.items():
    if isinstance(cat_items, dict):
        assets.update(cat_items)

found_unlinked_animations = []

# List all s_*.json files
s_files = [f for f in os.listdir(bundle_path) if f.startswith('s_') and f.endswith('.json')]

for sf in s_files:
    animation_id = sf[:-5] # remove .json
    
    # Check if any asset uses this animation
    linked = False
    for asset_id, asset_info in assets.items():
        if not isinstance(asset_info, dict): continue
        if animation_id in asset_info.get('spine', []):
            linked = True
            break
    
    if not linked:
        # Check if there's a gift with a similar name
        base_name = animation_id[2:] # remove s_
        if base_name in assets:
            found_unlinked_animations.append((base_name, animation_id))
        elif base_name.replace('_v2', '') in assets:
             found_unlinked_animations.append((base_name.replace('_v2', ''), animation_id))

print("Found unlinked animations (gift exists, but animation not linked):")
for gift_id, anim_id in found_unlinked_animations:
    print(f" - Gift '{gift_id}' could use animation '{anim_id}'")
