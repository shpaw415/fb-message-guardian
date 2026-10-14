#!/usr/bin/env python3
"""Stress-test fb-message-guardian v0.2.0 — canal API officielle Meta.
Chaque cas imprime PASS/FAIL ; code de sortie = nombre d'échecs."""
import json, os, subprocess, sys, time

HOME = os.path.expanduser("~")
SETTINGS = HOME + "/.config/open-bot/plugin-fb-message-guardian.json"
STATE = HOME + "/.open-bot/fb-message-guardian/state.json"
BAK = "/tmp/opencode"
API_SCAN = "/usr/local/bin/ob-plugin-fb-message-guardian-api-scan"
WATCH = "/usr/local/bin/ob-plugin-fb-message-guardian-watch"
QUEUE = "/usr/local/bin/ob-plugin-fb-message-guardian-queue"
SEND = "/usr/local/bin/ob-plugin-fb-message-guardian-send"
ENVBASE = {"FBGUARD_GRAPH_BASE": "http://127.0.0.1:8123"}
ENVERR = {"FBGUARD_GRAPH_BASE": "http://127.0.0.1:8124"}

fails = []

def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ((" — " + str(detail)[:200]) if not cond else ""))
    if not cond:
        fails.append(name)

def run(cmd, env_extra=None, timeout=60):
    env = dict(os.environ)
    env.update(env_extra or {})
    p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=timeout, env=env)
    lines = (p.stdout or "").strip().splitlines()
    try:
        js = json.loads(lines[-1]) if lines else {}
    except Exception:
        js = {}
    return p.returncode, js, p.stdout or "", p.stderr or ""

def reset_api_last_scan():
    subprocess.run(["python3", "-c",
                    "import json,os;p=os.path.expanduser('~/.open-bot/fb-message-guardian/state.json');"
                    "d=json.load(open(p));d['api_last_scan']=0;"
                    "json.dump(d,open(p,'w'),ensure_ascii=False,indent=1)"], capture_output=True)

def load_settings():
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def clean_api_state():
    subprocess.run(["python3", "-c",
                    "import json,os;p=os.path.expanduser('~/.open-bot/fb-message-guardian/state.json');"
                    "d=json.load(open(p));d['api_seen']={};d['api_last_scan']=0;"
                    "w=d.get('watcher',{});w.pop('api_diag_notified_on',None);d['watcher']=w;"
                    "json.dump(d,open(p,'w'),ensure_ascii=False,indent=1)"], capture_output=True)

