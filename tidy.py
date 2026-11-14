#!/usr/bin/env python3
"""Tidy - rename and organize files by telling an AI what you want.

Usage:  python tidy.py [-r] [folder]
Needs Python 3.8+ and nothing else (standard library only).
"""
import getpass
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

CONF = Path.home() / ".tidy.json"
CHUNK = 200                                   # files sent to the model per request
RETRY_CODES = (429, 500, 502, 503, 504, 529)  # temporary server errors worth retrying
CASE_INSENSITIVE = os.name == "nt" or sys.platform == "darwin"
# New names are kept safe for Windows on every system, so folders stay portable
# (USB drives, synced folders...). Relax these three lines if you don't want that.
BAD_CHARS = re.compile(r'[<>:"|?*\x00-\x1f\x7f]')
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
RESERVED = {"CON", "PRN", "AUX", "NUL"} | {"COM%d" % i for i in range(1, 10)} | {"LPT%d" % i for i in range(1, 10)}

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
    "the same \"to\", and keep file extensions unless the user says otherwise. "
    'Names must not contain any of < > : " | ? * and must not end with a dot or space. '
    "No prose, no code fences."
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
class BadReply(RuntimeError):
    """The model answered, but not with a usable list of changes."""


def retry_delay(err, attempt):
    try:
        return min(60, max(1, int(err.headers.get("Retry-After", ""))))
    except (TypeError, ValueError):
        return 2 ** (attempt + 1)  # 2s, 4s, 8s


def post(url, headers, body, retries=3):
    headers = dict(headers, **{"content-type": "application/json", "user-agent": "tidy/1.0"})
    data = json.dumps(body).encode()
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data, headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            text = e.read().decode(errors="replace")[:300]
            if e.code in RETRY_CODES and attempt < retries:
                wait = retry_delay(e, attempt)
                print(c("2", "  server busy (HTTP %d), retrying in %ds…" % (e.code, wait)))
                time.sleep(wait)
                continue
            raise RuntimeError("HTTP %d: %s" % (e.code, text))
        except urllib.error.URLError as e:
            raise RuntimeError("Can't reach %s (%s)" % (url, e.reason))
        except (OSError, ValueError) as e:  # timeouts, dropped connections, replies that aren't JSON
            raise RuntimeError("Bad response from %s (%s)" % (url, e))


