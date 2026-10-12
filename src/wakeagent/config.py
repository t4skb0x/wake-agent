import tomllib, json, pathlib
def load_config(path='wake.toml'):
    p=pathlib.Path(path)
    if not p.exists():
        return {}
    with open(p,'rb') as f:
        return tomllib.load(f)
