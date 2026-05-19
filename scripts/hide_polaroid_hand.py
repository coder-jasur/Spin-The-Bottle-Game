"""Remove pointing-hand slot from s_polaroid3 spine assets."""
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1] / "src" / "app" / "site" / "bottle"
for p in root.glob("bundle/*/s_polaroid3.json"):
    data = json.loads(p.read_text(encoding="utf-8"))
    data["slots"] = [s for s in data["slots"] if s["name"] != "hand"]
    data["skins"][0]["attachments"].pop("hand", None)
    p.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    print("patched", p)