def ask_ai(cfg, text):
    p, key, base = PRESETS[cfg["provider"]], api_key(cfg), cfg["url"].rstrip("/")
    if p["key"] and not key:
        raise RuntimeError("No API key. Set $%s, or run /config." % p["key"])
    if p["fmt"] == "anthropic":
        data = post(base + "/v1/messages",
                    {"x-api-key": key, "anthropic-version": "2023-06-01"},
                    {"model": cfg["model"], "max_tokens": 8000, "system": SYSTEM,
                     "messages": [{"role": "user", "content": text}]})
        try:
            return "".join(b.get("text", "") for b in data.get("content", []))
        except (AttributeError, TypeError):
            raise RuntimeError("Unexpected reply format from the model: %s" % str(data)[:200])
    headers = {"authorization": "Bearer " + key} if key else {}
    data = post(base + "/chat/completions", headers,
                {"model": cfg["model"], "temperature": 0,
                 "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}]})
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("Unexpected reply format from the model: %s" % str(data)[:200])


def parse_moves(text):
    text = text or ""
    if "</think>" in text:  # reasoning models: ignore the thinking part
        text = text.rsplit("</think>", 1)[1]
    dec, i = json.JSONDecoder(), text.find("[")
    while i >= 0:
        try:
            val, _ = dec.raw_decode(text, i)
            if isinstance(val, list) and (not val or any(isinstance(x, dict) for x in val)):
                return val
        except ValueError:
            pass
        i = text.find("[", i + 1)
    raise BadReply("The model didn't return a usable list of changes. Try rephrasing.")


def ask_moves(cfg, instr, chunk):
    """Ask the model about one batch of files. Retries once on an unreadable reply,
    then splits big batches in half (a reply that was cut off is usually too long)."""
    listing = "\n".join("%s | %s | %s" % (f["path"], fmt_size(f["size"]), f["mod"]) for f in chunk)
    prompt = "Instruction: %s\n\nFiles:\n%s" % (instr, listing)
    for attempt in range(2):
        try:
            return parse_moves(ask_ai(cfg, prompt))
        except BadReply:
            if attempt == 0:
                print(c("2", "  couldn't read the reply, trying again…"))
                prompt += "\n\nYour previous reply could not be parsed. Reply with ONLY the JSON array."
    if len(chunk) > 20:
        print(c("2", "  still unreadable, splitting the list in two…"))
        half = len(chunk) // 2
        return ask_moves(cfg, instr, chunk[:half]) + ask_moves(cfg, instr, chunk[half:])
    raise BadReply("The model didn't return a usable list of changes. Try rephrasing.")


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


def norm(path):
    """Comparison key for paths: case-insensitive where the file system is."""
    return path.lower() if CASE_INSENSITIVE else path


def clean_path(raw):
    """Make a path suggested by the model safe to create.
    Returns (path, note). path is "" when the suggestion can't be used; note says why,
    or says what was adjusted."""
    parts = raw.replace("\\", "/").strip().lstrip("/").split("/")
    out, adjusted = [], False
    for part in parts:
        fixed = BAD_CHARS.sub("_", part).strip().rstrip(". ")
        if fixed.split(".")[0].upper() in RESERVED:
            fixed = "_" + fixed
        if not fixed:
            return "", "invalid path"
        if len(fixed.encode("utf-8")) > 255:
            return "", "name too long"
        adjusted = adjusted or fixed != part
        out.append(fixed)
    return "/".join(out), ("name adjusted to be safe on all systems" if adjusted else "")


def make_plan(cfg, files, instr):
    known = {f["path"] for f in files}
    known_norm = {norm(p) for p in known}
    raw = []
    for i in range(0, len(files), CHUNK):
        print(c("2", "  asking the model… (%d/%d files)" % (min(i + CHUNK, len(files)), len(files))))
        raw += ask_moves(cfg, instr, files[i:i + CHUNK])
    plan, seen = [], set()
    for m in raw:
        if not isinstance(m, dict) or not isinstance(m.get("from"), str) or not isinstance(m.get("to"), str):
            continue
        src = m["from"]
        if src not in known or src in seen:  # unknown file, or the model listed it twice
            continue
        seen.add(src)
        dst, note = clean_path(m["to"])
        if not dst:
            plan.append({"from": src, "to": CONTROL.sub("_", m["to"]), "note": note, "on": False})
        elif dst != src:
            plan.append({"from": src, "to": dst, "note": note, "on": True})
    moving = {norm(p["from"]) for p in plan if p["on"]}
    counts = {}
    for p in plan:
        if p["on"]:
            counts[norm(p["to"])] = counts.get(norm(p["to"]), 0) + 1
    for p in plan:
        if not p["on"]:
            continue
        key, parts = norm(p["to"]), p["to"].split("/")
        if counts[key] > 1:
            p["note"], p["on"] = "same name as another change", False
        elif key in known_norm and key not in moving:
            p["note"], p["on"] = "a file with this name already exists", False
        else:
            for k in range(1, len(parts)):  # a plain file where a folder is needed?
                folder = "/".join(parts[:k])
                if norm(folder) in known_norm and norm(folder) not in moving:
                    p["note"], p["on"] = "a file named '%s' is in the way" % folder, False
                    break
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
    s, d = root / src, Path(os.path.normpath(str(root / dst)))
    root_real, parent_real = root.resolve(), d.parent.resolve()
    if parent_real != root_real and root_real not in parent_real.parents:
        raise RuntimeError("target is outside the folder")
    # d.exists() is also true for a case-only rename (photo.JPG -> photo.jpg) on
    # Windows/macOS, because both names are the same file; that case is allowed.
    if d.exists() and not (norm(src) == norm(dst) and s.exists() and os.path.samefile(str(s), str(d))):
        raise RuntimeError("target already exists")
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(s), str(d))


def temp_name(root):
    while True:
        name = ".tidy-tmp-" + os.urandom(4).hex()
        if not (root / name).exists():
            return name


def needs_temp(src, dst, sources):
    """True if dst (or a folder it needs) is currently occupied by a file that is itself about to move."""
    if norm(src) == norm(dst):
        return False  # case-only rename, done in place
    parts = dst.split("/")
    return any(norm("/".join(parts[:i])) in sources for i in range(1, len(parts) + 1))


def apply_moves(root, pairs):
    """Carry out (src, dst) moves. Chains (a->b, b->c), swaps and cycles work because files
    whose destination is still occupied are parked under a temporary name first.
    Returns (done, failed): done = [(src, dst)], failed = [(src, dst, reason)]."""
    sources = {norm(s) for s, _ in pairs}
    direct = [(s, d) for s, d in pairs if not needs_temp(s, d, sources)]
    via_temp = [(s, d) for s, d in pairs if needs_temp(s, d, sources)]
    done, failed, parked = [], [], []

    for s, d in via_temp:                      # 1. park the files that are in someone's way
        tmp = temp_name(root)
        try:
            move_file(root, s, tmp)
            parked.append((s, d, tmp))
        except Exception as e:
            failed.append((s, d, e))
    for s, d in direct:                        # 2. everything that can move straight away
        try:
            move_file(root, s, d)
            done.append((s, d))
        except Exception as e:
            failed.append((s, d, e))
    for s, d, tmp in parked:                   # 3. parked files to their final names
        try:
            move_file(root, tmp, d)
            done.append((s, d))
        except Exception as e:
            try:
                move_file(root, tmp, s)        # couldn't finish: put it back where it was
                failed.append((s, d, e))
            except Exception:
                failed.append((s, d, "%s; the file is now at %s" % (e, tmp)))
    for s, _ in done:
        prune_empty(root, s)
    return done, failed


def prune_empty(root, path):
    p = (root / path).parent
    try:
        while p != root and p.exists() and not any(p.iterdir()):
            p.rmdir()
            p = p.parent
    except OSError:
        pass


# ---------------------------------------------------------------- main loop
HELP = """Type what you want done, in plain language. For example:
  rename photos to YYYY-MM-DD_description using the modified date
  put PDFs in Documents, images in Pictures, everything else in Misc
  replace spaces with dashes and lowercase everything

After the preview: y = apply, n = cancel, 'except 2,5' = skip those,
or type a tweak (e.g. 'also keep the original extension case') to re-plan.

Commands:  /folder <path>   /recursive on|off   /files   /undo   /config   /help   /quit"""


def main():
    args = sys.argv[1:]
    recursive = "-r" in args or "--recursive" in args
    args = [a for a in args if a not in ("-r", "--recursive")]
    root = Path(args[0] if args else ".").expanduser().resolve()
    if not root.is_dir():
        sys.exit("Not a folder: %s" % root)
    print(c("1", "Tidy") + " - tell it how to rename and arrange your files.  /help for tips.")
    cfg = load_config()
    undo_stack = []
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
                batch = undo_stack.pop()
                ok, bad = apply_moves(root, [(dst, src) for src, dst in batch])
                for here, orig, why in bad:
                    print(c("31", "  couldn't restore %s: %s" % (here, why)))
                if bad:  # keep the ones that failed so /undo can be tried again
                    undo_stack.append([(orig, here) for here, orig, _ in bad])
                print("%d restored%s." % (len(ok), ", %d failed" % len(bad) if bad else ""))
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
                    done, failed = apply_moves(root, [(p["from"], p["to"]) for p in plan if p["on"]])
                    for src, _, why in failed:
                        print(c("31", "  failed: %s (%s)" % (src, why)))
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
