#!/usr/bin/env python3
"""Stress-test du canal DOM de fb-message-guardian (send v0.3.2) — régression du bug
« message long tronqué mais confirmé sent » (rapport 3764a31d670d8454, constat 2026-10-21).

Simule messenger.com e2ee avec des stubs ob-page / ob-vnc / ob-system1 (PATH) et un
composer qui peut tronquer les collages. Chaque cas imprime PASS/FAIL ; code de sortie =
nombre d'échecs. N'utilise AUCUN fichier de prod (réglages/état surchargés par env)."""
import importlib.util, json, os, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SEND = os.path.join(HERE, "..", "tools", "send.py")
TMP = "/tmp/opencode/fbg_dom"
STUBS = TMP + "/stubs"
STUB_STATE = TMP + "/stub_state.json"
SETTINGS = TMP + "/settings.json"
STATE = TMP + "/state.json"

PNG1x1 = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
          b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00"
          b"\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")

LONG_TEXT = ("Bonjour Cliente ! Merci pour ta patience. Voici le bilan mensuel automatique : "
             "promenades 12, visites vétérinaires 1, repas spéciaux 4, siestes comptabilisées "
             "séparément. Le rapport complet est disponible ici : "
             "https://exemple.m2-tech.ca/bilan/florie?mois=octobre&token=abc123 . "
             "Dis-moi si tu veux le détail d'une ligne, je peux préparer un résumé court ou "
             "exporter le tout en PDF pour ton dossier. " + "Point de suivi à noter. " * 12 +
             "Belle journée ! 🐾")

PAGE_STUB = r'''#!/usr/bin/env python3
import json, os, sys
a = sys.argv[1:]
st = json.load(open(os.environ["FBGUARD_STUB_STATE"], encoding="utf-8"))
if "click" in a:
    st.setdefault("clicks", []).append(a[-1])
    json.dump(st, open(os.environ["FBGUARD_STUB_STATE"], "w", encoding="utf-8"), ensure_ascii=False)
    print(json.dumps({"ok": True})); sys.exit(0)
labels = ["Écrire à Cliente Exemple"] + st.get("thread", []) + st.get("previews", [])
els = [{"id": "comp1", "role": "textbox", "label": "Écrire à Cliente Exemple", "editable": True}]
els += [{"id": "b%d" % i, "role": "generic", "label": l} for i, l in enumerate(st.get("thread", []))]
els += [{"id": "p%d" % i, "role": "generic", "label": l} for i, l in enumerate(st.get("previews", []))]
print(json.dumps({"url": "https://www.messenger.com/e2ee/t/florie",
                  "viewport": {"width": 1280, "height": 800},
                  "elements": els, "text": " ".join(labels)}, ensure_ascii=False))
'''

VNC_STUB = r'''#!/usr/bin/env python3
import json, os, sys
a = sys.argv[1:]
if a[:1] == ["--session"]:
    a = a[2:]                     # ob-vnc --session S <cmd…>
st = json.load(open(os.environ["FBGUARD_STUB_STATE"], encoding="utf-8"))
if a and a[0] == "paste":
    txt = a[1] if len(a) > 1 else ""
    st.setdefault("pastes", []).append(txt)
    lim = 200 if st.get("scenario") == "truncate200" else 10 ** 9
    st["composer"] = (st.get("composer") or "") + txt[:lim]
elif a and a[0] == "key":
    st.setdefault("keys", []).append(a[1])
    if a[1] == "ctrl-a":
        st.setdefault("ctrla_composer", []).append(st.get("composer") or "")
    if a[1] == "delete":
        st["composer"] = ""
    if a[1] == "return" and st.get("composer"):
        body = st["composer"]
        if "partial" in str(st.get("scenario", "")):
            st.setdefault("thread", []).append(
                "Saisir, Message envoyé à … par Vous : " + body[:60] + "…")
            st.setdefault("previews", []).append("Cliente Exemple Vous: " + body[:40] + "…")
            st.setdefault("thread", []).append("Carte de lien jointe au message")
        else:
            st.setdefault("thread", []).append(
                "Saisir, Message envoyé à Cliente Exemple par Vous : " + body)
            st.setdefault("previews", []).append("Cliente Exemple Vous: " + body)
        st["composer"] = ""
elif a and a[0] == "capture":
    open(a[1], "wb").write(b"png")
    if "2" in a:
        print("capture ok live 1280x800 factor 1.0 offset 0,0")
    else:
        print("capture ok")
elif a and a[:1] == ["move"]:
    st.setdefault("clicks", []).append("pixel")
json.dump(st, open(os.environ["FBGUARD_STUB_STATE"], "w", encoding="utf-8"), ensure_ascii=False)
'''

