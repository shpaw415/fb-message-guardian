#!/usr/bin/env python3
"""Assemble open-bot.plugin.json : outils inline depuis tools/, cron, configs, skill."""
import json, os

BASE = os.path.expanduser("~/plugins-create/fb-message-guardian")

def tool(name, path):
    with open(os.path.join(BASE, path), encoding="utf-8") as f:
        return {"name": name, "content": f.read()}

with open(os.path.join(BASE, "open-bot.plugin.json"), encoding="utf-8") as f:
    m = json.load(f)

# Version 0.2.0 : ajout du canal API officielle Meta (messages de la Page).
m["version"] = "0.2.0"
m["configs"][0]["label"] = ("Mode de réponse : hybride (modèle si mot-clé, IA sinon), "
                            "auto (modèles + notifications) ou ia (tout rédigé par l'IA)")
m["tools"] = [
    tool("scan", "tools/scan.py"),
    tool("api-scan", "tools/api_scan.py"),
    tool("send", "tools/send.py"),
    tool("queue", "tools/queue.py"),
    tool("watch", "tools/watch.py"),
    tool("rules", "tools/rules.py"),
]
extras = [
    {
        "key": "source",
        "label": "Source : dom (MP perso via le navigateur), api (Page via l'API officielle Meta) ou dom+api (les deux)",
        "def": "dom",
        "type": "enum",
        "options": ["dom", "api", "dom+api"]
    },
    {
        "key": "fb_page_id",
        "label": "Id de la Page Facebook à surveiller via l'API (vide = canal api inactif)",
        "def": "",
        "type": "string"
    },
    {
        "key": "fb_page_token",
        "label": "Token d'accès Page (secret) pour l'API Graph Meta — permissions pages_messaging et pages_read_engagement",
        "def": "",
        "type": "secret"
    },
    {
        "key": "api_graph_version",
        "label": "Version de l'API Graph Meta (ne changer que si Meta déprécie la version par défaut)",
        "def": "v21.0",
        "type": "string"
    },
    {
        "key": "watcher_enabled",
        "label": "Veille scriptée active : scan toutes les ~3 min sans agent, réponses modèles instantanées (zéro token au repos)",
        "def": "true",
        "type": "bool"
    },
    {
        "key": "screen_session",
        "label": "Id de session d'écran fixe pour la veille (vide = détection automatique de l'écran vivant)",
        "def": "",
        "type": "string"
    },
]
m["configs"] = [c for c in m["configs"] if c["key"] not in {x["key"] for x in extras}]
m["configs"].extend(extras)
# Persona installée avec le plugin : sélectionnable à la création d'un fil pour gérer
# le gardien (règles, file, diagnostics) dans la voix voulue par Justin.
m["personas"] = [{
    "name": "Gestion Gardien FB",
    "instruction": (
        "Tu gères le gardien de messages Facebook (plugin fb-message-guardian) de Justin, "
        "en français, de façon courte et concrète. Tu crées et ajustes ses règles de réponse "
        "(le carnet de règles), tu vérifies la file des brouillons, tu lances des scans "
        "manuels et tu diagnostiques pourquoi aucune réponse n'est partie — le skill "
        "fb-message-guardian se charge automatiquement dès qu'on parle du plugin. Quand "
        "Justin décrit un cas (« si untel dit X, réponds Y » ou « ses photos vont dans tel "
        "projet »), tu traduis en règle, tu la montres et tu l'ajoutes après son accord. Tu "
        "ne réponds JAMAIS toi-même aux messages Facebook sans son oui explicite : les "
        "brouillons partent seulement après « envoie p-XXXX ». Jamais de mot de passe, "
        "jamais hors des portes du plugin (heures actives, plafond, liste blanche, "
        "approbation). Les messages hors règles s'ignorent en silence. Va droit au problème "
        "et propose un correctif plutôt qu'une explication longue."
    ),
}]

