#!/usr/bin/env python3
"""ob-plugin-fb-message-guardian-send — envoie une réponse dans une conversation Messenger.
Double porte : réglages (mode d'envoi, liste blanche, plafond, heures) vérifiés ici aussi.
Canal DOM (v0.3.3) : le collage e2ee ne passe fiablement que ~300 car, perd les emojis et
aplatit les sauts de ligne ; et un aperçu de lien suffisait à produire un faux « sent »
(message tronqué du 2026-10-21). Le texte part donc en collages courts (≤ MAX_PASTE)
ACCUMULÉS dans le composer — chaque collage vérifié PAR CAPTURE (juge ob-system1) — puis
UN SEUL Return par bloc (≤ ACCUMULATE_MAX car) : le destinataire reçoit UN message, pas
une pile de bulles. Confirmation fondée sur le TEXTE INTÉGRAL du bloc (jamais l'aperçu
du lien ni un fragment). Doute → send_failed, brouillon conservé, aucun fragment laissé
dans le composer ; jamais de ctrl-a tant que la capture n'a pas prouvé que le focus est
dans le composer (ctrl-a hors composer déclenche la sélection multiple des messages)."""
import argparse, json, os, re, subprocess, sys, tempfile, time
from datetime import datetime
from urllib.request import Request, urlopen

PLUGIN_ID = "fb-message-guardian"
SETTINGS_PATH = os.environ.get(
    "FBGUARD_SETTINGS", os.path.expanduser("~/.config/open-bot/plugin-%s.json" % PLUGIN_ID))
STATE_PATH = os.environ.get(
    "FBGUARD_STATE", os.path.expanduser("~/.open-bot/%s/state.json" % PLUGIN_ID))
INBOX_URL = "https://www.messenger.com/"
GRAPH_BASE = os.environ.get("FBGUARD_GRAPH_BASE", "https://graph.facebook.com").rstrip("/")
DEFAULT_GRAPH_VERSION = "v21.0"

MAX_PASTE = 280        # le collage e2ee tronque au-delà de ~300 car (constat 2026-10-21)
ACCUMULATE_MAX = 1200  # plafond d'UN message Messenger : au-delà, on coupe en blocs
PASTE_ATTEMPTS = 2     # tentatives max par bloc avant abandon (jamais de Return à l'aveugle)
CONFIRM_READS = 2      # lectures DOM post-Return avant le secours par capture


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
    if page is None or "messenger.com" not in (page.get("url") or ""):
        sh(["ob-nav", "--session", session, "--goal", INBOX_URL]); time.sleep(4)
        page = page_read(session)
        if page is None: return None, None
    if find_composer(page) is not None:
        # fil déjà ouvert (cas usuel après un scan) : ne RIEN cliquer — le seul lien au nom
        # de l'expéditeur visible est l'en-tête du fil, le cliquer ouvre le panneau d'infos
        # et décale le composer (cause de l'échec e2ee du 2026-10-20).
        return page, None
    sl = sender.lower()
    links = [e for e in page.get("elements", [])
             if e.get("role") == "link" and sl in (e.get("label") or "").lower()]
    good = [e for e in links if not any(h in norm(e.get("label")) for h in HEADER_HINTS)]
    for e in (good or links)[:3]:
        sh(["ob-page", "--session", session, "click", e["id"]]); time.sleep(4)
        page = page_read(session)
        if find_composer(page) is not None:
            return page, e["id"]
    return (page, None) if find_composer(page) is not None else (None, None)

def norm(s):
    """minuscules + espaces repliés : les labels DOM coupent/surchargent le texte."""
    return " ".join((s or "").split()).lower()

HEADER_HINTS = ("passer un appel", "lancer un appel", "informations sur la conversation")

COMPOSER_HINTS = ("écrire à", "ecrire a", "écris", "write")

def find_composer(page):
    """Le composer des fils e2ee est la textbox « Écrire à <nom> » (contenteditable :
    value reste vide même rempli). Retourne l'élément ou None."""
    boxes = [e for e in page.get("elements", []) if e.get("editable")]
    for e in boxes:
        lbl = norm(e.get("label"))
        if e.get("role") == "textbox" and any(h in lbl for h in COMPOSER_HINTS):
            return e
    for e in boxes:
        if e.get("role") == "textbox":
            return e
    return None

EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002B00-\U00002BFF\U00002600-\U000027BF"
    "\U0000FE00-\U0000FE0F\U0000200D\U000020E3]")

def strip_emojis(text):
    """Le collage e2ee PERD les emojis (constat 2026-10-21) : les retirer d'abord, sinon
    le texte attendu ne correspondrait jamais à ce qui part vraiment et chaque envoi
    finirait en send_failed. (Le canal API, lui, part avec le texte brut et ses emojis.)"""
    return EMOJI_RE.sub("", text or "")

def split_segments(text, maxlen=MAX_PASTE):
    """Texte → segments de ≤ maxlen car, paragraphe unique chacun (le collage e2ee
    aplatit les sauts de ligne). Coupure préférée en fin de phrase, sinon fin de mot,
    sinon coupe sèche (une URL, sans espace, reste entière)."""
    flat = " ".join((text or "").split())
    segs, rest = [], flat.strip()
    while rest:
        if len(rest) <= maxlen:
            segs.append(rest)
            break
        window = rest[:maxlen + 1]
        cuts = [window.rfind(p) + len(p) for p in (". ", "! ", "? ", "… ")]
        good = [c for c in cuts if maxlen // 2 <= c <= maxlen]
        cut = max(good) if good else window.rfind(" ") + 1
        if cut < maxlen // 4:                    # mot unique très long : coupe sèche
            cut = maxlen
        segs.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    return [s for s in segs if s]

def snap(session):
    """Capture d'écran pleine (ob-vnc). Retourne le chemin du png ou None."""
    fd, tmp = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    p = sh(["ob-vnc", "--session", session, "capture", tmp], timeout=60)
    if not p or p.returncode != 0:
        try: os.unlink(tmp)
        except OSError: pass
        return None
    return tmp

def _answers(p):
    """Réponses q1/q2… d'un appel ob-system1, ou None si juge indisponible/indécis."""
    if not p or p.returncode != 0:
        return None
    try:
        js = json.loads((p.stdout or "").strip().splitlines()[-1])
    except Exception:
        return None
    if not js.get("ok"):
        return None
    ans = js.get("answers") or {}
    def pick(q):
        a = ans.get(q) or {}
        if a.get("valid") is False:
            return None
        return {"oui": True, "non": False}.get((a.get("choice") or "").strip().lower())
    return ans, pick

def composer_probe(session, expected, mode="exact"):
    """Capture + juge ob-system1 : (ok, has_text). ok selon mode —
    exact : le composer affiche EXACTEMENT ce texte (premier collage) ;
    ends  : le composer SE TERMINE exactement par ce texte (collages suivants) ;
    full  : le composer affiche l'INTÉGRALITÉ de ce texte (porte avant le Return).
    has_text : le composer contient du texte (même partiel). (None, None) si le juge
    est indisponible. Le read DOM d'un contenteditable e2ee renvoie toujours
    value=\"\" : seule la capture voit le vrai contenu (2026-10-21)."""
    questions = {
        "exact": "La zone de saisie du message affiche-t-elle EXACTEMENT ce texte, "
                 "au complet (une URL peut apparaître en carte de lien) ?",
        "ends": "La zone de saisie du message SE TERMINE-T-ELLE exactement par ce texte "
                "(une URL peut apparaître en carte de lien) ?",
        "full": "La zone de saisie du message affiche-t-elle l'INTÉGRALITÉ de ce texte, "
                "au complet (une URL peut apparaître en carte de lien) ?",
    }
    tmp = snap(session)
    if not tmp:
        return None, None
    try:
        p = sh(["ob-system1",
                "--state", "Texte attendu dans la zone de saisie du message : « %s »" % expected,
                "--image", tmp,
                "--choice", questions.get(mode, questions["exact"]), "oui", "non",
                "--choice", "La zone de saisie du message contient-elle du texte, même partiel ou différent ?",
                "oui", "non"], timeout=90)
    finally:
        try: os.unlink(tmp)
        except OSError: pass
    got = _answers(p)
    if not got:
        return None, None
    _, pick = got
    return pick("q1"), pick("q2")

def vision(session, image, context, question):
    """Juge System 1 sur une capture, question fermée : True / False / None."""
    p = sh(["ob-system1", "--state", context, "--image", image,
            "--choice", question, "oui", "non"], timeout=90)
    got = _answers(p)
    return got[1]("q1") if got else None

def focus_composer(session, comp):
    """Focus clavier du composer e2ee : le clic DOM sur le textbox « Écrire à <nom> »
    DONNE le focus (constat 2026-10-21). Le clic pixel reste incertain : il peut tomber
    sur une rangée de message et ouvrir la sélection multiple."""
    if not comp or not comp.get("id"):
        return False
    sh(["ob-page", "--session", session, "click", comp["id"]])
    time.sleep(1.0)
    return True

def clear_composer(session):
    """Vider le composer — UNIQUEMENT quand la capture vient de prouver qu'il contient
    du texte (donc que le focus y est) : un ctrl-a tombé hors composer déclenche la
    sélection multiple des messages (bordure pointillée + pilule « Échap »)."""
    sh(["ob-vnc", "--session", session, "key", "ctrl-a"])
    sh(["ob-vnc", "--session", session, "key", "delete"])
    time.sleep(0.5)

def group_blocks(segs, max_chars=ACCUMULATE_MAX):
    """Regroupe les segments en blocs : chaque bloc partira en UN message (≤ max_chars)."""
    blocks, cur, size = [], [], 0
    for s in segs:
        add = len(s) + (1 if cur else 0)
        if cur and size + add > max_chars:
            blocks.append(cur)
            cur, size = [], 0
            add = len(s)
        cur.append(s)
        size += add
    if cur:
        blocks.append(cur)
    return blocks

def paste_block(session, block):
    """Accumuler TOUS les segments d'un bloc dans le composer — chaque collage reste
    ≤ MAX_PASTE (garde anti-troncature) et est vérifié PAR CAPTURE avant le suivant —
    puis c'est l'appelant qui presse le Return unique. Le focus est remis par clic DOM
    au DÉBUT du bloc seulement (un clic au milieu repositionnerait le caret et les
    collages suivants s'inséreraient au mauvais endroit). True : bloc au complet dans
    le composer. False : échec après PASTE_ATTEMPTS (composer laissé vide : aucun
    fragment). None : juge indisponible — le Return sera tenté, mais la confirmation
    post-envoi exigera alors le texte intégral."""
    for attempt in range(1, PASTE_ATTEMPTS + 1):
        page = page_read(session)
        comp = find_composer(page) if page else None
        if comp is None or not focus_composer(session, comp):
            return False
        ok = True
        for j, seg in enumerate(block):
            payload = seg if j == 0 else " " + seg          # espace de couture
            if j < len(block) - 1:
                payload += " "
            sh(["ob-vnc", "--session", session, "paste", payload])
            time.sleep(1.2)
            # 1er collage : le composer doit être exactement le segment (il était vide) ;
            # suivants : il doit se terminer par le nouveau segment (la troncature coupe la fin)
            probe, has_text = composer_probe(session, seg, "exact" if j == 0 else "ends")
            if probe is None:                               # juge indisponible
                return None                                 # pas de nettoyage à l'aveugle
            if not probe:
                ok = False
                break
        if ok:
            whole = " ".join(block)
            final, has_text = composer_probe(session, whole, "full")
            if final:                                       # porte intégrale avant le Return
                return True
            if final is None:
                return None
            ok = False                                      # intégralité contestée : on refait
        if has_text:
            clear_composer(session)                         # focus prouvé : on vide et on refait
        # sinon : rien n'est arrivé (focus perdu ?) — re-clic DOM + re-collage
    return False

def confirm_send(page, segment, session=None, shot=None):
    """Confirmation par le TEXTE INTÉGRAL du bloc seulement : aperçu de liste
    « … Vous: <texte> », bulle du fil « Message envoyé … par Vous : <texte> », ou texte
    de page. Un fragment, un aperçu tronqué ou la seule carte de lien ne prouvent RIEN
    (c'est ce qui produisait le faux « sent » du 2026-10-21). Secours : capture soumise
    au juge (une URL peut partir en carte de lien jointe au message)."""
    if page and segment:
        full = norm(segment)
        if full:
            for e in page.get("elements", []):
                lbl = norm(e.get("label"))
                if full in lbl and ("vous:" in lbl or "vous :" in lbl or "message envoyé" in lbl):
                    return "confirmé par le texte intégral (DOM)"
            if full in norm(page.get("text")):
                return "confirmé par le texte intégral (page)"
    if session and shot:
        r = vision(session, shot,
                   "Texte attendu du dernier message envoyé : « %s »" % segment,
                   "Le dernier message envoyé par Vous dans le fil affiche-t-il ce texte "
                   "au complet (une URL peut apparaître en carte de lien jointe) ?")
        if r:
            return "confirmé par capture (texte intégral visible dans le fil)"
    return None

def send_block(session, block):
    """Un bloc : collages courts accumulés + vérifiés par capture, UN SEUL Return,
    confirmation sur le TEXTE INTÉGRAL du bloc. Retourne le détail de confirmation,
    ou None (échec — brouillon conservé)."""
    pasted = paste_block(session, block)
    if pasted is False:
        return None                           # accumulation jamais complète : pas de Return
    sh(["ob-vnc", "--session", session, "key", "return"])
    time.sleep(3.5)
    page = None
    for _ in range(CONFIRM_READS):
        page = page_read(session)
        hit = confirm_send(page, " ".join(block))
        if hit:
            return hit
        time.sleep(1.5)
    shot = snap(session)
    try:
        return confirm_send(page, " ".join(block), session=session, shot=shot)
    finally:
        if shot:
            try: os.unlink(shot)
            except OSError: pass

def residual_cleanup(session, expected):
    """Après une confirmation manquée : garantir qu'aucun fragment ne reste dans le
    composer. La capture décide ; sans juge, ne RIEN toucher (jamais de ctrl-a à l'aveugle)."""
    exact, has_text = composer_probe(session, expected, "exact")
    if exact or has_text:                     # texte encore en zone de saisie = non envoyé
        clear_composer(session)
        return "fragment retiré du composer (non envoyé)"
    if exact is False and has_text is False:
        return "composer déjà vide"
    return "composer non vérifiable (juge indisponible) — vérifier l'écran"

def send_via_composer(session, text):
    """Canal DOM v0.3.3 : collages courts (≤ %d car) accumulés dans le composer et
    vérifiés par capture, UN SEUL Return par bloc (≤ %d car — le destinataire reçoit
    UN message, pas une pile de bulles), confirmation sur le texte intégral de CHAQUE
    bloc. Un bloc non confirmé → send_failed, blocs restants NON envoyés, brouillon
    conservé, composer vide.""" % (MAX_PASTE, ACCUMULATE_MAX)
    page = page_read(session)
    if page is None:
        return {"ok": False, "detail": "page illisible avant envoi"}
    comp = find_composer(page)
    if comp is None:
        return {"ok": False, "detail": "composer introuvable dans le DOM (fil fermé ?)"}
    body = strip_emojis(text)
    segs = split_segments(body)
    if not segs:
        return {"ok": False, "detail": "texte vide (ou emojis seuls : le collage e2ee les perd)"}
    blocks = group_blocks(segs)
    note = ", emojis retirés (le collage e2ee les perd)" if EMOJI_RE.search(text or "") else ""
    for bi, block in enumerate(blocks, 1):
        hit = send_block(session, block)
        if hit:
            continue
        rest = len(blocks) - bi
        return {"ok": False, "detail": "bloc %d/%d non confirmé par son texte intégral — "
                "envoi interrompu (%d bloc(s) restant(s) NON envoyé(s)), brouillon conservé, %s"
                % (bi, len(blocks), rest, residual_cleanup(session, " ".join(block)))}
    return {"ok": True, "detail": "texte intégral confirmé : %d collage(s) de ≤ %d car, "
            "%d message(s) (bloc(s) de ≤ %d car)%s"
            % (len(segs), MAX_PASTE, len(blocks), ACCUMULATE_MAX, note)}

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
    out({"status": "sent", "to": sender, "chars": len(text), "sent_today": state["sends"][today],
         "detail": result.get("detail", "")})

if __name__ == "__main__":
    main()
