# FB Message Guardian — Documentation

Gardien de boîte Facebook Messenger pour open-bot. Il surveille les messages non lus du
compte Facebook connecté dans le navigateur du bureau, décide quoi faire selon TES règles,
et répond automatiquement — avec un brouillon notifié à toi avant tout envoi, par défaut.

Version 0.5.2-beta (développement) · Auteur : Justin Hallé · Projet : `~/workspace/fb-message-guardian/`
Repo prévu : `shpaw415/fb-message-guardian` (pas encore publié au marketplace)

---

## 1. Le workflow complet (deux étages, un seul cron hybride)

### Étage 1 — La veille scriptée (`ob-plugin-fb-message-guardian-watch`, toutes les 3 min, ZÉRO token)

Un cron **hybride** (`ob-cron`, nom `fb-guardian-watcher`, `--run auto`) lance
`ob-plugin-fb-message-guardian-watch`. Le script tourne à chaque passe ; l'agent n'est
réveillé QUE si une ligne de sa sortie commence par `[cron-agent]`. Le script :

1. Trouve un écran vivant (réglage `screen_session`, sinon détection automatique dans
   `~/.open-bot/vnc/ses_*`, la session la plus récente qui répond d'abord).
2. Lance le scan avec un intervalle court (3 min au lieu du réglage global).
3. Exécute lui-même tout ce qui est déterministe :
   - **notify** → notification tableau de bord ;
   - **send:template** → remplit `{expediteur}` et envoie (réponse partie dans les ~3 min) ;
   - **draft côté modèle** → brouillon mis en file + notification « envoie p-XXXX » ;
4. Décide du réveil de l'agent :
   - **rien à faire** → aucune ligne `[cron-agent]` : passe 100 % silencieuse (zéro token,
     zéro message dans le fil du job — c'est LA différence avec l'ancien cron prompt 15 min) ;
   - **réponses IA en attente** (`await_ai`) → ligne `[cron-agent]` immédiate (nouvelle
     entrée), puis au plus une relance par intervalle de scan tant que le travail traîne ;
   - **`dom_changed`** → une ligne `[cron-agent]` de diagnostic, 1 fois par jour max.

### Étage 2 — L'agent, réveillé à la demande (tokens seulement s'il y a du travail IA)

La ligne `[cron-agent]` du watcher (qui contient la session d'écran et l'état) + le
message du job `fb-guardian-watcher` réveillent un agent qui ne traite QUE les entrées
`await_ai` : il lit le fil, rédige en français, envoie (si les portes direct étaient déjà
validées au scan) ou dépose le brouillon en attente de ton oui — puis termine en 2-3 lignes.

```
 ┌ chaque 3 min : WATCH (script, 0 token au repos) ─┐    ┌ agent, SEULEMENT si [cron-agent] ──┐
 │ scan → notify / réponse modèle / file            │───▶│ rédige les réponses IA en attente  │
 │ rien à faire → silence, personne n'est réveillé  │    │ ou diagnostique un dom_changed     │
 └──────────────────────────────────────────────────┘    └────────────────────────────────────┘
                     ▲      toi : « envoie p-XXXX » dans n'importe quel fil
```

### Le scan (`ob-plugin-fb-message-guardian-scan`)

Un script pur, sans IA :

1. S'assure que le navigateur est sur `https://www.messenger.com/` (jamais
   facebook.com/messages : sa vue e2ee ne montre pas la liste des conversations).
2. Lit le DOM via `ob-page` : compteurs de non lus (`Discussions · 7 non lus`), et chaque
   conversation (nom + aperçu du dernier message + heure, préfixe `Vous: ` si le dernier
   message est le tien).
3. **Détection de session** : formulaire de connexion / bouton « Se connecter » →
   `not_logged_in`. Le plugin ne touchera JAMAIS au mot de passe : il te notifie (1 fois
   par jour max) et attend que tu te reconnectes à la main sur l'écran.
4. Compare avec le registre `~/.open-bot/fb-message-guardian/state.json` :
   un aperçu qui change côté « pas moi » = nouveau message entrant.
5. Applique tes règles et produit le plan (voir §2).

### Étape 2 — Les actions

- **Réponses modèles** : le watcher script les envoie seul, dans les 3 minutes.
- **Réponses IA** : l'agent du cycle 15 min lit le fil (`scan --thread "Nom"`), rédige,
  puis envoie (portes direct déjà validées) ou dépose un brouillon.
- **Brouillons (approbation)** : réponse mise en file (`queue`) avec un id `p-XXXX` et une
  notification. Tu approuves en disant **« envoie p-XXXX »** dans n'importe quel fil.
- **Notifications** : rien n'est envoyé ; tu es juste informé.

### Étape 3 — L'envoi (`ob-plugin-fb-message-guardian-send`)

Le composer Messenger n'est pas exposé dans l'arbre accessible du navigateur (~24 éléments
visibles seulement). L'envoi passe donc par la voie pixels : clic dans le composer,
collage du texte, Entrée, puis **confirmation** en relisant l'aperçu de la conversation
(il doit passer à `Vous: <texte>`). 3 essais, sinon échec propre avec rapport.

