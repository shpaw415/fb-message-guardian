#!/usr/bin/env python3
"""Assemble open-bot.plugin.json : outils inline depuis tools/, cron, configs, skill."""
import json, os

BASE = os.path.expanduser("~/plugins-create/fb-message-guardian")

def tool(name, path):
    with open(os.path.join(BASE, path), encoding="utf-8") as f:
        return {"name": name, "content": f.read()}

with open(os.path.join(BASE, "open-bot.plugin.json"), encoding="utf-8") as f:
    m = json.load(f)

# Version stable : première publication marketplace.
m["version"] = "0.1.0"
m["configs"][0]["label"] = ("Mode de réponse : hybride (modèle si mot-clé, IA sinon), "
                            "auto (modèles + notifications) ou ia (tout rédigé par l'IA)")
m["tools"] = [
    tool("scan", "tools/scan.py"),
    tool("send", "tools/send.py"),
    tool("queue", "tools/queue.py"),
    tool("watch", "tools/watch.py"),
    tool("rules", "tools/rules.py"),
]
m["configs"].extend([
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
])
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

Gardien de boîte Facebook Messenger. Quatre outils : `ob-plugin-fb-message-guardian-scan`,
`-send`, `-queue`, `-watch` (tous exigent `--session <id d'écran du fil>`, sauf watch).

## Qui fait quoi
- **watch** (cron auto `fb-guardian-watcher`, ~3 min, ZÉRO token au repos) : scanne, notifie,
  envoie les réponses modèles, met en file. Il ne réveille l'agent QUE si sa sortie contient
  une ligne `[cron-agent]` : nouvelles réponses IA en attente (`await_ai`) ou scan
  `dom_changed`. Sinon silence total : aucune passe d'agent, aucun message.
- **l'agent** (réveillé à la demande par la ligne `[cron-agent]`, qui donne la session
  d'écran) : ne traite QUE les `await_ai` (rédaction IA, photos à router) ou un diagnostic
  `dom_changed`. Chaque entrée se termine par
  `queue --set-text <id> "<réponse>"` (brouillon à approuver), un envoi `send --pending`,
  ou `queue --done <id>` (traitée ou hors périmètre) — JAMAIS laissée
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
  `~/workspace/<projet>/fb-guardian/` (tâche `route_photo` pour l'agent).
Commandes : `--list`, `--add --sender X --contains "a,b" --photo --reply "..." / --notify "..." / --save-photo projet [--priority N]`,
`--enable|--disable|--remove ID`, `--test --sender X --text "aperçu"`.
Pour éditer à la main : le JSON est un simple tableau de règles, un objet par règle.

## Règles permanentes
- Jamais de mot de passe : reconnecter l'utilisateur est une action humaine sur l'écran du bureau.
- Jamais deux réponses au même message : le registre du scan fait foi.
- En envoi direct, l'expéditeur doit être sur la liste blanche ; sinon brouillon + approbation.
- Le plafond quotidien et les heures actives sont des portes DURES : même `--force` ne les contourne pas.
- Les brouillons partent seulement après le oui explicite de l'utilisateur (« envoie p-XXXX »).
- Passes sans travail = silence : ne jamais commenter un cycle où il n'y avait rien à faire,
  et ne jamais détailler les messages ignorés — ils n'existent pas pour l'utilisateur.

## Commandes utiles
- Scan + plan : `ob-plugin-fb-message-guardian-scan --session S` (`--force` ignore l'intervalle)
- Contexte d'un fil pour rédiger : `...-scan --session S --thread "Nom"`
- File d'attente : `...-queue --list` (n'expose que les entrées ouvertes en `open[]`),
  `--approve p-XXXX`, `--set-text p-XXXX "texte"` (brouillon), `--done p-XXXX` (traitée),
  `--skip p-XXXX "raison"` (doublon)
- Envoi d'un brouillon approuvé : `...-send --session S --pending p-XXXX`
- Envoi direct demandé par l'utilisateur : `...-send --session S --to "Nom" --text "..." --force`
- Veille manuelle : `...-watch` (une passe scriptée, silence si rien de neuf)

## Statuts du scan
`too_soon` (intervalle pas écoulé), `ok` (plan dans `new[]` + `await_ai`), `not_logged_in`/`needs_user`
(session fermée ou écran à rendre — notifier, ne pas retenter), `dom_changed`/`error`
(sélecteurs à réparer : Facebook a changé son DOM — réparer parse_rows/parse_row dans le scan).
Les aperçus e2ee sans texte (« chiffrement… ») et les groupes sont ignorés faute d'aperçu lisible.
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
