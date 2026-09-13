from pathlib import Path
import yaml
from solarohith.captions import make_ass

cfg = yaml.safe_load(Path("config.yaml").read_text())
assert cfg["ai"]["model"] == "qwen3.6:latest"
assert cfg["ai"]["base_url"].endswith("/v1")

print("SCAG config/import smoke test: OK")
print("Qwen model:", cfg["ai"]["model"])
print("Ollama URL:", cfg["ai"]["base_url"])