---

## 2. La logique de décision (les portes)

Chaque message entrant traverse les portes dans l'ordre. TOUT doit passer :

| # | Porte | Réglage | Effet si bloquée |
|---|-------|---------|------------------|
| 1 | Mot-clé | `trigger_keywords` | détermine template ou IA (selon `reply_mode`) |
| 2 | Heures actives | `active_hours_start/end` | action rétrogradée en `notify` |
| 3 | Plafond quotidien | `daily_reply_cap` | idem |
| 4 | Mode d'envoi | `send_mode` = approbation | action devient `draft` |
| 5 | Liste blanche | `whitelist` | action devient `draft` |

Modes de réponse (`reply_mode`) :

- **hybride** (défaut) — mot-clé trouvé → réponse modèle ; sinon → rédaction IA.
- **auto** — mot-clé trouvé → réponse modèle ; sinon → simple notification (zéro token).
- **ia** — tout est rédigé par l'IA.

En **approbation** (défaut), rien ne part sans ton « envoie p-XXXX ». En **direct**, seuls
les expéditeurs de la liste blanche reçoivent une réponse automatique.

---

## 2-bis — La personnalité des réponses (le carnet de règles, v0.5.0)

Le carnet `~/.config/open-bot/fb-message-guardian-rules.json`, géré par
`ob-plugin-fb-message-guardian-rules`, définit COMMENT répondre à QUI — et il est consulté
AVANT le comportement par défaut. Premier match (priorité croissante) gagne.

Une règle = qui (`sender`, sous-chaîne) + quoi (`contains` mots-clés, et/ou `photo: true`)
→ une ou plusieurs actions :

