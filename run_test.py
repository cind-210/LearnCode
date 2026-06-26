import subprocess
import sys
import os

os.chdir(r"C:\OD\Project2\Agent\learncode")
result = subprocess.run(
    [sys.executable, "-m", "src.main"],
    capture_output=True,
    text=True,
    timeout=10,
)
with open(r"C:\OD\Project2\Agent\learncode\run_result.txt", "w", encoding="utf-8") as f:
    f.write("STDOUT:\n" + result.stdout[:2000] + "\n\nSTDERR:\n" + result.stderr[:2000] + "\n\nEXIT: " + str(result.returncode))
print("DONE")
