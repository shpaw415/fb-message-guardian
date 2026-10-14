#!/usr/bin/env python3
"""ob-plugin-fb-message-guardian-watch — veille scriptée, ZÉRO token au repos.
Conçu pour un cron HYBRIDE (ob-cron --run auto) : le script tourne à chaque passe et
l'agent n'est réveillé QUE si une ligne de sortie commence par [cron-agent].
Toutes les ~3 minutes :
1. résout une session d'écran vivante (config screen_session, sinon auto-détection) ;
2. lance le scan avec un intervalle court ;
3. exécute les actions déterministes LUI-MÊME (notification, envoi modèle, mise en file) ;
4. émet une ligne [cron-agent] SEULEMENT s'il y a du travail IA en attente (await_ai,
   anti-répétition : pas de réveil plus d'une fois par intervalle de scan, sauf nouvelle
   entrée) ou un scan dom_changed (1 diagnostic/jour max). Sinon silence total :
   rien n'est affiché pour l'orchestrateur, aucun token dépensé."""
import argparse, glob, json, os, re, subprocess, sys, time
from datetime import datetime

PLUGIN_ID = "fb-message-guardian"
SCAN_BIN = "/usr/local/bin/ob-plugin-fb-message-guardian-scan"
SEND_BIN = "/usr/local/bin/ob-plugin-fb-message-guardian-send"
API_SCAN_BIN = "/usr/local/bin/ob-plugin-fb-message-guardian-api-scan"
SETTINGS_PATH = os.path.expanduser("~/.config/open-bot/plugin-%s.json" % PLUGIN_ID)
STATE_PATH = os.path.expanduser("~/.open-bot/%s/state.json" % PLUGIN_ID)
VNC_DIR = os.path.expanduser("~/.open-bot/vnc")
MARKER = "[cron-agent]"  # ligne reconnue par ob-cron --run auto : réveille l'agent
WAKE_CAP = 3             # réveils max par entrée avant notification « file bloquée »

def pkey_of(msg):
    return (msg or {}).get("pkey") or ""

def open_duplicate(st, sender, pkey):
    """Une entrée OUVERTE existe déjà pour ce même message ? (anti re-comptage pour l'agent)"""
    if not pkey:
        return False
    return any(e.get("status") in ("await_ai", "await_approval") and e.get("sender") == sender
               and e.get("pkey") == pkey for e in st["pending"])

def sh(cmd, timeout=120):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None

def settings():
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f).get("settings", {})
    except Exception:
        return {}

def state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        d = {}
    d.setdefault("pending", [])
    d.setdefault("watcher", {})
    return d

def save_state(d):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_PATH)

def resolve_session(cfg, st):
    """session fixée en config, sinon la session la plus récente dont la page répond."""
    fixed = (cfg.get("screen_session") or "").strip()
    if fixed:
        return fixed if page_alive(fixed) else None
    st["watcher"].setdefault("session", None)
    remembered = st["watcher"].get("session")
    if remembered and page_alive(remembered):
        return remembered
    candidates = sorted(glob.glob(os.path.join(VNC_DIR, "ses_*")),
                        key=os.path.getmtime, reverse=True)
    for path in candidates[:3]:
        sid = os.path.basename(path)
        if page_alive(sid):
            st["watcher"]["session"] = sid
            save_state(st)
            return sid
    return None

def page_alive(sid):
    p = sh(["ob-page", "--session", sid, "read"], timeout=40)
    return bool(p and p.returncode == 0 and '"url"' in (p.stdout or ""))

def notify(title, body):
    sh(["ob-plugin", "notify", PLUGIN_ID, title, body])

def queue_pending(st, sender, plan, keyword_matched, template_text, pkey="", msg=None):
    if open_duplicate(st, sender, pkey):
        return None
    pid = "p-" + os.urandom(2).hex()
    needs_ai = (plan == "send:ai") or (plan == "draft" and not keyword_matched)
    entry = {"id": pid, "sender": sender, "text": "" if needs_ai else template_text,
             "status": "await_ai" if needs_ai else "await_approval",
             "plan": plan, "pkey": pkey, "created": datetime.now().isoformat(timespec="seconds")}
    if msg and msg.get("channel") == "api":  # l'envoi de ce brouillard partira par l'API Page
        entry.update({"channel": "api", "psid": msg.get("psid"),
                      "window_open": bool(msg.get("window_open"))})
    st["pending"].append(entry)
    save_state(st)
    return entry

def queue_photo_task(st, sender, project, reply, pkey="", quiet=False):
    """Photo à router vers un projet open-bot : tâche pour l'agent (ouvre le fil, sauvegarde)."""
    if open_duplicate(st, sender, pkey):
        return None
    pid = "p-" + os.urandom(2).hex()
    st["pending"].append({"id": pid, "sender": sender, "text": "", "status": "await_ai",
                          "plan": "route_photo", "task": "route_photo", "project": project,
                          "reply": reply or "", "pkey": pkey,
                          "created": datetime.now().isoformat(timespec="seconds")})
    save_state(st)
    if not quiet:
        notify("Photo à router", "%s → projet %s (file %s)" % (sender, project, pid))
    return pid

