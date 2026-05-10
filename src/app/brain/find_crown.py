
import json

assets_json_path = r'c:\Users\dhikm\PycharmProjects\SpinTheBootleGame\src\app\site\assets.json'
with open(assets_json_path, 'r', encoding='utf-8') as f:
    root_assets = json.load(f)

for cat_name, cat_items in root_assets.items():
    if isinstance(cat_items, dict):
        if "crown" in cat_items:
            print(f"Found 'crown' in category: {cat_name}")
        for k in cat_items.keys():
            if "crown" in k:
                print(f"Found '{k}' in category: {cat_name}")
