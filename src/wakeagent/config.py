
import tomllib, json, pathlib

def load_toml(path):
    with open(path, "rb") as f:
        return tomllib.load(f)

def load_config(path="wake.toml"):
    p = pathlib.Path(path)
    if not p.exists():
        jp = pathlib.Path("config.json")
        if jp.exists():
            return json.loads(jp.read_text())
        return {}
    return load_toml(p)