def act(scan, session, cfg, st):
    done = []
    template = cfg.get("auto_template", "")
    for msg in scan.get("new", []):
        sender = msg["sender"]
        action = msg["action"]
        pkey = pkey_of(msg)
        if action == "notify":
            ntext = msg.get("notify_text")
            if ntext:
                body = (ntext.replace("{expediteur}", sender.split()[0])
                             .replace("{aperçu}", msg.get("preview", "")[:120]))
                if msg.get("route_photo"):
                    body += " — photo à router vers le projet %s" % msg["route_photo"]
            else:
                body = "%s : %s" % (sender, msg["preview"][:120])
            notify("Message Facebook", body)
            done.append("notify:%s" % sender)
        elif action.startswith("send:template"):
            text = (msg.get("text") or template).replace("{expediteur}", sender.split()[0])
            if msg.get("channel") == "api":  # envoi via l'API officielle Page, sans écran
                r = sh([SEND_BIN, "--session", session or "", "--psid", msg.get("psid") or "",
                        "--to", sender, "--text", text])
            else:
                r = sh([SEND_BIN, "--session", session, "--to", sender, "--text", text])
            outj = {}
            try:
                outj = json.loads((r.stdout or "").strip().splitlines()[-1]) if r and r.stdout else {}
            except Exception:
                pass
            if outj.get("status") == "sent":
                done.append("sent:%s" % sender)
            else:
                entry = queue_pending(st, sender, "draft", True, text, pkey, msg)
                if entry:
                    notify("Réponse en attente de ton oui",
                           "Brouillon %s pour %s : « %s » (envoi refusé : %s)"
                           % (entry["id"], sender, text[:80], outj.get("reason", "?")))
                    done.append("queued:%s" % sender)
                else:
                    done.append("dedup:%s" % sender)
        elif action == "send:ai":
            entry = queue_pending(st, sender, "send:ai", False, "", pkey, msg)
            if entry:
                notify("Message Facebook — l'IA prépare une réponse",
                       "%s : %s (file %s)" % (sender, msg["preview"][:100], entry["id"]))
                done.append("ai:%s" % sender)
            else:
                done.append("dedup:%s" % sender)
        elif action == "draft":
            rtext = msg.get("text")
            if rtext:  # réponse fixe d'une règle : brouillon directement approvable
                entry = queue_pending(st, sender, "draft", True, rtext, pkey, msg)
            else:
                entry = queue_pending(st, sender, "draft", bool(msg.get("matched_keyword")),
                                      template, pkey, msg)
            if entry:
                if entry["status"] == "await_ai":
                    notify("Message Facebook — l'IA prépare une réponse",
                           "%s : %s (file %s)" % (sender, msg["preview"][:100], entry["id"]))
                else:
                    notify("Réponse en attente de ton oui",
                           "Brouillon %s pour %s : « %s »"
                           % (entry["id"], sender, entry["text"][:80]))
                done.append("queued:%s" % sender)
            else:
                done.append("dedup:%s" % sender)
        if msg.get("route_photo"):  # règle save_photo_to : la photo part vers le projet via l'agent
            if not queue_photo_task(st, sender, msg["route_photo"], msg.get("text", ""), pkey,
                                    quiet=(action == "notify")):
                done.append("dedup:%s" % sender)
            else:
                done.append("photo:%s→%s" % (sender, msg["route_photo"]))
    return done

def pending_ai(st):
    return [p for p in st.get("pending", []) if p.get("status") == "await_ai"]

