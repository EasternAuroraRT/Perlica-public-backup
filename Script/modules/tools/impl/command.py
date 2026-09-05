import subprocess

def run_command(command: str) -> str:
    result = subprocess.run(command, shell=True, capture_output=True, text=True)
    return f"[RETCODE]\n{result.returncode}\n[STDOUT]\n{result.stdout}\n[STDERR]\n{result.stderr}"