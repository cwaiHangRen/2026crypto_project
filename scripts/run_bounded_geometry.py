from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import time


def main() -> int:
    root = pathlib.Path(__file__).resolve().parents[1]
    out = root / "submission/logs/geometry-pressure-bounded.txt"
    tmp = root / "submission/geometry-tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")
    command = [sys.executable, "-m", "pytest", "--basetemp", str(tmp), "-q", "tests/unit/test_rotation_geometry.py"]
    started = time.perf_counter()
    status = "completed"
    return_code: int | None = None
    error = ""
    output = ""
    try:
        result = subprocess.run(command, cwd=root, env=env, text=True, capture_output=True, timeout=20)
        return_code = result.returncode
        output = result.stdout + "\n" + result.stderr
    except subprocess.TimeoutExpired as exc:
        status = "timeout"
        error = "bounded timeout 20s"
        output = (exc.stdout or "") + "\n" + (exc.stderr or "")
    out.write_text(
        "command=" + repr(command) + "\n"
        + "status=" + status + "\n"
        + "return_code=" + str(return_code) + "\n"
        + "elapsed_ms=" + str(round((time.perf_counter() - started) * 1000, 3)) + "\n"
        + "error=" + error + "\n"
        + output,
        encoding="utf-8",
    )
    print(status, return_code)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