SYSTEM1_STUB = r'''#!/usr/bin/env python3
import json, os, re, sys
a = sys.argv[1:]
st = json.load(open(os.environ["FBGUARD_STUB_STATE"], encoding="utf-8"))
if str(st.get("scenario", "")).startswith("nojudge"):
    print(json.dumps({"ok": False, "detail": "unconfigured"})); sys.exit(3)
ctx = a[a.index("--state") + 1] if "--state" in a else ""
m = re.search(r"« (.*) »", ctx)
expected = m.group(1) if m else ""
comp = st.get("composer") or ""
thread = st.get("thread") or []
answers, qs = {}, 0
i = 0
while i < len(a):
    if a[i] == "--choice":
        q, qs = a[i + 1], qs + 1
        if "EXACTEMENT" in q:
            ch = "oui" if comp.rstrip() == expected.strip() else "non"
        elif "SE TERMINE" in q:
            ch = "oui" if comp.rstrip().endswith(expected.strip()) else "non"
        elif "INTÉGRALITÉ" in q:
            ch = "oui" if " ".join(comp.split()) == " ".join(expected.split()) else "non"
        elif "contient-elle du texte" in q:
            ch = "oui" if comp else "non"
        elif "dernier message envoyé" in q:
            ch = "oui" if thread and expected in thread[-1] else "non"
        else:
            ch = "non"
        answers["q%d" % qs] = {"choice": ch, "confidence": 0.99, "valid": True}
        st.setdefault("judges", []).append({"q": q, "choice": ch})
        i += 4                              # --choice Q oui non
    else:
        i += 1
json.dump(st, open(os.environ["FBGUARD_STUB_STATE"], "w", encoding="utf-8"), ensure_ascii=False)
print(json.dumps({"ok": True, "answers": answers}))
'''

fails = []

def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ((" — " + str(detail)[:220]) if not cond else ""))
    if not cond:
        fails.append(name)

def write_stubs():
    os.makedirs(STUBS, exist_ok=True)
    for name, body in (("ob-page", PAGE_STUB), ("ob-vnc", VNC_STUB), ("ob-system1", SYSTEM1_STUB)):
        p = STUBS + "/" + name
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        os.chmod(p, 0o755)

def fresh(scenario, pending_text=None):
    os.makedirs(TMP, exist_ok=True)
    stub = {"scenario": scenario, "composer": "", "thread": [], "previews": [],
            "keys": [], "pastes": [], "clicks": [], "judges": [], "ctrla_composer": []}
    with open(STUB_STATE, "w", encoding="utf-8") as f:
        json.dump(stub, f, ensure_ascii=False)
    with open(SETTINGS, "w", encoding="utf-8") as f:
        json.dump({"settings": {"active_hours_start": 0, "active_hours_end": 23,
                                "send_mode": "direct", "whitelist": "Cliente"}}, f)
    state = {"conversations": {}, "sends": {}, "sent_log": [],
             "pending": [{"id": "p-t1", "sender": "Cliente Exemple", "text": pending_text or "",
                          "status": "await_approval", "plan": "send:ai"}]}
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    return stub

def run_send(extra_args):
    env = dict(os.environ)
    env["PATH"] = STUBS + os.pathsep + env["PATH"]
    env["FBGUARD_SETTINGS"] = SETTINGS
    env["FBGUARD_STATE"] = STATE
    env["FBGUARD_STUB_STATE"] = STUB_STATE
    env.pop("FBGUARD_GRAPH_BASE", None)
    p = subprocess.run([sys.executable, SEND, "--session", "screens", "--force"] + extra_args,
                       capture_output=True, text=True, timeout=120, env=env)
    lines = (p.stdout or "").strip().splitlines()
    try:
        js = json.loads(lines[-1]) if lines else {}
    except Exception:
        js = {}
    with open(STUB_STATE, encoding="utf-8") as f:
        stub = json.load(f)
    with open(STATE, encoding="utf-8") as f:
        state = json.load(f)
    return p.returncode, js, stub, state