# Plus AUCUN cron prompt dans le manifeste : depuis open-bot en mode hybride, l'agent
# n'est réveillé que par le watcher scripté (ligne [cron-agent]) quand il y a du travail.
# Le cron auto lui-même vit dans ob-cron (job « fb-guardian-watcher », --run auto) —
# voir README §7 pour la commande de recréation après un reset du desktop.
m["cron"] = []

m["skills"][0]["body"] = """# fb-message-guardian

Gardien de boîte Facebook Messenger. Cinq outils : `ob-plugin-fb-message-guardian-scan`,
`-api-scan`, `-send`, `-queue`, `-watch` (scan et send exigent `--session <id d'écran>`
pour le canal DOM ; le canal API n'a besoin d'aucun écran).

## Deux canaux, une même file
- **canal DOM** (config `source` = dom ou dom+api) : Messenger personnel, lu via le
  navigateur. L'API officielle Meta ne couvre PAS les Messenger personnels — seule
  surface officielle : les messages d'une Page.
- **canal API officielle** (source = api ou dom+api) : messages de la Page Facebook, lus
  par `api-scan` via le Graph API (`/{page}/conversations`), réponses envoyées par
  `POST /{page}/messages`. Zéro écran, zéro navigateur : fonctionne même navigateur
  fermé. Nécessite `fb_page_id` + `fb_page_token` (secret) dans les réglages du plugin.
- Le canal API suit exactement les mêmes règles, mêmes portes (heures, plafond, mode
  d'envoi, liste blanche) et la même file que le canal DOM. Chaque entrée api porte
  `channel: "api"` et `psid` : un `send --pending p-XXXX` route tout seul vers l'API —
  et refusera si la fenêtre de 24 h de Meta est fermée (porte Meta, non contournable).

## Qui fait quoi
- **watch** (cron auto `fb-guardian-watcher`, ~3 min, ZÉRO token au repos) : lance les
  passes des canaux actifs, notifie, envoie les réponses modèles, met en file. Il ne
  réveille l'agent QUE si sa sortie contient une ligne `[cron-agent]` : nouvelles
  réponses IA en attente (`await_ai`), scan `dom_changed`, ou échec api (1
  diagnostic/jour max). Sinon silence total : aucune passe d'agent, aucun message.
- **l'agent** (réveillé à la demande par la ligne `[cron-agent]`) : ne traite QUE les
  `await_ai` (rédaction IA, photos à router) ou un diagnostic. Chaque entrée se termine
  par `queue --set-text <id> "<réponse>"` (brouillon à approuver), un envoi
  `send --pending`, ou `queue --done <id>` (traitée ou hors périmètre) — JAMAIS laissée
  ouverte. Une même entrée n'est réveillée que 3 fois ; ensuite c'est une notification
  « file bloquée », pas un nouveau réveil.
- **Ignorer en silence** : un message qui ne correspond à aucune règle, qui est un doublon
  ou déjà couvert se retire avec `queue --done <id>` et N'APPARAÎT NULLE PART — ni raison
  rédigée, ni mention dans la conclusion. La conclusion ne liste QUE les actions réelles
  (brouillon déposé, envoi parti, photo routée) ; s'il n'y en a aucune, la réponse entière
  est une seule ligne : « rien ».
- **l'utilisateur** : approuve les brouillons « envoie p-XXXX ».

## La personnalité des réponses (le carnet de règles)
Le fichier `~/.config/open-bot/fb-message-guardian-rules.json` (géré par
`ob-plugin-fb-message-guardian-rules`) définit COMMENT répondre à QUI, avant le comportement
par défaut. Premier match (priorité croissante) gagne. Une règle : qui (`sender`, sous-chaîne)
+ quoi (`contains`, et/ou `photo: true`) → action :
- `reply` — réponse personnalisée (`{expediteur}` remplacé) ; en direct si les portes globales
  (mode d'envoi, liste blanche, heures, plafond) le permettent, sinon brouillon ;
- `notify` — notification personnalisée au lieu d'une réponse ;
- `save_photo_to` — nom d'un projet open-bot : les photos du fil partent dans
  `~/workspace/<projet>/fb-guardian/` (tâche `route_photo` pour l'agent). Sur le canal
  api, la photo vit dans l'inbox de la Page (Business Suite) : y aller ou skipper.
Commandes : `--list`, `--add --sender X --contains "a,b" --photo --reply "..." / --notify "..." / --save-photo projet [--priority N]`,
`--enable|--disable|--remove ID`, `--test --sender X --text "aperçu"`.
Pour éditer à la main : le JSON est un simple tableau de règles, un objet par règle.

## Règles permanentes
- Jamais de mot de passe : reconnecter l'utilisateur est une action humaine sur l'écran du bureau.
- Jamais deux réponses au même message : le registre du scan fait foi (canal DOM : aperçu
  normalisé ; canal api : id de message Meta, dédoublonnage exact).
- En envoi direct, l'expéditeur doit être sur la liste blanche ; sinon brouillon + approbation.
- Le plafond quotidien et les heures actives sont des portes DURES : même `--force` ne les contourne pas.
- La fenêtre de messagerie de 24 h de Meta est une porte du canal api : au-delà, refus clair.
- Les brouillons partent seulement après le oui explicite de l'utilisateur (« envoie p-XXXX »).
- Passes sans travail = silence : ne jamais commenter un cycle où il n'y avait rien à faire,
  et ne jamais détailler les messages ignorés — ils n'existent pas pour l'utilisateur.

## Commandes utiles
- Scan DOM + plan : `ob-plugin-fb-message-guardian-scan --session S` (`--force` ignore l'intervalle)
- Scan API + plan : `ob-plugin-fb-message-guardian-api-scan` (aucun écran requis)
- Contexte d'un fil DOM pour rédiger : `...-scan --session S --thread "Nom"` (canal api :
  relire la conversation via le Graph API si besoin de contexte)
- File d'attente : `...-queue --list` (n'expose que les entrées ouvertes en `open[]`),
  `--approve p-XXXX`, `--set-text p-XXXX "texte"` (brouillon), `--done p-XXXX` (traitée),
  `--skip p-XXXX "raison"` (doublon)
- Envoi d'un brouillon approuvé : `...-send --session S --pending p-XXXX`
  (le canal — DOM ou API — est choisi par l'entrée elle-même)
- Envoi direct demandé par l'utilisateur : DOM `...-send --session S --to "Nom" --text "..." --force`,
  API `...-send --psid PSID --to "Nom" --text "..."`
- Veille manuelle : `...-watch` (une passe scriptée des canaux actifs, silence si rien de neuf)

## Statuts des scans
DOM : `too_soon` (intervalle pas écoulé), `ok` (plan dans `new[]` + `await_ai`),
`not_logged_in`/`needs_user` (session fermée ou écran à rendre — notifier, ne pas
retenter), `dom_changed`/`error` (sélecteurs à réparer : Facebook a changé son DOM —
réparer parse_rows/parse_row dans le scan). Les aperçus e2ee sans texte (« chiffrement… »)
et les groupes sont ignorés faute d'aperçu lisible.
API : `disabled` (source=dom), `unconfigured` (fb_page_id/fb_page_token manquants),
`api_error` (token expiré, permissions, réseau — le detail dit quoi), `ok`. Un échec api
ne réveille l'agent qu'une fois par jour.
"""

# Déduplique les configs par clé (les builds répétés étendaient la liste sans vérifier).
seen, deduped = set(), []
for c in m["configs"]:
    if c["key"] in seen:
        continue
    seen.add(c["key"])
    deduped.append(c)
m["configs"] = deduped

with open(os.path.join(BASE, "open-bot.plugin.json"), "w", encoding="utf-8") as f:
    json.dump(m, f, ensure_ascii=False, indent=2)
print("manifest assemble:", m["version"], "- outils:", [t["name"] for t in m["tools"]])
