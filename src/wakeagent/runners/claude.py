
def run_claude(prompt, session_id=None, mode="read-only", capture_file=None, workdir="/work"):
    import subprocess, pathlib
    evidence = ""
    if capture_file and pathlib.Path(capture_file).exists():
        try:
            result = subprocess.run([capture_file], capture_output=True, text=True, timeout=30, cwd=workdir)
            evidence = result.stdout
        except Exception as e:
            evidence = f"capture failed: {e}"
    full_prompt = f"{evidence}\n\n{prompt}" if evidence else prompt
    cmd = ["Muse", "exec", "--reasoning-effort", "medium"]
    if session_id:
        cmd += ["--session-id", session_id]
    try:
        result = subprocess.run(cmd + [full_prompt], capture_output=True, text=True, timeout=600, cwd=workdir)
        return result.returncode, result.stdout, result.stderr
    except Exception as e:
        return 1, "", str(e)
