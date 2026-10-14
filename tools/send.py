#!/usr/bin/env python3
"""ob-plugin-fb-message-guardian-send — envoie une réponse dans une conversation Messenger.
Double porte : réglages (mode d'envoi, liste blanche, plafond, heures) vérifiés ici aussi.
Le tap se fait via ob-vnc (clic dans le composer, collage, Entrée) car le composer n'est pas
exposé dans l'arbre accessible retourné par ob-page."""
import argparse, json, os, re, subprocess, sys, time
from datetime import datetime
from urllib.request import Request, urlopen

PLUGIN_ID = "fb-message-guardian"
SETTINGS_PATH = os.path.expanduser("~/.config/open-bot/plugin-%s.json" % PLUGIN_ID)
STATE_PATH = os.path.expanduser("~/.open-bot/%s/state.json" % PLUGIN_ID)
INBOX_URL = "https://www.messenger.com/"
SIDEBAR_W = 380
GRAPH_BASE = os.environ.get("FBGUARD_GRAPH_BASE", "https://graph.facebook.com").rstrip("/")
DEFAULT_GRAPH_VERSION = "v21.0"

def api_send(psid, text, timeout=25):
    """Envoi par l'API officielle Meta (POST /{page}/messages). Retourne (ok, detail)."""
    settings = {}
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            settings = json.load(f).get("settings", {})
    except Exception:
        pass
    page_id = (settings.get("fb_page_id") or "").strip()
    token = (settings.get("fb_page_token") or "").strip()
    version = (settings.get("api_graph_version") or DEFAULT_GRAPH_VERSION).strip()
    if not page_id or not token:
        return False, "api non configurée : fb_page_id et fb_page_token requis"
    body = json.dumps({"recipient": {"id": psid}, "message": {"text": text},
                       "messaging_type": "RESPONSE"}).encode("utf-8")
    url = "%s/%s/%s/messages?%s" % (GRAPH_BASE, version, page_id,
                                    "access_token=" + token)
    try:
        with urlopen(Request(url, data=body, method="POST",
                             headers={"Content-Type": "application/json"}), timeout=timeout) as r:
            resp = json.loads(r.read().decode("utf-8"))
        return True, "message_id %s" % ((resp.get("message_id") or "?"),)
    except Exception as e:
        detail = str(e)[:200]
        try:
            err = json.loads(e.read().decode("utf-8")).get("error") or {}
            detail = "%s %s" % (err.get("code", ""), err.get("message", "")[:180])
        except Exception:
            pass
        return False, detail

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
    ap.add_argument("--session", default="", help="id d'écran (requis pour le canal DOM ; inutile via --psid)")
    ap.add_argument("--to", metavar="NOM")
    ap.add_argument("--text", metavar="TEXTE")
    ap.add_argument("--pending", metavar="ID", help="approuver et envoyer un brouillon en attente")
    ap.add_argument("--psid", metavar="PSID", help="envoi direct par l'API Page (canal api, sans écran)")
    ap.add_argument("--force", action="store_true", help="envoi sur demande explicite de l'utilisateur (contourne mode/liste blanche, jamais le plafond)")
    args = ap.parse_args()

    settings, state = load()
    sender, text, psid = args.to, args.text, args.psid
    pid = None
    if args.pending:
        entry = next((p for p in state["pending"] if p["id"] == args.pending), None)
        if not entry:
            out({"status": "error", "detail": "brouillon introuvable : " + args.pending}, 2)
        sender, text, pid = entry["sender"], entry["text"], entry["id"]
        if entry.get("channel") == "api":
            psid = entry.get("psid") or psid
            if not entry.get("window_open") and not args.force:
                out({"status": "refused",
                     "reason": "fenêtre de 24 h Meta fermée sur ce fil : répondre depuis la boîte Page à la main"}, 2)
    if not sender or not text:
        out({"status": "error", "detail": "--to et --text requis (ou --pending ID)"}, 2)
    api_mode = bool(psid)  # canal API officiel dès qu'un PSID est fourni
    if args.psid and not psid:
        out({"status": "error", "detail": "--psid exigé pour un envoi API direct"}, 2)

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

    # le placeholder part JAMAIS brut : expansion au moment de l'envoi (les deux canaux)
    text = text.replace("{expediteur}", (sender or "").split()[0] if sender else "")

    if api_mode:
        ok, detail = api_send(psid, text)
        if not ok:
            out({"status": "send_failed", "detail": "envoi API refusé : " + detail}, 2)
        today = datetime.now().strftime("%Y-%m-%d")
        state["sends"][today] = state.get("sends", {}).get(today, 0) + 1
        state.setdefault("sent_log", []).append({"ts": datetime.now().isoformat(timespec="seconds"),
                                                 "sender": sender, "text": text[:200],
                                                 "channel": "api", "pending": pid})
        if pid:
            state["pending"] = [p for p in state["pending"] if p["id"] != pid]
        save(state)
        out({"status": "sent", "channel": "api", "to": sender, "psid": psid,
             "chars": len(text), "sent_today": state["sends"][today]})

    if not args.session:
        out({"status": "error", "detail": "--session requis pour l'envoi DOM (inutile avec --psid)"}, 2)
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
