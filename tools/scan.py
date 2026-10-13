#!/usr/bin/env python3
"""ob-plugin-fb-message-guardian-scan — lit la boîte Messenger via le DOM (ob-page),
détecte la session fermée, et produit un plan d'action JSON. Zéro token IA."""
import argparse, json, os, re, subprocess, sys, time
from datetime import datetime

PLUGIN_ID = "fb-message-guardian"
SETTINGS_PATH = os.path.expanduser("~/.config/open-bot/plugin-%s.json" % PLUGIN_ID)
RULES_PATH = os.path.expanduser("~/.config/open-bot/fb-message-guardian-rules.json")
STATE_DIR = os.path.expanduser("~/.open-bot/" + PLUGIN_ID)
STATE_PATH = os.path.join(STATE_DIR, "state.json")
INBOX_URL = "https://www.messenger.com/"
LOGIN_MARKERS = ("se connecter", "mot de passe", "adresse électronique", "find your account",
                 "s'inscrire", "créer un nouveau compte")
E2EE_PLACEHOLDER = "chiffrement"
PHOTO_HINTS = ("photo", "\U0001F4F7")  # aperçus Messenger : « a envoyé une photo » / 📷

def out(obj, code=0):
    print(json.dumps(obj, ensure_ascii=False))
    sys.exit(code)

def sh(cmd, timeout=120):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None

def page_read(session):
    p = sh(["ob-page", "--session", session, "read"])
    if not p or p.returncode != 0:
        return None
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        return None

def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_PATH)

def inbox_visible(page):
    return any(re.search(r"discussions?\s*·", (e.get("label") or "").lower())
               for e in page.get("elements", []))

def detect_login(page):
    labels = " | ".join(e.get("label", "") for e in page.get("elements", [])).lower()
    has_inbox = re.search(r"discussions\s*·", labels) is not None
    url = page.get("url", "")
    if has_inbox:
        return "ok", has_inbox
    if "login" in url or any(m in labels for m in LOGIN_MARKERS):
        return "not_logged_in", has_inbox
    if page.get("title", "").lower().startswith("messenger") and not has_inbox:
        return "dom_changed", has_inbox
    return "needs_user", has_inbox

def load_rules():
    rules = load_json(RULES_PATH, [])
    return [r for r in rules
            if r.get("enabled", True) and (r.get("reply") or r.get("notify") or r.get("save_photo_to"))]

def match_rule(rules, sender, preview):
    """La « personnalité » des réponses : première règle qui matche (priorité croissante), ou None."""
    pl = (preview or "").lower()
    sl = (sender or "").lower()
    has_photo = any(h in pl for h in PHOTO_HINTS)
    for r in sorted(rules, key=lambda x: (int(x.get("priority", 100)),)):
        rs = (r.get("sender") or "").strip().lower()
        if rs and rs not in sl:
            continue
        words = [w.strip().lower() for w in (r.get("contains") or []) if w and w.strip()]
        if words and not any(w in pl for w in words):
            continue
        if r.get("photo") and not has_photo:
            continue
        return r
    return None

def parse_rows(page):
    """Retourne [{name, preview, time, own, group}] à partir des paires lien + 'Autres options pour X'."""
    els = page.get("elements", [])
    rows = []
    last_link = None
    for e in els:
        label = (e.get("label") or "").strip()
        if e.get("role") == "link" and label:
            last_link = label
        elif e.get("role") == "button" and label.startswith("Autres options pour "):
            name = label[len("Autres options pour "):].strip()
            if last_link and name.lower() in last_link.lower():
                rows.append(parse_row(last_link, name))
            last_link = None
    return rows

TIMEY = re.compile(r"^(?:(?:il y a\s+)?[\dh]|hier|auj|lun|mar|mer|jeu|ven|sam|dim)", re.I)

def norm_preview(p):
    """Clé stable d'un aperçu : minuscule, queues temporelles volatiles retirées."""
    s = (p or "").lower()
    s = re.sub(r"\s*·\s*(?:il y a\s+)?[\dh]+\s*(?:min|h|j|sec|heures?|minutes?|jours?|secondes?)\.?$", "", s)
    s = re.sub(r"\s+", " ", s).strip(" ·.")
    return s

