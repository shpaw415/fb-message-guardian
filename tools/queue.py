#!/usr/bin/env python3
"""ob-plugin-fb-message-guardian-queue — gère la file du gardien.
--list : liste l'attente.  --approve ID : marque approuvé (l'envoi reste un appel send --pending).
--set-text ID TEXTE : dépose la réponse rédigée (l'entrée passe en await_approval, brouillon à approuver).
--done ID : retire une entrée traitée ou obsolète (re-capture d'un fil déjà couvert)."""
import argparse, json, os, sys
from datetime import datetime

STATE_PATH = os.path.expanduser("~/.open-bot/fb-message-guardian/state.json")

def out(o, c=0):
    print(json.dumps(o, ensure_ascii=False)); sys.exit(c)

def load():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        d = {}
    d.setdefault("pending", [])
    return d

def save(d):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_PATH)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--sender")
    ap.add_argument("--text")
    ap.add_argument("--approve", metavar="ID")
    ap.add_argument("--set-text", dest="set_text", nargs=2, metavar=("ID", "TEXTE"),
                    help="dépose la réponse rédigée : l'entrée devient un brouillon à approuver")
    ap.add_argument("--done", metavar="ID", help="retire une entrée traitée ou obsolète")
    ap.add_argument("--skip", nargs="+", metavar=("ID", "RAISON"),
                    help="marque une entrée ignorée ; raison facultative (2-3 mots max)")
    args = ap.parse_args()

    d = load()
    if args.list:
        open_e = [p for p in d["pending"] if p.get("status") in ("await_ai", "await_approval")]
        out({"status": "ok", "open": open_e, "pending": d["pending"]})
    if args.done:
        n = len(d["pending"])
        d["pending"] = [p for p in d["pending"] if p["id"] != args.done]
        save(d)
        out({"status": "done", "id": args.done, "removed": n - len(d["pending"])})
    if args.skip:
        rid, reason = args.skip[0], (args.skip[1] if len(args.skip) > 1 else "ignoré")
        e = next((p for p in d["pending"] if p["id"] == rid), None)
        if not e: out({"status": "error", "detail": "introuvable : " + rid}, 2)
        e["status"] = "skipped"
        e["skipped_at"] = datetime.now().isoformat(timespec="seconds")
        e["reason"] = reason
        save(d)
        out({"status": "skipped", "id": rid})
    if args.set_text:
        rid, text = args.set_text
        e = next((p for p in d["pending"] if p["id"] == rid), None)
        if not e: out({"status": "error", "detail": "introuvable : " + rid}, 2)
        e["text"] = text
        e["status"] = "await_approval"
        e["drafted_at"] = datetime.now().isoformat(timespec="seconds")
        save(d)
        out({"status": "drafted", "id": rid, "sender": e.get("sender"),
             "hint": "notifie l'utilisateur : brouillon %s pour %s — approuve par « envoie %s »"
                     % (rid, e.get("sender"), rid)})
    if args.approve:
        e = next((p for p in d["pending"] if p["id"] == args.approve), None)
        if not e: out({"status": "error", "detail": "introuvable : " + args.approve}, 2)
        e["approved"] = True
        save(d)
        out({"status": "approved", "id": e["id"], "sender": e["sender"],
             "hint": "envoyer avec ob-plugin-fb-message-guardian-send --pending " + e["id"]})
    if not args.sender or not args.text:
        out({"status": "error", "detail": "--sender et --text requis (ou --list / --approve / --set-text / --done ID)"}, 2)
    pid = "p-" + os.urandom(2).hex()
    entry = {"id": pid, "sender": args.sender, "text": args.text, "status": "await_approval",
             "created": datetime.now().isoformat(timespec="seconds")}
    d["pending"].append(entry)
    save(d)
    out({"status": "queued", "id": pid, "sender": args.sender,
         "hint": "approuver avec : guardian oui %s" % pid})

if __name__ == "__main__":
    main()
