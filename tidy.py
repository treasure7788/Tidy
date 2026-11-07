#!/usr/bin/env python3
"""Tidy - rename and organize files by telling an AI what you want.

Usage:  python tidy.py [folder]
Needs Python 3.8+ and nothing else (standard library only).
"""
import getpass
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

CONF = Path.home() / ".tidy.json"
PRESETS = {
    "openai":     {"fmt": "openai",    "url": "https://api.openai.com/v1",      "key": "OPENAI_API_KEY",     "model": ""},
    "anthropic":  {"fmt": "anthropic", "url": "https://api.anthropic.com",      "key": "ANTHROPIC_API_KEY",  "model": "claude-sonnet-5-5"},
    "openrouter": {"fmt": "openai",    "url": "https://openrouter.ai/api/v1",   "key": "OPENROUTER_API_KEY", "model": ""},
    "ollama":     {"fmt": "openai",    "url": "http://localhost:11434/v1",      "key": None,                 "model": ""},
    "custom":     {"fmt": "openai",    "url": "",                               "key": "TIDY_KEY",           "model": ""},
}
SYSTEM = (
    "You are a file organizer. You get a list of files (relative path | size | modified date) and an instruction.\n"
    'Reply with ONLY a JSON array of objects like {"from":"old/path.ext","to":"new/path.ext"}, one for each file '
    "that should be renamed or moved.\n"
    'Rules: "from" must be exactly a path from the list. "to" is the new relative path, folders separated by "/". '
    "Only include files that change. Never invent files, never use '..' or absolute paths, never give two files "
    "the same \"to\", and keep file extensions unless the user says otherwise. No prose, no code fences."
)

USE_COLOR = sys.stdout.isatty()
if os.name == "nt":
    os.system("")  # enables ANSI colors in recent Windows terminals


def c(code, text):
    return "\033[%sm%s\033[0m" % (code, text) if USE_COLOR else text


# ---------------------------------------------------------------- config
def setup():
    print(c("1", "Connect an AI model"))
    names = list(PRESETS)
    for i, n in enumerate(names, 1):
        print("  %d) %s" % (i, n))
    pick = input("Provider [1]: ").strip() or "1"
    name = names[int(pick) - 1] if pick.isdigit() and 0 < int(pick) <= len(names) else pick
    if name not in PRESETS:
        sys.exit("Unknown provider.")
    p = PRESETS[name]
    url = input("API base URL [%s]: " % p["url"]).strip() or p["url"]
    model = input("Model name%s: " % (" [%s]" % p["model"] if p["model"] else "")).strip() or p["model"]
    cfg = {"provider": name, "url": url, "model": model}
    if p["key"]:
        key = getpass.getpass("API key (input hidden, Enter to use $%s instead): " % p["key"]).strip()
        if key and input("Save the key in %s? [y/N] " % CONF).strip().lower() == "y":
            cfg["key"] = key
        elif key:
            cfg["session_key"] = key
    CONF.write_text(json.dumps({k: v for k, v in cfg.items() if k != "session_key"}, indent=2))
    try:
        CONF.chmod(0o600)
    except OSError:
        pass
    return cfg


def load_config():
    cfg = {}
    if CONF.exists():
        try:
            cfg = json.loads(CONF.read_text())
        except ValueError:
            pass
    cfg["provider"] = os.environ.get("TIDY_PROVIDER", cfg.get("provider", ""))
    cfg["url"] = os.environ.get("TIDY_URL", cfg.get("url", ""))
    cfg["model"] = os.environ.get("TIDY_MODEL", cfg.get("model", ""))
    if cfg["provider"] not in PRESETS or not cfg["model"]:
        return setup()
    cfg["url"] = cfg["url"] or PRESETS[cfg["provider"]]["url"]
    return cfg


def api_key(cfg):
    p = PRESETS[cfg["provider"]]
    return (os.environ.get("TIDY_KEY") or cfg.get("session_key") or cfg.get("key")
            or (os.environ.get(p["key"]) if p["key"] else "") or "")