| Action | Effet |
|--------|-------|
| `reply` | réponse personnalisée (`{expediteur}` remplacé) ; en direct si les portes globales (mode d'envoi, liste blanche, heures, plafond) le permettent, sinon brouillon à approuver |
| `notify` | notification personnalisée au lieu d'une réponse (`{expediteur}` et `{aperçu}` remplaçables) |
| `save_photo_to` | nom d'un projet open-bot : les photos du fil sont sauvegardées dans `~/workspace/<projet>/fb-guardian/` par l'agent (tâche `route_photo`, réveillée comme le reste de l'IA) |

Exemples :

```sh
# Untel demande d'acheter un produit pour telle plateforme → réponse fixe
ob-plugin-fb-message-guardian-rules --add --sender "Fournisseur X" \
  --contains "acheter,aliexpress,amazon" \
  --reply "Bonjour {expediteur} ! Je note la demande et je te reviens vite."

# Quelqu'un envoie une photo → la photo part dans le projet open-bot « famille »
ob-plugin-fb-message-guardian-rules --add --sender "Coralie" --photo \
  --save-photo famille --notify "Nouvelle photo de {expediteur}"

# Vérifier quelle règle répondrait à un aperçu donné
ob-plugin-fb-message-guardian-rules --test --sender "Fournisseur X" --text "tu peux acheter ça sur aliexpress?"
ob-plugin-fb-message-guardian-rules --list
```

Les règles remplacent la décision par défaut (mots-clés/modèle/IA), mais JAMAIS les portes
dures : heures actives, plafond quotidien, mode d'envoi et liste blanche s'appliquent aussi
à leurs réponses.

---

## 3. Les statuts du scan

| Statut | Signification | Le cycle fait quoi |
|--------|---------------|--------------------|
| `too_soon` | intervalle pas écoulé | rien, termine |
| `ok` | plan dans `new[]` | exécute le plan |
| `not_logged_in` | session Facebook fermée | notifie (1/jour), ne réessaie PAS |
| `needs_user` | écran bloqué / navigation refusée | notifie, rend la main |
| `dom_changed` | Facebook a changé son DOM | diagnostic + rapport (sélecteurs à réparer) |
| `error` | lecture impossible | rapport |

---

## 4. Les réglages (page Plugins → FB Message Guardian)

| Clé | Défaut | Rôle |
|-----|--------|------|
| `reply_mode` | hybride | auto / ia / hybride (§2) |
| `send_mode` | approbation | approbation / direct |
| `whitelist` | (vide) | expéditeurs autorisés en direct (virgules) |
| `trigger_keywords` | devis, prix, renseignement, site web, wordpress | mots-clés déclencheurs |
| `auto_template` | bonjour {expediteur}… | réponse modèle |
| `check_interval_minutes` | 15 | intervalle minimum entre deux scans (10–240) |
| `daily_reply_cap` | 20 | plafond de réponses par jour |
| `active_hours_start` / `_end` | 8 / 21 | fenêtre horaire des envois |
| `notify_untreated` | true | notifier les messages sans règle |
| `notify_logout` | true | notifier les déconnexions (1/jour max) |
| `ai_persona` | (vide) | persona qui rédige (vide = modèle du bureau) |
| `watcher_enabled` | true | veille scriptée toutes les ~3 min (0 token au repos) |
| `screen_session` | (vide) | id d'écran fixe pour la veille (vide = auto-détection) |

---

## 5. Commandes manuelles utiles

```sh
# Scan immédiat + plan (S = id d'écran du fil)
ob-plugin-fb-message-guardian-scan --session S --force

# Lire les derniers messages d'un fil (contexte pour rédiger)
ob-plugin-fb-message-guardian-scan --session S --thread "Nom"

# Voir / approuver l'attente
ob-plugin-fb-message-guardian-queue --list
ob-plugin-fb-message-guardian-queue --approve p-XXXX
ob-plugin-fb-message-guardian-send --session S --pending p-XXXX

# Envoi direct demandé explicitement par toi (contourne mode/liste blanche,
# jamais le plafond ni les heures… sauf force explicite = tu es le patron)
ob-plugin-fb-message-guardian-send --session S --to "Nom" --text "..." --force

# La personnalité des réponses : carnet de règles
ob-plugin-fb-message-guardian-rules --list
ob-plugin-fb-message-guardian-rules --add --sender "Nom" --contains "mot1,mot2" --reply "..."
ob-plugin-fb-message-guardian-rules --test --sender "Nom" --text "aperçu simulé"
ob-plugin-fb-message-guardian-rules --enable|--disable|--remove r-XXXX
```

Tout est aussi documenté dans le skill `fb-message-guardian` (chargé automatiquement quand
tu parles du plugin dans un fil).

---

## 6. Garde-fous absolus

1. Jamais de mot de passe : la reconnexion est humaine.
2. Jamais deux réponses au même message (le registre fait foi).
3. Jamais d'envoi hors des portes, même si l'agent se trompe : le send refuse tout seul.
4. Chaque envoi est loggé dans `state.json` (`sent_log` : date, destinataire, texte).

## 7. Limites connues (v0.2.0)

- Les discussions de groupe et les aperçus e2ee chiffrés (« …chiffrement… ») sont ignorés :
  pas d'aperçu lisible, donc pas de détection d'activité.
- Seules les ~7 premières conversations de la barre latérale sont lues (limite de la
  fenêtre accessible du navigateur). Les demandes de messages (2 non lus à l'installation)
  ne sont pas encore scannées — compteur visible seulement.
- L'envoi dépend des pixels (composer non exposé) : si la mise en page change beaucoup,
  le send peut échouer et le rapporter proprement.
- Un seul cron pilote désormais le plugin : `fb-guardian-watcher` en mode **auto** (le
  script tourne toutes les 3 min et ne réveille l'agent que par une ligne `[cron-agent]`).
  L'ancien cron prompt 15 min (`plugin:fb-message-guardian:scan-inbox`) n'existe plus et
  le manifeste n'en déclare plus (le schéma plugin ne sait pas exprimer le mode auto).
- **Anti re-comptage (v0.5.1)** : un même message ne peut plus revenir en boucle à
  l'agent — le scan dédoublonne sur une clé d'aperçu normalisée (queues « il y a X min »
  retirées), absorbe les variations d'aperçu moins de 10 min après un plan, la file
  refuse un second doublon ouvert pour le même message, et une entrée n'est réveillée
  que 3 fois avant de devenir une notification « file bloquée » (1/jour) au lieu d'un
  nouveau réveil. L'agent vide la file avec `queue --set-text`, `--done`, `--skip`.
- **Ignorés = silence (v0.5.2)** : un message hors règles, doublon ou déjà couvert est
  retiré avec `queue --done` et n'apparaît nulle part — pas de raison rédigée, pas de
  mention dans la conclusion de l'agent. La conclusion ne liste que les actions réelles ;
  s'il n'y en a aucune, la réponse entière de l'agent est « rien ».
- La veille tourne via un cron `ob-cron` du bureau (`fb-guardian-watcher`) : elle survit
  aux mises à jour du plugin mais doit être recréée à la main après un reset du desktop :

```sh
ob-cron add --name fb-guardian-watcher \
  --script "ob-plugin-fb-message-guardian-watch" --run auto --every 180 \
  --message "Tu es le cycle IA du gardien Facebook (plugin fb-message-guardian), réveillé par le watcher scripté SEULEMENT s'il y a du travail : réponses IA en attente ou scan dom_changed. La ligne [cron-agent] donne la session d'écran <SESSION> et l'état. Passe unique, conclus en 2-3 lignes maximum : 1) ob-plugin-fb-message-guardian-queue --list ; 2) pour chaque entrée status=await_ai : contexte du fil via ob-plugin-fb-message-guardian-scan --session <SESSION> --thread \"<sender>\", rédige une réponse courte et professionnelle en français ; 3) si plan=send:ai → ob-plugin-fb-message-guardian-send --session <SESSION> --pending <id> --force ; sinon (plan=draft) → écris le texte rédigé dans l'entrée (~/.open-bot/fb-message-guardian/state.json, status=await_approval) et notifie Justin : brouillon <id> pour <sender> — « <réponse> » (il approuve par « envoie <id> »). dom_changed → diagnostique une fois avec ob-page --session <SESSION> read, répare parse_rows/parse_row du scan si possible, sinon rapporte. Garde-fous : jamais de mot de passe, jamais deux réponses au même message, jamais d'envoi hors des portes du plugin, pas de boucle, une seule passe."
```
