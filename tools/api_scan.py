#!/usr/bin/env python3
"""ob-plugin-fb-message-guardian-api-scan — source « API officielle Meta » : lit les
messages de la Page Facebook via le Graph API (/{page}/conversations?platform=messenger),
détecte les nouveaux messages entrants et produit le MÊME plan d'action que le scan DOM
(mêmes règles, mêmes portes : heures, plafond, mode d'envoi, liste blanche).
Zéro écran, zéro navigateur, zéro token IA. Portée : messages de la Page uniquement —
l'API officielle Meta ne couvre pas les Messenger personnels (ceux-là restent au scan DOM).
Couture de test : env FBGUARD_GRAPH_BASE remplace https://graph.facebook.com."""
import argparse, json, os, sys, time
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

PLUGIN_ID = "fb-message-guardian"
SETTINGS_PATH = os.path.expanduser("~/.config/open-bot/plugin-%s.json" % PLUGIN_ID)
RULES_PATH = os.path.expanduser("~/.config/open-bot/fb-message-guardian-rules.json")
STATE_DIR = os.path.expanduser("~/.open-bot/" + PLUGIN_ID)
STATE_PATH = os.path.join(STATE_DIR, "state.json")
GRAPH_BASE = os.environ.get("FBGUARD_GRAPH_BASE", "https://graph.facebook.com").rstrip("/")
DEFAULT_GRAPH_VERSION = "v21.0"
WINDOW_SECONDS = 24 * 3600   # fenêtre standard de messagerie Meta
MESSAGES_PER_THREAD = 5      # fenêtre de rattrapage par conversation
CONV_LIMIT = 25
SEEN_KEEP = 20               # mids conservés par conversation pour le dédoublonnage

def out(obj, code=0):
    print(json.dumps(obj, ensure_ascii=False))
    sys.exit(code)

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

def load_rules():
    rules = load_json(RULES_PATH, [])
    return [r for r in rules
            if r.get("enabled", True) and (r.get("reply") or r.get("notify") or r.get("save_photo_to"))]

def match_rule(rules, sender, preview):
    """Première règle qui matche (priorité croissante), ou None — identique au scan DOM."""
    pl = (preview or "").lower()
    sl = (sender or "").lower()
    has_photo = any(h in pl for h in ("photo", "\U0001F4F7"))
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

def parse_ts(s):
    """'2026-10-14T03:33:44+0000' → epoch secondes."""
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z").timestamp()
    except Exception:
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        except Exception:
            return 0.0

def graph_get(base, version, path, params, token, timeout=25):
    url = "%s/%s%s?%s" % (base, version, path, urlencode(params))
    req = Request(url, headers={"Authorization": "Bearer %s" % token})
    try:
        with urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8")), None
    except HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
            err = (body.get("error") or {})
            detail = "%s %s" % (err.get("code", e.code), err.get("message", "")[:200])
        except Exception:
            detail = "HTTP %d" % e.code
        return None, detail
    except (URLError, TimeoutError, OSError) as e:
        return None, "réseau : %s" % str(e)[:150]

def preview_of(msg):
    """Texte du message, ou un aperçu de pièce jointe compatible avec les règles photo."""
    text = (msg.get("message") or "").strip()
    if text:
        return text
    for a in (msg.get("attachments") or {}).get("data", []):
        kind = (a.get("mime_type") or a.get("file_type") or "").lower()
        if "image" in kind or a.get("image_data"):
            return "[photo]"
        if kind:
            return "[pièce jointe %s]" % kind.split("/")[0]
    return "[pièce jointe]"