def main():
    orig_settings = load_settings()
    with open(BAK + "/settings.orig.json", "w", encoding="utf-8") as f:
        json.dump(orig_settings, f, ensure_ascii=False, indent=1)
    try:
        with open(STATE, encoding="utf-8") as f:
            orig_state = f.read()
    except Exception:
        orig_state = None
    with open(BAK + "/state.orig.json", "w", encoding="utf-8") as f:
        f.write(orig_state or "{}")
    clean_api_state()

    # réglages de test : source=api, heures 0-23, direct + liste blanche Marc
    subprocess.run(["ob-plugin", "settings", "fb-message-guardian", "source=api",
                    "fb_page_id=999999999999999", "fb_page_token=TESTTOKEN",
                    "active_hours_start=0", "active_hours_end=23",
                    "send_mode=direct", "whitelist=Marc,Anonyme Cinq"], capture_output=True, text=True)

    # Cas 1 — config manquante : sans fb_page_id/token → unconfigured (exit 3)
    subprocess.run(["ob-plugin", "settings", "fb-message-guardian", "fb_page_id=", "fb_page_token="],
                   capture_output=True, text=True)
    rc, js, _, _ = run([API_SCAN, "--force"])
    check("C1 unconfigured", rc == 3 and js.get("status") == "unconfigured", js)
    # Cas 1-bis — source=dom → disabled, exit 0 (canal api volontairement off)
    subprocess.run(["ob-plugin", "settings", "fb-message-guardian", "fb_page_id=999999999999999",
                    "fb_page_token=TESTTOKEN", "source=dom"], capture_output=True, text=True)
    rc, js, _, _ = run([API_SCAN, "--force"])
    check("C1bis disabled quand source=dom", rc == 0 and js.get("status") == "disabled", js)
    subprocess.run(["ob-plugin", "settings", "fb-message-guardian", "source=api"],
                   capture_output=True, text=True)

    # Cas 2 — happy path : plan attendu sur le mock
    rc, js, _, _ = run([API_SCAN, "--force"], ENVBASE)
    plan = {e["sender"]: e for e in js.get("new", [])}
    check("C2 status ok", rc == 0 and js.get("status") == "ok" and js.get("source") == "api", js)
    check("C2 Marc → send:template (devis+site web, whitelist)", plan.get("Marc Tremblay", {}).get("action") == "send:template", plan.get("Marc Tremblay"))
    check("C2 Clément → draft (hors liste blanche)", plan.get("Clément Æ", {}).get("action") == "draft", plan.get("Clément Æ"))
    check("C2 Sophie → draft (fenêtre 24 h fermée n'empêche pas la mise en file)", plan.get("Sophie", {}).get("action") == "draft", plan.get("Sophie"))
    check("C2 Sophie window_open=false", plan.get("Sophie", {}).get("window_open") is False, plan.get("Sophie"))
    check("C2 Anonyme Cinq (unicode, sans nom d'expéditeur) → send:template", plan.get("Anonyme Cinq", {}).get("action") == "send:template", plan.get("Anonyme Cinq"))
    check("C2 aperçu tronqué à 160", len(plan.get("Clément Æ", {}).get("preview", "")) <= 160, len(plan.get("Clément Æ", {}).get("preview", "")))
    check("C2 conv sortante seule (t_B) ignorée", "Solo Page" not in plan, list(plan))
    check("C2 conv sans messages (t_E) ignorée", len(js.get("new", [])) == 4, len(js.get("new", [])))
    check("C2 pkey = id Meta", plan.get("Marc Tremblay", {}).get("pkey") == "m_m_in_2", plan.get("Marc Tremblay", {}).get("pkey"))

    # Cas 3 — re-run : dédoublonnage exact, zéro nouveau
    rc, js, _, _ = run([API_SCAN, "--force"], ENVBASE)
    check("C3 dédoublonnage re-run", js.get("new") == [], js.get("new"))

    # Cas 4 — watch (canal api seul) : agit, réveille l'agent pour l'IA
    open(BAK + "/fbmock_posts.log", "wb").close()
    clean_api_state()  # vide api_seen : la passe watch doit voir les messages comme nouveaux
    rcw, _, outw, errw = run([WATCH], ENVBASE)
    check("C4 watch réveille l'agent", "[cron-agent]" in outw, outw + errw)
    posts = open(BAK + "/fbmock_posts.log", "rb").read().decode()
    check("C4 envoi modèle Marc parti par l'API", '"10001"' in posts and "Bonjour Marc !" in posts, posts[:300])
    _, q, _, _ = run([QUEUE, "--list"])
    open_e = {e["sender"]: e for e in q.get("open", [])}
    check("C4 Clément en file await_approval", open_e.get("Clément Æ", {}).get("status") == "await_approval", list(open_e))
    check("C4 Clément porte channel/psid", open_e.get("Clément Æ", {}).get("channel") == "api" and open_e.get("Clément Æ", {}).get("psid") == "10004", open_e.get("Clément Æ"))
    sophie = open_e.get("Sophie", {})
    check("C4 Sophie en file await_ai", sophie.get("status") == "await_ai", sophie)
    check("C4 Sophie window_open=false en file", sophie.get("window_open") is False, sophie)

    # Cas 5 — re-run watch : trop tôt pour l'api_scan, pas de second réveil
    time.sleep(1)
    reset_api_last_scan()
    rcw, _, outw2, _ = run([WATCH], ENVBASE)
    check("C5 second watch silencieux (anti-boucle)", "[cron-agent]" not in outw2, outw2)

    # Cas 6 — fenêtre 24 h : le brouillon Sophie est refusé à l'envoi
    sid = sophie.get("id")
    run([QUEUE, "--set-text", sid, "Salut Sophie !"])
    rc, js, _, _ = run([SEND, "--session", "", "--pending", sid], ENVBASE)
    check("C6 fenêtre 24 h refusée", rc == 2 and js.get("status") == "refused" and "24 h" in js.get("reason", ""), js)

    # Cas 7 — envoi api d'un brouillon approuvé (Clément, --force contourne la liste blanche)
    cid = open_e.get("Clément Æ", {}).get("id")
    rc, js, _, _ = run([SEND, "--session", "", "--pending", cid, "--force"], ENVBASE)
    check("C7 brouillon Clément envoyé via api", rc == 0 and js.get("status") == "sent" and js.get("channel") == "api", js)
    posts = open(BAK + "/fbmock_posts.log", "rb").read().decode()
    check("C7 POST mock avec PSID 10004", '"10004"' in posts and "{expediteur}" not in posts, posts[-300:])
    _, q2, _, _ = run([QUEUE, "--list"])
    check("C7 brouillon Clément retiré de la file", "Clément Æ" not in {e["sender"] for e in q2.get("open", [])}, [e["sender"] for e in q2.get("open", [])])
    check("C7 sent_log a incrémenté le plafond", js.get("sent_today", 0) >= 1, js)

    # Cas 8 — échec API : token invalide → diagnostic une fois/jour, puis silence
    rc, js, _, _ = run([API_SCAN, "--force"], ENVERR)
    check("C8 api_error détaillé", rc == 3 and js.get("status") == "api_error" and "190" in js.get("detail", ""), js)
    subprocess.run(["python3", "-c", "import json,os;p=os.path.expanduser('~/.open-bot/fb-message-guardian/state.json');d=json.load(open(p));w=d.get('watcher',{});w.pop('api_diag_notified_on',None);d['watcher']=w;json.dump(d,open(p,'w'),ensure_ascii=False,indent=1)"], capture_output=True)
    reset_api_last_scan()
    rcw, _, outw3, _ = run([WATCH], ENVERR)
    check("C8 watch diagnostique l'échec api (1/jour)", outw3.count("[cron-agent]") == 1 and "api_error" in outw3, outw3)
    reset_api_last_scan()
    rcw, _, outw4, _ = run([WATCH], ENVERR)
    check("C8 diagnostic pas répété le même jour", outw4.count("[cron-agent]") == 0, outw4)

    # restauration : réglages + état de prod (try/finally en amont)
    try:
        for k, v in orig_settings.get("settings", {}).items():
            subprocess.run(["ob-plugin", "settings", "fb-message-guardian", "%s=%s" % (k, v)],
                           capture_output=True, text=True)
        with open(BAK + "/state.orig.json", encoding="utf-8") as f:
            restored = f.read()
        with open(STATE, "w", encoding="utf-8") as f:
            f.write(restored)
    finally:
        print("---")
        print("ÉCHECS: %d %s" % (len(fails), fails) if fails else "TOUT PASSE")
        sys.exit(1 if fails else 0)

if __name__ == "__main__":
    main()
