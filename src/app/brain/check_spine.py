
import json

constants_path = r'c:\Users\dhikm\PycharmProjects\SpinTheBootleGame\src\app\api\ws\constants.py'
assets_json_path = r'c:\Users\dhikm\PycharmProjects\SpinTheBootleGame\src\app\site\assets.json'

with open(constants_path, 'r', encoding='utf-8') as f:
    content = f.read()
    start = content.find('GIFT_TYPES = [')
    end = content.find(']', start)
    list_str = content[start:end+1]
    gift_types = [it.strip().strip('"').strip("'") for it in list_str.replace('GIFT_TYPES = [', '').replace(']', '').replace('\n', '').split(',') if it.strip()]

with open(assets_json_path, 'r', encoding='utf-8') as f:
    root_assets = json.load(f)

# Find in "gifts" and "hats" and "drinks"
search_cats = ["gifts", "hats", "drinks", "decor", "gestures"]
# Actually, let's just find them anywhere

found_gifts = {}

def find_item(obj, target_id):
    if isinstance(obj, dict):
        if target_id in obj and isinstance(obj[target_id], dict):
            return obj[target_id]
        for k, v in obj.items():
            res = find_item(v, target_id)
            if res: return res
    return None

for gt in gift_types:
    item = find_item(root_assets, gt)
    if item:
        found_gifts[gt] = item
    else:
        found_gifts[gt] = "NOT FOUND"

print("Spine status for GIFT_TYPES:")
for gt in gift_types:
    item = found_gifts[gt]
    if item == "NOT FOUND":
        print(f" - {gt}: NOT FOUND in assets.json")
    else:
        spine = item.get('spine', [])
        print(f" - {gt}: spine={spine}")
