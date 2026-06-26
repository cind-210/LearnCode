import py_compile
import os
import sys
import traceback

base = os.path.join(os.path.dirname(__file__), "src")
errors = []
for root, dirs, files in os.walk(base):
    for f in sorted(files):
        if f.endswith(".py"):
            fp = os.path.join(root, f)
            try:
                py_compile.compile(fp, doraise=True)
            except py_compile.PyCompileError as e:
                errors.append(f"ERROR in {fp}: {e}")

with open(os.path.join(os.path.dirname(__file__), "compile_result.txt"), "w", encoding="utf-8") as out:
    if errors:
        for e in errors:
            out.write(e + "\n")
        out.write(f"\n{len(errors)} file(s) have errors.")
    else:
        out.write("All Python files compiled successfully.")
