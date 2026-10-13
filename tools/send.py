#!/usr/bin/env python3
"""ob-plugin-fb-message-guardian-send — envoie une réponse dans une conversation Messenger.
Double porte : réglages (mode d'envoi, liste blanche, plafond, heures) vérifiés ici aussi.
Le tap se fait via ob-vnc (clic dans le composer, collage, Entrée) car le composer n'est pas
exposé dans l'arbre accessible retourné par ob-page."""
import argparse, json, os, re, subprocess, sys, time
from datetime import datetime

PLUGIN_ID = "fb-message-guardian"
SETTINGS_PATH = os.path.expanduser("~/.config/open-bot/plugin-%s.json" % PLUGIN_ID)
STATE_PATH = os.path.expanduser("~/.open-bot/%s/state.json" % PLUGIN_ID)
INBOX_URL = "https://www.messenger.com/"
SIDEBAR_W = 380

def out(obj, code=0):
    print(json.dumps(obj, ensure_ascii=False)); sys.exit(code)

def sh(cmd, timeout=120):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None

def page_read(session):
    p = sh(["ob-page", "--session", session, "read"])
    if not p or p.returncode != 0: return None
    try: return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception: return None

def load():
    settings = {}
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            settings = json.load(f).get("settings", {})
    except Exception: pass
    state = {"conversations": {}, "sends": {}, "sent_log": [], "pending": []}
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            d = json.load(f)
            for k in state: d.setdefault(k, state[k])
            state = d
    except Exception: pass
    return settings, state

def save(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_PATH)

def gates(settings, state, sender, hard_only=False):
    """Portes : heures + plafond sont dures (même avec --force).
    mode d'envoi et liste blanche sont contournables par --force."""
    today = datetime.now().strftime("%Y-%m-%d")
    hour = datetime.now().hour
    h1 = int(float(settings.get("active_hours_start", 8) or 0))
    h2 = int(float(settings.get("active_hours_end", 21) or 23))
    hours_ok = (h1 <= hour <= h2) if h1 <= h2 else (hour >= h1 or hour <= h2)
    if not hours_ok: return "hors heures actives (%dh-%dh)" % (h1, h2)
    cap = int(float(settings.get("daily_reply_cap", 20) or 20))
    if state.get("sends", {}).get(today, 0) >= cap: return "plafond quotidien atteint (%d)" % cap
    if hard_only: return None
    if settings.get("send_mode", "approbation") != "direct":
        return "mode approbation : passe par une notification (ou --force sur demande explicite de l'utilisateur)"
    wl = [w.strip().lower() for w in (settings.get("whitelist") or "").split(",") if w.strip()]
    if wl and not any(w in sender.lower() for w in wl):
        return "expéditeur hors liste blanche"
    return None

def open_thread(session, sender):
    page = page_read(session)
    if page is None or "messenger.com" not in page.get("url", ""):
        sh(["ob-nav", "--session", session, "--goal", INBOX_URL]); time.sleep(4)
        page = page_read(session)
        if page is None: return None, None
    for e in page.get("elements", []):
        if e.get("role") == "link" and sender.lower() in (e.get("label") or "").lower():
            sh(["ob-page", "--session", session, "click", e["id"]]); time.sleep(4)
            return page_read(session), e["id"]
    return None, None

def send_via_composer(session, text):
    page = page_read(session)
    if page is None: return {"ok": False, "detail": "page illisible avant envoi"}
    vp = page.get("viewport", {})
    w, h = vp.get("width", 1920), vp.get("height", 1080)
    x = SIDEBAR_W + max(200, (w - SIDEBAR_W) // 2)
    for dy in (42, 60, 30):
        y = h - dy
        sh(["ob-vnc", "--session", session, "move", str(x), str(y)])
        sh(["ob-vnc", "--session", session, "click", "1"]); time.sleep(1)
        sh(["ob-vnc", "--session", session, "paste", text]); time.sleep(1)
        sh(["ob-vnc", "--session", session, "key", "return"]); time.sleep(3)
        check = page_read(session)
        if check:
            for e in check.get("elements", []):
                lbl = (e.get("label") or "")
                if e.get("role") == "link" and lbl.strip().startswith("Vous: ") and \
                   text[:30].lower() in lbl.lower():
                    return {"ok": True, "detail": "confirmé dans l'aperçu de la conversation"}
                if "Saisir, Message envoyé à" in lbl and text[:30].lower() in lbl.lower():
                    return {"ok": True, "detail": "confirmé dans le fil"}
        # sinon : le texte est peut-être resté dans le composer — vider avant nouvel essai
        sh(["ob-vnc", "--session", session, "key", "ctrl-a"])
        sh(["ob-vnc", "--session", session, "key", "delete"])
    return {"ok": False, "detail": "envoi non confirmé après 3 essais : vérifier l'écran"}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument("--to", metavar="NOM")
    ap.add_argument("--text", metavar="TEXTE")
    ap.add_argument("--pending", metavar="ID", help="approuver et envoyer un brouillon en attente")
    ap.add_argument("--force", action="store_true", help="envoi sur demande explicite de l'utilisateur (contourne mode/liste blanche, jamais le plafond)")
    args = ap.parse_args()

    settings, state = load()
    sender, text = args.to, args.text
    pid = None
    if args.pending:
        entry = next((p for p in state["pending"] if p["id"] == args.pending), None)
        if not entry:
            out({"status": "error", "detail": "brouillon introuvable : " + args.pending}, 2)
        sender, text, pid = entry["sender"], entry["text"], entry["id"]
    if not sender or not text:
        out({"status": "error", "detail": "--to et --text requis (ou --pending ID)"}, 2)

    if not args.force:
        block = gates(settings, state, sender)
        if block:
            out({"status": "refused", "reason": block}, 2)
    else:
        block = gates(settings, state, sender, hard_only=True)
        if block:
            out({"status": "refused", "reason": "porte dure (--force ne les contourne pas) : " + block}, 2)

    if not args.force and any(e.get("sender") == sender and e.get("text") == text[:200]
                              for e in state.get("sent_log", [])):
        out({"status": "refused",
             "reason": "réponse identique déjà envoyée à ce destinataire (jamais deux réponses au même message ; --force pour passer)"}, 2)

    thread_page, _ = open_thread(args.session, sender)
    if thread_page is None:
        out({"status": "error", "detail": "conversation introuvable : " + sender}, 2)

    result = send_via_composer(args.session, text)
    if not result["ok"]:
        out({"status": "send_failed", "detail": result["detail"]}, 2)

    today = datetime.now().strftime("%Y-%m-%d")
    state["sends"][today] = state.get("sends", {}).get(today, 0) + 1
    state.setdefault("sent_log", []).append({"ts": datetime.now().isoformat(timespec="seconds"),
                                             "sender": sender, "text": text[:200],
                                             "pending": pid})
    if pid:
        state["pending"] = [p for p in state["pending"] if p["id"] != pid]
    save(state)
    out({"status": "sent", "to": sender, "chars": len(text), "sent_today": state["sends"][today]})

if __name__ == "__main__":
    main()
