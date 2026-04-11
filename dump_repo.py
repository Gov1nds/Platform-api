import os

IGNORE_DIRS = {"node_modules", ".git", "dist", "build", "__pycache__"}
OUTPUT_FILE = "repo_dump.txt"

def should_ignore(path):
    return any(part in IGNORE_DIRS for part in path.split(os.sep))

with open(OUTPUT_FILE, "w", encoding="utf-8") as out:
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]

        for file in files:
            filepath = os.path.join(root, file)

            if should_ignore(filepath):
                continue

            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    out.write(f"=== FILE: {filepath} ===\n")
                    out.write(f.read())
                    out.write("\n\n")
            except:
                pass