def parse_row(label, name):
    s, group, online = label, False, False
    if s.startswith("Discussion de groupe : "):
        group, s = True, s[len("Discussion de groupe : "):]
    for _ in range(3):
        before = s
        m = re.match(r"^(?:En ligne|Actif(?:·?ve)?)\s+", s)
        if m:
            online, s = True, s[m.end():]
        m = re.match(r"^il y a\s+(?:\d+|une?|quelques?)\s*(?:seconde?s?|min(?:ute)?s?|h(?:eures?)?|j(?:ours?)?)\s+", s)
        if m:
            s = s[m.end():]
        if s.startswith(name + " "):
            s = s[len(name) + 1:]
        elif s == name:
            s = ""
        if s == before:
            break
    t = None
    parts = s.rsplit(" · ", 1)
    if len(parts) == 2 and 1 <= len(parts[1]) <= 24 and not parts[1].endswith("."):
        tail = parts[1].strip()
        s = parts[0].strip()
        if TIMEY.match(tail):
            t = tail  # horodatage (« 8 h », « hier ») : hors de l'aperçu pour un dédoublonnage stable
        # sinon : invite d'action (« Répondre ? ») : retirée de l'aperçu, pas un horodatage
    own = s.startswith("Vous: ")
    if own:
        s = s[len("Vous: "):]
    return {"name": name, "preview": s.strip(), "time": t, "own": own, "group": group}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument("--force", action="store_true", help="rescan immédiat, ignore l'intervalle")
    ap.add_argument("--interval-override-seconds", type=float, default=None,
                    help="remplace l'intervalle configuré (utilisé par le watcher)")
    ap.add_argument("--thread", metavar="NOM", help="lire les derniers messages d'une conversation")
    args = ap.parse_args()

    settings = load_json(SETTINGS_PATH, {}).get("settings", {})
    state = load_json(STATE_PATH, {"conversations": {}, "sends": {}, "sent_log": [], "pending": [],
                                   "logout_notified_on": None, "last_scan": 0})
    now = time.time()

    if args.thread:
        out(read_thread(args.session, args.thread))

    interval_min = float(settings.get("check_interval_minutes", 15) or 15)
    min_age = interval_min * 60
    if args.interval_override_seconds is not None:
        min_age = max(60.0, args.interval_override_seconds)
    if not args.force and state.get("last_scan") and now - state["last_scan"] < min_age - 30:
        out({"status": "too_soon", "next_due_in_min": round((min_age - (now - state["last_scan"])) / 60, 1)})

    page = page_read(args.session)
    if page is None or "messenger.com" not in page.get("url", "") or not inbox_visible(page):
        nav = sh(["ob-nav", "--session", args.session, "--goal", INBOX_URL])
        navtxt = (nav.stdout or "") + (nav.stderr or "") if nav else ""
        if '"status":"needs_user"' in navtxt or '"status":"blocked"' in navtxt:
            out({"status": "needs_user", "detail": "navigation refusée, écran à rendre à l'utilisateur"}, 3)
        time.sleep(4)
        page = page_read(args.session)
        if page is None:
            out({"status": "error", "detail": "lecture impossible après navigation"}, 4)

    status, has_inbox = detect_login(page)
    if status != "ok":
        state["last_scan"] = now
        save_state(state)
        today = datetime.now().strftime("%Y-%m-%d")
        if status == "not_logged_in" and settings.get("notify_logout", "true") == "true":
            if state.get("logout_notified_on") != today:
                state["logout_notified_on"] = today
                save_state(state)
                notify("Session Facebook fermée", "Reconnecte-toi dans le navigateur du bureau pour réactiver le gardien.")
                out({"status": "not_logged_in", "notified": True, "detail": "utilisateur notifié"}, 3)
            out({"status": "not_logged_in", "notified": False, "detail": "rappel déjà envoyé aujourd'hui"}, 3)
        out({"status": status, "detail": "boîte Messenger illisible via le DOM"}, 3)

    rows = parse_rows(page)
    if not rows:
        out({"status": "dom_changed", "detail": "aucune conversation lisible : sélecteurs à réparer"}, 3)

    keywords = [k.strip().lower() for k in (settings.get("trigger_keywords") or "").split(",") if k.strip()]
    whitelist = [w.strip().lower() for w in (settings.get("whitelist") or "").split(",") if w.strip()]
    mode = settings.get("reply_mode", "hybride")
    rules = load_rules()
    cap = int(float(settings.get("daily_reply_cap", 20) or 20))
    h_start = int(float(settings.get("active_hours_start", 8) or 0))
    h_end = int(float(settings.get("active_hours_end", 21) or 23))
    hour = datetime.now().hour
    hours_ok = (h_start <= hour <= h_end) if h_start <= h_end else (hour >= h_start or hour <= h_end)
    today = datetime.now().strftime("%Y-%m-%d")
    sent_today = state.get("sends", {}).get(today, 0)

    plan = []
    for r in rows:
        if r["group"] or r["own"] or not r["preview"]:
            continue
        prev = r["preview"]
        if not prev or E2EE_PLACEHOLDER in prev[:80]:
            continue
        known = state["conversations"].get(r["name"], {})
        pkey = norm_preview(prev)
        known_key = known.get("pkey") or norm_preview(known.get("preview", ""))
        if known_key == pkey:
            # même message : rafraîchit l'horodatage vu, ne replanifie PAS
            known.update({"time": r["time"], "seen": today})
            continue
        if now - float(known.get("planned") or 0) < max(600.0, min_age):
            # l'aperçu a bougé mais le fil vient d'être planifié (bruit DOM, badge, aperçu
            # qui se stabilise) : absorbe sans replanifier — le prochain passage confira.
            known.update({"preview": prev, "pkey": pkey, "time": r["time"], "seen": today})
            continue
        state["conversations"][r["name"]] = {"preview": prev, "pkey": pkey, "time": r["time"],
                                             "seen": today, "planned": now}
        matched = next((k for k in keywords if k in prev.lower()), None)
        whitelisted = (not whitelist) or any(w in r["name"].lower() for w in whitelist)
        entry = {"sender": r["name"], "preview": prev[:160], "time": r["time"], "pkey": pkey,
                 "matched_keyword": matched, "action": None, "reason": None}

        # 1. La personnalité des réponses : une règle du carnet remplace le défaut.
        rule = match_rule(rules, r["name"], prev)
        if rule:
            entry["rule_id"] = rule.get("id")
            if rule.get("save_photo_to"):
                entry["route_photo"] = rule["save_photo_to"]
            if rule.get("reply"):
                entry["text"] = rule["reply"]
                # mêmes portes DURES que les réponses modèles : heures, plafond, mode, liste blanche
                if not hours_ok:
                    entry["action"], entry["reason"] = "notify", "hors heures actives"
                elif sent_today >= cap:
                    entry["action"], entry["reason"] = "notify", "plafond quotidien atteint (%d)" % cap
                elif settings.get("send_mode", "approbation") != "direct":
                    entry["action"], entry["reason"] = "draft", "mode approbation"
                elif not whitelisted:
                    entry["action"], entry["reason"] = "draft", "expéditeur hors liste blanche"
                else:
                    entry["action"] = "send:template"
            else:
                entry["action"] = "notify"
                entry["notify_text"] = rule.get("notify") or ""
        # 2. Sinon, le comportement par défaut (mots-clés / modèle / IA).
        else:
            if mode == "hybride":
                action = "template" if matched else "ai"
            elif mode == "ia":
                action = "ai"
            else:
                action = "template" if matched else "notify"
            if action in ("template", "ai"):
                if not hours_ok:
                    action, entry["reason"] = "notify", "hors heures actives"
                elif sent_today >= cap:
                    action, entry["reason"] = "notify", "plafond quotidien atteint (%d)" % cap
                elif settings.get("send_mode", "approbation") != "direct":
                    action = "draft"
                    entry["reason"] = "mode approbation"
                elif not whitelisted:
                    action = "draft"
                    entry["reason"] = "expéditeur hors liste blanche"
                else:
                    action = "send:" + action
            entry["action"] = action
        plan.append(entry)

    state["last_scan"] = now
    await_ai = [p for p in state.get("pending", []) if p.get("status") == "await_ai"]
    save_state(state)
    out({"status": "ok", "unread_folders": folder_counts(page), "sent_today": sent_today,
         "cap": cap, "hours_ok": hours_ok, "new": plan,
         "await_ai": len(await_ai), "pending": len(state.get("pending", []))})