def sender_name(msg, conv):
    frm = msg.get("from") or {}
    if frm.get("name"):
        return frm["name"]
    page_id = (((msg.get("to") or {}).get("data") or [{}])[0].get("id"))
    for p in (conv.get("participants") or {}).get("data", []):
        if p.get("id") and p["id"] != page_id and p.get("name"):
            return p["name"]
    return "Client Page"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="rescan immédiat, ignore l'intervalle")
    ap.add_argument("--interval-override-seconds", type=float, default=None,
                    help="remplace l'intervalle configuré (utilisé par le watcher)")
    args = ap.parse_args()

    settings = load_json(SETTINGS_PATH, {}).get("settings", {})
    if (settings.get("source", "dom") or "dom") == "dom":
        out({"status": "disabled", "detail": "source=dom : scan API désactivé"})

    page_id = (settings.get("fb_page_id") or "").strip()
    token = (settings.get("fb_page_token") or "").strip()
    version = (settings.get("api_graph_version") or DEFAULT_GRAPH_VERSION).strip()
    if not page_id or not token:
        out({"status": "unconfigured",
             "detail": "fb_page_id et fb_page_token requis (onglet Plugins → réglages, token secret)"}, 3)

    state = load_json(STATE_PATH, {})
    state.setdefault("pending", [])
    state.setdefault("api_seen", {})
    state.setdefault("sends", {})
    now = time.time()
    interval_min = float(settings.get("check_interval_minutes", 15) or 15)
    min_age = interval_min * 60
    if args.interval_override_seconds is not None:
        min_age = max(60.0, args.interval_override_seconds)
    if not args.force and state.get("api_last_scan") and now - state["api_last_scan"] < min_age - 30:
        out({"status": "too_soon",
             "next_due_in_min": round((min_age - (now - state["api_last_scan"])) / 60, 1)})

    data, err = graph_get(GRAPH_BASE, version, "/%s/conversations" % page_id,
                          {"platform": "messenger", "limit": str(CONV_LIMIT),
                           "fields": "updated_time,participants,messages.limit(%d){created_time,from,to,message,attachments}" % MESSAGES_PER_THREAD},
                          token)
    if err:
        state["api_last_scan"] = now
        save_state(state)
        out({"status": "api_error", "detail": err}, 3)

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
    scanned = 0
    for conv in (data.get("data") or []):
        scanned += 1
        cid = conv.get("id")
        msgs = (conv.get("messages") or {}).get("data") or []
        if not cid or not msgs:
            continue
        seen = state["api_seen"].setdefault(cid, {"mid": None, "all": [], "ts": 0})
        seen_set = set(seen.get("all") or ([seen["mid"]] if seen.get("mid") else []))
        updated = parse_ts(conv.get("updated_time", ""))
        window_open = bool(updated and (now - updated) < WINDOW_SECONDS)
        target = None
        for msg in msgs:  # du plus récent au plus ancien
            frm = (msg.get("from") or {})
            if frm.get("id") == page_id:
                continue  # message sortant de la Page : jamais « nouveau »
            if msg.get("id") and msg["id"] not in seen_set:
                target = msg  # on garde le PLUS RÉCENT entrant non vu
        # marquer toute la fenêtre lue : on ne planifie que la dernière entrée,
        # les plus vieilles sont rattrapées et considérées couvertes
        seen["all"] = [m.get("id") for m in msgs if m.get("id")][-SEEN_KEEP:]
        if not target:
            seen["ts"] = now
            continue
        prev = preview_of(target)
        state["api_seen"][cid] = seen
        entry = {"sender": sender_name(target, conv), "preview": prev[:160],
                 "time": target.get("created_time", ""), "pkey": "m_%s" % target.get("id"),
                 "matched_keyword": next((k for k in keywords if k in prev.lower()), None),
                 "channel": "api", "psid": (target.get("from") or {}).get("id"),
                 "thread_id": cid, "window_open": window_open,
                 "action": None, "reason": None}
        # mêmes portes DURES que le scan DOM : heures, plafond, mode, liste blanche
        whitelisted = (not whitelist) or any(w in entry["sender"].lower() for w in whitelist)
        rule = match_rule(rules, entry["sender"], prev)
        if rule:
            entry["rule_id"] = rule.get("id")
            if rule.get("reply"):
                entry["text"] = rule["reply"]
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
        else:
            if mode == "hybride":
                action = "template" if entry["matched_keyword"] else "ai"
            elif mode == "ia":
                action = "ai"
            else:
                action = "template" if entry["matched_keyword"] else "notify"
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

    # entretien : au plus 500 conversations suivies
    if len(state["api_seen"]) > 500:
        keep = sorted(state["api_seen"].items(), key=lambda kv: kv[1].get("ts", 0), reverse=True)[:500]
        state["api_seen"] = dict(keep)
    state["api_last_scan"] = now
    await_ai = [p for p in state.get("pending", []) if p.get("status") == "await_ai"]
    save_state(state)
    out({"status": "ok", "source": "api", "api_conversations_scanned": scanned,
         "sent_today": sent_today, "cap": cap, "hours_ok": hours_ok, "new": plan,
         "await_ai": len(await_ai), "pending": len(state.get("pending", []))})

if __name__ == "__main__":
    main()