def unit_module():
    spec = importlib.util.spec_from_file_location("fgsend", SEND)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def main():
    shutil.rmtree(TMP, ignore_errors=True)
    write_stubs()
    mod = unit_module()

    flat = " ".join(LONG_TEXT.split())
    body = mod.strip_emojis(LONG_TEXT)
    flat_body = " ".join(body.split())
    segs = mod.split_segments(body)
    check("U1 segmente ≤ %d car" % mod.MAX_PASTE, all(len(s) <= mod.MAX_PASTE for s in segs),
          [len(s) for s in segs])
    check("U2 segments reconstituent le texte", " ".join(segs) == flat_body)
    check("U3 emojis retirés du texte à coller", "🐾" not in body and "🐾" in LONG_TEXT)
    check("U4 texte > 600 car (cas du rapport)", len(LONG_TEXT) > 600, len(LONG_TEXT))
    check("U5 coupe sèche d'un mot très long",
          mod.split_segments("x" * 700) == ["x" * 280, "x" * 280, "x" * 140])
    check("U6 phrase courte intacte", mod.split_segments("Bonjour !") == ["Bonjour !"])
    blocks = mod.group_blocks(["a" * 500, "b" * 500, "c" * 500])
    check("U7 blocs ≤ %d car" % mod.ACCUMULATE_MAX,
          blocks == [["a" * 500, "b" * 500], ["c" * 500]] and
          all(" ".join(b).__len__() <= mod.ACCUMULATE_MAX for b in blocks), blocks)

    # C2 — confirmation : texte intégral exigé, aperçu partiel/lien seul rejeté
    seg = segs[0]
    full_page = {"elements": [{"label": "Cliente Exemple Vous: " + seg}],
                 "text": "Cliente Exemple Vous: " + seg}
    part_page = {"elements": [{"label": "Cliente Exemple Vous: " + seg[:40] + "…"},
                              {"label": "Carte de lien jointe au message"}],
                 "text": "Cliente Exemple Vous: " + seg[:40] + "… Carte de lien jointe au message"}
    check("C2 texte intégral confirmé", mod.confirm_send(full_page, seg) is not None)
    check("C2 aperçu partiel/lien seul rejeté", mod.confirm_send(part_page, seg) is None)

    # C3 — message long (>600 car, URL + emoji) : collages courts accumulés, UN message
    fresh("ok", LONG_TEXT)
    rc, js, stub, state = run_send(["--pending", "p-t1"])
    sent = [b.split("par Vous : ", 1)[1] for b in stub["thread"]
            if "par Vous : " in b and "Message envoyé" in b]
    check("C3 status sent", rc == 0 and js.get("status") == "sent", js)
    check("C3 chaque collage ≤ %d car" % mod.MAX_PASTE,
          all(len(p) <= mod.MAX_PASTE for p in stub["pastes"]), [len(p) for p in stub["pastes"]])
    check("C3 UN SEUL message = texte intégral",
          len(sent) == 1 and " ".join(sent[0].split()) == flat_body,
          (len(sent), [len(s) for s in sent], len(flat_body)))
    check("C3 n collages = n segments, 1 seul Return",
          len(stub["pastes"]) == len(segs) and stub["keys"].count("return") == 1,
          (len(stub["pastes"]), stub["keys"].count("return"), len(segs)))
    check("C3 URL intacte dans le fil",
          any("https://exemple.m2-tech.ca/bilan/florie?mois=octobre&token=abc123" in b
              for b in stub["thread"]))
    check("C3 emoji absent des bulles envoyées", all("🐾" not in b for b in stub["thread"]))
    check("C3 aucun ctrl-a (aucun collage raté)", stub["ctrla_composer"] == [])
    check("C3 juge : 1 exact + %d fin + 1 intégralité" % (len(segs) - 1),
          len([j for j in stub["judges"] if "EXACTEMENT" in j["q"]]) == 1 and
          len([j for j in stub["judges"] if "SE TERMINE" in j["q"]]) == len(segs) - 1 and
          len([j for j in stub["judges"] if "INTÉGRALITÉ" in j["q"]]) == 1,
          stub["judges"])
    check("C3 détail mentionne les emojis retirés", "emojis" in js.get("detail", ""), js.get("detail"))
    check("C3 brouillon retiré de la file après envoi",
          "p-t1" not in [e["id"] for e in state["pending"]])

    # C4 — composer tronque les collages : jamais de Return, zéro fragment, brouillon gardé
    fresh("truncate200", LONG_TEXT)
    rc, js, stub, state = run_send(["--pending", "p-t1"])
    check("C4 send_failed (jamais confirmé)", rc == 2 and js.get("status") == "send_failed", js)
    check("C4 aucun Return pressé", "return" not in stub["keys"], stub["keys"])
    check("C4 aucun fragment laissé", stub["composer"] == "", stub["composer"])
    check("C4 ctrl-a seulement composer prouvé non vide",
          stub["ctrla_composer"] and all(stub["ctrla_composer"]), stub["ctrla_composer"])
    check("C4 brouillon conservé", "p-t1" in [e["id"] for e in state["pending"]])
    check("C4 détail cite le bloc bloqué", "bloc 1/" in js.get("detail", ""), js.get("detail"))

    # C5 — confirmation impossible : aperçu tronqué + carte de lien seulement (bug v0.3.1)
    short = "Voici le lien du bilan : https://exemple.m2-tech.ca/bilan/florie?token=abc123 et le résumé complet joint."
    fresh("partial_preview", short)
    rc, js, stub, state = run_send(["--pending", "p-t1"])
    check("C5 send_failed (aperçu partiel insuffisant)", rc == 2 and js.get("status") == "send_failed", js)
    check("C5 juge de bulle consulté et défavorable",
          any("dernier message envoyé" in j["q"] and j["choice"] == "non" for j in stub["judges"]),
          stub["judges"])
    check("C5 un seul segment tenté (pas d'empilement)", len(stub["pastes"]) == 1, stub["pastes"])
    check("C5 composer vide (aucun fragment)", stub["composer"] == "")
    check("C5 brouillon conservé", "p-t1" in [e["id"] for e in state["pending"]])

    # C6 — juge indisponible : envoi tenté, confirmation stricte par DOM passe
    fresh("nojudge", short)
    rc, js, stub, state = run_send(["--pending", "p-t1"])
    check("C6 sans juge : envoyé si le DOM confirme au complet", rc == 0 and js.get("status") == "sent", js)
    check("C6 aucun ctrl-a sans preuve de focus", stub["ctrla_composer"] == [])

    # C7 — juge indisponible ET aperçu partiel : échec propre, brouillon gardé, rien touché
    fresh("nojudge_partial", short)
    rc, js, stub, state = run_send(["--pending", "p-t1"])
    check("C7 sans juge ni confirmation : send_failed", rc == 2 and js.get("status") == "send_failed", js)
    check("C7 brouillon conservé", "p-t1" in [e["id"] for e in state["pending"]])
    check("C7 aucun ctrl-a sans preuve (juge off)", stub["ctrla_composer"] == [])
    check("C7 composer vide (Return a consommé le collage)", stub["composer"] == "")

    # C8 — texte > ACCUMULATE_MAX : 2 blocs = 2 messages, intégralité confirmée par bloc
    very_long = LONG_TEXT + " Complément du dossier : " + ("Détail vérifié et à jour. " * 40)
    fresh("ok", very_long)
    rc, js, stub, state = run_send(["--pending", "p-t1"])
    vflat = " ".join(mod.strip_emojis(very_long).split())
    vsent = [b.split("par Vous : ", 1)[1] for b in stub["thread"]
             if "par Vous : " in b and "Message envoyé" in b]
    check("C8 status sent (>1200 car)", rc == 0 and js.get("status") == "sent", js)
    check("C8 deux blocs = deux messages",
          stub["keys"].count("return") == 2 and len(vsent) == 2,
          (stub["keys"].count("return"), len(vsent)))
    check("C8 chaque bloc ≤ %d car" % mod.ACCUMULATE_MAX,
          all(len(s) <= mod.ACCUMULATE_MAX for s in vsent), [len(s) for s in vsent])
    check("C8 blocs reconstituent le texte intégral",
          " ".join(" ".join(s.split()) for s in vsent) == vflat,
          (len(" ".join(vsent)), len(vflat)))
    check("C8 détail annonce 2 messages", "2 message(s)" in js.get("detail", ""), js.get("detail"))

    print("---")
    print("ÉCHECS: %d %s" % (len(fails), fails) if fails else "TOUT PASSE")
    sys.exit(1 if fails else 0)

if __name__ == "__main__":
    main()
