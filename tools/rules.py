#!/usr/bin/env python3
"""ob-plugin-fb-message-guardian-rules — la « personnalité » des réponses Facebook.
Gère le carnet de règles ~/.config/open-bot/fb-message-guardian-rules.json, consulté par le
scan AVANT le comportement par défaut. Premier match (priorité croissante) gagne :
- reply         → réponse personnalisée ({expediteur} remplacé) ; en direct si les portes
                  globales (mode d'envoi, liste blanche, heures, plafond) le permettent,
                  sinon brouillon à approuver ;
- notify        → notification personnalisée au lieu d'une réponse ;
- save_photo_to → nom d'un projet open-bot : les photos du fil sont sauvegardées dans
                  ~/workspace/<projet>/fb-guardian/ par l'agent (tâche route_photo)."""
import argparse, json, os, sys
from datetime import datetime

RULES_PATH = os.path.expanduser("~/.config/open-bot/fb-message-guardian-rules.json")
PHOTO_HINTS = ("photo", "\U0001F4F7")  # aperçus Messenger : « a envoyé une photo » / 📷

def out(o, c=0):
    print(json.dumps(o, ensure_ascii=False)); sys.exit(c)

def load():
    try:
        with open(RULES_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save(rs):
    os.makedirs(os.path.dirname(RULES_PATH), exist_ok=True)
    tmp = RULES_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rs, f, ensure_ascii=False, indent=1)
    os.replace(tmp, RULES_PATH)

def preview_has_photo(p):
    return any(h in (p or "").lower() for h in PHOTO_HINTS)

def match_rule(rules, sender, preview, photo=None):
    """Première règle active avec au moins une action qui matche, ou None."""
    pl = (preview or "").lower()
    sl = (sender or "").lower()
    if photo is None:
        photo = preview_has_photo(preview)
    active = [r for r in rules
              if r.get("enabled", True) and (r.get("reply") or r.get("notify") or r.get("save_photo_to"))]
    for r in sorted(active, key=lambda x: (int(x.get("priority", 100)),)):
        rs = (r.get("sender") or "").strip().lower()
        if rs and rs not in sl:
            continue
        words = [w.strip().lower() for w in (r.get("contains") or []) if w and w.strip()]
        if words and not any(w in pl for w in words):
            continue
        if r.get("photo") and not photo:
            continue
        return r
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--add", action="store_true")
    ap.add_argument("--sender", help="expéditeur (sous-chaîne, vide = tout le monde)")
    ap.add_argument("--contains", help="mots-clés séparés par des virgules (aucun = n'importe quel texte)")
    ap.add_argument("--photo", action="store_true", help="uniquement si l'aperçu suggère une photo")
    ap.add_argument("--reply", help="réponse personnalisée ({expediteur} est remplacé)")
    ap.add_argument("--notify", help="texte de notification personnalisé (au lieu d'une réponse)")
    ap.add_argument("--save-photo", dest="save_photo", metavar="PROJET",
                    help="projet open-bot où router les photos du fil")
    ap.add_argument("--priority", type=int, default=100)
    ap.add_argument("--enable", metavar="ID"); ap.add_argument("--disable", metavar="ID")
    ap.add_argument("--remove", metavar="ID")
    ap.add_argument("--test", action="store_true", help="montre la règle qui matcherait")
    ap.add_argument("--text", help="aperçu simulé (pour --test)")
    args = ap.parse_args()
    rs = load()

    if args.list:
        out({"status": "ok", "rules_path": RULES_PATH, "rules": rs})

    if args.enable or args.disable or args.remove:
        rid = args.enable or args.disable or args.remove
        e = next((x for x in rs if x.get("id") == rid), None)
        if not e:
            out({"status": "error", "detail": "introuvable : " + rid}, 2)
        if args.remove:
            rs.remove(e)
            save(rs)
            out({"status": "removed", "id": rid})
        e["enabled"] = bool(args.enable)
        save(rs)
        out({"status": "ok", "id": rid, "enabled": e["enabled"]})

    if args.add:
        if not (args.reply or args.notify or args.save_photo):
            out({"status": "error", "detail": "au moins une action requise : --reply, --notify ou --save-photo"}, 2)
        rule = {"id": "r-" + os.urandom(2).hex(), "enabled": True, "priority": args.priority,
                "sender": (args.sender or "").strip(),
                "contains": [w.strip() for w in (args.contains or "").split(",") if w.strip()],
                "photo": bool(args.photo),
                "reply": args.reply or "", "notify": args.notify or "",
                "save_photo_to": (args.save_photo or "").strip(),
                "created": datetime.now().isoformat(timespec="seconds")}
        rs.append(rule)
        save(rs)
        out({"status": "added", "rule": rule})

    if args.test:
        r = match_rule(rs, args.sender or "", args.text or "")
        out({"status": "ok", "matched": bool(r), "rule": r,
             "detail": None if r else "aucune règle ne matche : comportement par défaut (mots-clés/modèle/IA)"})

    out({"status": "error",
         "detail": "rien à faire : --list / --add / --enable ID / --disable ID / --remove ID / --test --text \"...\""}, 2)

if __name__ == "__main__":
    main()