# ---------------------------------------------------------------- AI call
def post(url, headers, body):
    headers = dict(headers, **{"content-type": "application/json", "user-agent": "tidy/1.0"})
    req = urllib.request.Request(url, json.dumps(body).encode(), headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError("HTTP %d: %s" % (e.code, e.read().decode(errors="replace")[:300]))
    except urllib.error.URLError as e:
        raise RuntimeError("Can't reach %s (%s)" % (url, e.reason))


def ask_ai(cfg, text):
    p, key, base = PRESETS[cfg["provider"]], api_key(cfg), cfg["url"].rstrip("/")
    if p["key"] and not key:
        raise RuntimeError("No API key. Set $%s, or run /config." % p["key"])
    if p["fmt"] == "anthropic":
        data = post(base + "/v1/messages",
                    {"x-api-key": key, "anthropic-version": "2023-06-01"},
                    {"model": cfg["model"], "max_tokens": 8000, "system": SYSTEM,
                     "messages": [{"role": "user", "content": text}]})
        return "".join(b.get("text", "") for b in data.get("content", []))
    headers = {"authorization": "Bearer " + key} if key else {}
    data = post(base + "/chat/completions", headers,
                {"model": cfg["model"], "temperature": 0,
                 "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}]})
    return data["choices"][0]["message"]["content"]


def parse_moves(text):
    a, b = text.find("["), text.rfind("]")
    if a < 0 or b < a:
        raise RuntimeError("The model didn't return a list of changes. Try rephrasing.")
    try:
        return json.loads(text[a:b + 1])
    except ValueError:
        raise RuntimeError("The model's reply wasn't valid JSON. Try again.")


# ---------------------------------------------------------------- files
def list_files(root, recursive):
    out = []
    if recursive:
        for d, dirs, names in os.walk(root):
            dirs[:] = sorted(x for x in dirs if not x.startswith("."))
            for n in sorted(names):
                if not n.startswith("."):
                    out.append(Path(d) / n)
    else:
        out = sorted(x for x in root.iterdir() if x.is_file() and not x.name.startswith("."))
    res = []
    for f in out:
        st = f.stat()
        res.append({"path": f.relative_to(root).as_posix(), "size": st.st_size,
                    "mod": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d")})
    return res


def fmt_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0


def make_plan(cfg, files, instr):
    known = {f["path"] for f in files}
    raw = []
    for i in range(0, len(files), 200):
        print(c("2", "  asking the model… (%d/%d files)" % (min(i + 200, len(files)), len(files))))
        listing = "\n".join("%s | %s | %s" % (f["path"], fmt_size(f["size"]), f["mod"]) for f in files[i:i + 200])
        raw += parse_moves(ask_ai(cfg, "Instruction: %s\n\nFiles:\n%s" % (instr, listing)))
    plan, moving = [], set()
    for m in raw:
        if not isinstance(m, dict) or not isinstance(m.get("from"), str) or not isinstance(m.get("to"), str):
            continue
        src, dst = m["from"], m["to"].replace("\\", "/").strip().lstrip("/")
        if src not in known or src == dst:
            continue
        parts = dst.split("/")
        note = "invalid path" if (not dst or any(x in ("", ".", "..") for x in parts)) else ""
        plan.append({"from": src, "to": dst, "note": note, "on": not note})
        moving.add(src)
    counts = {}
    for p in plan:
        if not p["note"]:
            counts[p["to"]] = counts.get(p["to"], 0) + 1
    for p in plan:
        if p["note"]:
            continue
        if counts[p["to"]] > 1:
            p["note"] = "same name as another change"
        elif p["to"] in known and p["to"] not in moving:
            p["note"] = "a file with this name already exists"
        p["on"] = not p["note"]
    return plan


def show_plan(plan):
    print()
    w = len(str(len(plan)))
    for i, p in enumerate(plan, 1):
        mark = c("32", "✓") if p["on"] else c("31", "✗")
        line = "%s %*d  %s  →  %s" % (mark, w, i, p["from"], c("32", p["to"]) if p["on"] else p["to"])
        print(line + (c("33", "   (%s)" % p["note"]) if p["note"] else ""))
    print("\n%d of %d changes selected." % (sum(p["on"] for p in plan), len(plan)))


def move_file(root, src, dst):
    s, d = (root / src).resolve(), (root / dst).resolve()
    if root.resolve() not in d.parents:
        raise RuntimeError("target is outside the folder")
    if d.exists():
        raise RuntimeError("target already exists")
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(s), str(d))


def prune_empty(root, path):
    p = (root / path).parent
    while p != root and p.exists() and not any(p.iterdir()):
        p.rmdir()
        p = p.parent


# ---------------------------------------------------------------- main loop
HELP = """Type what you want done, in plain language. For example:
  rename photos to YYYY-MM-DD_description using the modified date
  put PDFs in Documents, images in Pictures, everything else in Misc
  replace spaces with dashes and lowercase everything

After the preview: y = apply, n = cancel, 'except 2,5' = skip those,
or type a tweak (e.g. 'also keep the original extension case') to re-plan.

Commands:  /folder <path>   /recursive on|off   /files   /undo   /config   /help   /quit"""


def main():
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").expanduser().resolve()
    if not root.is_dir():
        sys.exit("Not a folder: %s" % root)
    print(c("1", "Tidy") + " - tell it how to rename and arrange your files.  /help for tips.")
    cfg = load_config()
    recursive, undo_stack = False, []
    print("Folder: %s   Model: %s (%s)" % (root, cfg["model"], cfg["provider"]))
    print("Only file names, sizes and dates are sent to the model, never file contents.\n")

    while True:
        try:
            line = input(c("36", "tidy> ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        if line.startswith("/"):
            cmd, _, arg = line.partition(" ")
            arg = arg.strip()
            if cmd in ("/quit", "/exit", "/q"):
                return
            elif cmd == "/help":
                print(HELP)
            elif cmd == "/config":
                cfg = setup()
            elif cmd == "/files":
                fs = list_files(root, recursive)
                print("\n".join(f["path"] for f in fs) or "(no files)")
                print("%d files%s" % (len(fs), "" if recursive else " (top level only; /recursive on to include subfolders)"))
            elif cmd == "/recursive":
                recursive = arg.lower() in ("on", "yes", "true", "1")
                print("Subfolders: %s" % ("included" if recursive else "not included"))
            elif cmd == "/folder":
                new = Path(arg).expanduser().resolve()
                if new.is_dir():
                    root, undo_stack = new, []
                    print("Folder: %s" % root)
                else:
                    print(c("31", "Not a folder: %s" % new))
            elif cmd == "/undo":
                if not undo_stack:
                    print("Nothing to undo.")
                    continue
                batch, ok, bad = undo_stack.pop(), 0, 0
                for src, dst in reversed(batch):
                    try:
                        move_file(root, dst, src)
                        prune_empty(root, dst)
                        ok += 1
                    except Exception as e:
                        bad += 1
                        print(c("31", "  couldn't restore %s: %s" % (dst, e)))
                print("%d restored%s." % (ok, ", %d failed" % bad if bad else ""))
            else:
                print("Unknown command. /help lists them.")
            continue

        files = list_files(root, recursive)
        if not files:
            print("No files to work on here.")
            continue
        instr = line
        try:
            plan = make_plan(cfg, files, instr)
            while True:
                if not plan:
                    print("The model found nothing to change for that.")
                    break
                show_plan(plan)
                ans = input("Apply? [y]es / [n]o / except N,N / or type a tweak: ").strip()
                low = ans.lower()
                if low in ("y", "yes"):
                    done = []
                    for p in plan:
                        if p["on"]:
                            try:
                                move_file(root, p["from"], p["to"])
                                done.append((p["from"], p["to"]))
                            except Exception as e:
                                print(c("31", "  failed: %s (%s)" % (p["from"], e)))
                    if done:
                        undo_stack.append(done)
                    print(c("32", "%d changed." % len(done)) + ("  /undo reverts it." if done else ""))
                    break
                if low in ("", "n", "no"):
                    print("Cancelled. Nothing was changed.")
                    break
                if low.startswith("except"):
                    for tok in ans[6:].replace(",", " ").split():
                        if tok.isdigit() and 0 < int(tok) <= len(plan):
                            plan[int(tok) - 1]["on"] = False
                    continue
                instr += ". Also: " + ans
                plan = make_plan(cfg, files, instr)
        except (RuntimeError, KeyError, IndexError) as e:
            print(c("31", "Error: %s" % e))
        except KeyboardInterrupt:
            print("\nCancelled.")


if __name__ == "__main__":
    main()