def folder_counts(page):
    counts = {}
    for e in page.get("elements", []):
        m = re.match(r"^(Discussions|Marketplace|Demandes)\s*·\s*(\d+)\s*non lus", e.get("label", ""))
        if m:
            counts[m.group(1)] = int(m.group(2))
    return counts

def read_thread(session, name):
    """Clique la conversation NOM et extrait les derniers messages lisibles (pour rédaction IA)."""
    page = page_read(session)
    if page is None or "messenger.com" not in page.get("url", "") or not inbox_visible(page):
        sh(["ob-nav", "--session", session, "--goal", INBOX_URL]); time.sleep(4)
        page = page_read(session)
        if page is None:
            return {"status": "error", "detail": "page illisible"}
    target = None
    for e in page.get("elements", []):
        if e.get("role") == "link" and name.lower() in (e.get("label") or "").lower():
            target = e["id"]; break
    if not target:
        return {"status": "error", "detail": "conversation introuvable : " + name}
    sh(["ob-page", "--session", session, "click", target]); time.sleep(4)
    page = page_read(session)
    msgs = []
    for e in (page or {}).get("elements", []):
        m = re.match(r"^Saisir, Message envoyé à (.+?) par (.+?)(?: : (.*))?$", e.get("label", ""))
        if m:
            msgs.append({"when": m.group(1), "author": m.group(2), "text": (m.group(3) or "").strip()})
    return {"status": "ok", "thread": name, "messages": msgs[-12:]}

def notify(title, body):
    sh(["ob-plugin", "notify", PLUGIN_ID, title, body])

if __name__ == "__main__":
    main()
