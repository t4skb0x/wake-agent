def run_codex(prompt, **kw):
    import subprocess
    return subprocess.run(['codex', 'exec', prompt], capture_output=True, text=True, timeout=600)