def wake_agent(st, cfg, session, scan):
    """Porte du cron hybride : imprime [cron-agent] UNIQUEMENT s'il y a du travail.
    - await_ai présents : réveil immédiat à la première entrée, puis au plus une fois
      par intervalle de scan (check_interval_minutes, min 10 min) sauf NOUVELLE entrée ;
      une entrée est réveillée au plus WAKE_CAP fois, ensuite : notification « file
      bloquée » (1/jour) au lieu de réveiller l'agent en boucle.
    - dom_changed : un réveil-diagnostic par jour au maximum.
    - tout le reste : retour sans rien imprimer → passe silencieuse, zéro token."""
    w = st.setdefault("watcher", {})
    ai = pending_ai(st)
    today = datetime.now().strftime("%Y-%m-%d")
    dom_wake = scan.get("status") == "dom_changed" and w.get("last_dom_wake") != today
    wakeable = [e for e in ai if int(e.get("wake_count") or 0) < WAKE_CAP]
    if not ai and not dom_wake:
        return
    if not wakeable and not dom_wake:
        if w.get("stuck_notified_on") != today:
            w["stuck_notified_on"] = today
            senders = ", ".join(sorted({(e.get("sender") or "?").split()[0] for e in ai}))
            notify("File du gardien bloquée",
                   "%d tâche(s) IA non traitée(s) après %d réveils (%s). Vérifie "
                   "ob-plugin-fb-message-guardian-queue --list." % (len(ai), WAKE_CAP, senders))
        return
    try:
        cooldown = max(600.0, float(cfg.get("check_interval_minutes", 15) or 15) * 60.0)
    except (TypeError, ValueError):
        cooldown = 900.0
    fresh = any(not e.get("wake_count") for e in wakeable)
    if not fresh and not dom_wake and (time.time() - float(w.get("last_agent_wake") or 0)) < cooldown:
        return
    w["last_agent_wake"] = time.time()
    if dom_wake:
        w["last_dom_wake"] = today
    for e in wakeable:
        e["wake_count"] = int(e.get("wake_count") or 0) + 1
    save_state(st)
    if ai:
        senders = ", ".join(sorted({(e.get("sender") or "?").split()[0] for e in wakeable}))
        photos = sum(1 for e in wakeable if e.get("task") == "route_photo")
        suffix = " dont %d photo(s) à router vers un projet" % photos if photos else ""
        print("%s Gardien FB : %d tâche(s) IA en attente (%s)%s. Canal : %s."
              % (MARKER, len(wakeable), senders, suffix, session))
    if dom_wake:
        print("%s Gardien FB : scan=dom_changed (plus aucune conversation lisible) sur la "
              "session %s : Facebook a probablement changé son DOM. Diagnostique une fois "
              "avec ob-page --session %s read et répare parse_rows/parse_row du scan si tu "
              "peux, sinon rapporte." % (MARKER, session, session))

def dom_pass(cfg, st, seconds):
    """Une passe du canal DOM (Messenger personnel). Retourne (status, session, scan)."""
    session = resolve_session(cfg, st)
    if not session:
        return "no_session", None, None
    p = sh([SCAN_BIN, "--session", session, "--interval-override-seconds", str(seconds)])
    if not p or not p.stdout:
        return "no_output", session, None
    try:
        scan = json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        return "unreadable", session, None
    return scan.get("status"), session, scan

def api_pass(cfg, st, seconds):
    """Une passe du canal API officielle (messages de la Page). Retourne (status, scan)."""
    p = sh([API_SCAN_BIN, "--interval-override-seconds", str(seconds)])
    if not p or not p.stdout:
        return "no_output", None
    try:
        scan = json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        return "unreadable", None
    return scan.get("status"), scan

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval-seconds", type=float, default=180)
    args = ap.parse_args()
    cfg = settings()
    if cfg.get("watcher_enabled", "true") != "true":
        print("watcher désactivé"); return
    source = (cfg.get("source", "dom") or "dom")
    st = state()
    lines = []

    # — Canal DOM (Messenger personnel, via l'écran) —
    if source in ("dom", "dom+api"):
        status, session, scan = dom_pass(cfg, st, args.interval_seconds)
        if status == "ok":
            st = state()  # recharger : le scan (sous-processus) a pu mettre à jour l'état
            done = act(scan, session, cfg, st)
            ai = pending_ai(st)
            lines.append("dom: " + ((", ".join(done) or "aucune") if (done or ai) else "rien"))
            if done or ai:
                wake_agent(st, cfg, "écran %s" % session, scan)
        elif status == "too_soon":
            pass
        elif status == "dom_changed":
            lines.append("dom: dom_changed")
            wake_agent(st, cfg, session, scan)
        elif status == "no_session":
            lines.append("dom: aucune session d'écran vivante")
        else:
            lines.append("dom: %s" % status)

    # — Canal API officielle Meta (messages de la Page, sans écran) —
    if source in ("api", "dom+api"):
        status, scan = api_pass(cfg, st, args.interval_seconds)
        if status == "ok":
            st = state()  # recharger : l'api_scan (sous-processus) a pu marquer api_seen
            done = act(scan, None, cfg, st)
            ai = pending_ai(st)
            lines.append("api: " + ((", ".join(done) or "aucune") if (done or ai) else "rien"))
            if done or ai:
                wake_agent(st, cfg, "API Page", scan)
        elif status == "too_soon":
            pass
        elif status in ("unconfigured", "api_error"):
            w = st.setdefault("watcher", {})
            today = datetime.now().strftime("%Y-%m-%d")
            if w.get("api_diag_notified_on") != today:  # 1 diagnostic/jour max
                w["api_diag_notified_on"] = today
                save_state(st)
                lines.append("api: %s — %s" % (status, (scan or {}).get("detail", "")))
                print("%s Gardien FB : source api en échec (%s) : %s. Vérifie fb_page_id / "
                      "fb_page_token (réglages du plugin) et les permissions du token."
                      % (MARKER, status, (scan or {}).get("detail", "")))
        else:
            lines.append("api: %s" % status)

    print(" | ".join(lines) if lines else "rien")

if __name__ == "__main__":
    main()
