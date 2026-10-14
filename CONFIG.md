# CONFIG — fb-message-guardian : guide de configuration

Guide pour l'agent qui installe le plugin. Tout se règle sur l'onglet **Plugins →
FB Message Guardian → Settings**, dans la langue de l'utilisateur qui installe.

## Ce dont le plugin a besoin

- **Canal DOM** (défaut) : rien à configurer — il lit Messenger personnel via le
  navigateur du bureau. L'utilisateur doit juste rester connecté à messenger.com.
- **Canal API officielle** (optionnel, v0.2.0) : messages de la **Page Facebook**.
  Nécessite `fb_page_id` + `fb_page_token`, et `source` passé à `api` ou `dom+api`.
  L'API officielle Meta ne couvre PAS les Messenger personnels — c'est normal.

## Réglages (settings)

| Clé | Quoi demander à l'utilisateur |
|-----|-------------------------------|
| `source` | « Surveiller tes MP perso (dom), les messages de ta Page (api), ou les deux (dom+api) ? » |
| `fb_page_id` | L'id numérique de la Page (ex. 964452330084980). Visible dans la Page → À propos, ou via l'API Graph Explorer. |
| `fb_page_token` | Token d'accès Page — voir la marche à suivre ci-dessous. Secret : ne jamais le demander en clair dans un fil, pointer l'utilisateur vers le champ masqué de la page Plugins. |
| `api_graph_version` | Laisser le défaut (v21.0) sauf dépréciation Meta. |

Les autres réglages (heures, plafond, mots-clés, liste blanche, mode d'envoi) ont des
défauts sains : n'y toucher que sur demande.

## Obtenir le token de Page (fb_page_token)

1. L'utilisateur va sur **developers.facebook.com** → My Apps → **Create App** (type
   Business) — ou réutilise une app existante.
2. Dans l'app : **Add Product → Messenger**.
3. **Messenger → Access Tokens / Generate Token** : choisir la Page, copier le token
   (celui-ci est permanent pour une Page liée à l'app ; sinon générer un token utilisateur
   dans Graph API Explorer puis l'étendre, ou passer par un System User Meta Business).
4. Vérifier que l'app a les permissions **`pages_messaging`**, **`pages_read_engagement`**
   (et `pages_show_list` pour le diagnostic) : App → App Review → Permissions and Features.
5. **Important (mode dev)** : tant que l'app n'a pas passé l'App Review de
   `pages_messaging`, l'API ne délivre les réponses qu'aux personnes ayant un rôle sur
   l'app ou la Page (admin, développeur, testeur). Pour répondre à de vrais clients,
   l'app doit passer l'App Review — l'utilisateur la fait lui-même, ça ne se scripte pas.
6. Remplir le secret : page Plugins → FB Message Guardian → Settings → champ masqué
   `fb_page_token` (ou `ob-plugin vault fb-message-guardian fb_page_token=<valeur>`,
   en écrivant-only : la valeur n'est jamais relue par l'agent).

## Ordre ask → fill → verify

1. Demander le périmètre (`source`) puis, si api : `fb_page_id`, puis le token (champ masqué).
2. Remplir les deux réglages.
3. Vérifier :

```sh
ob-plugin-fb-message-guardian-api-scan --force
```

- `{"status":"ok","source":"api", ...}` → configuré, la passe lit les conversations de la Page.
- `{"status":"unconfigured"}` → il manque `fb_page_id` ou `fb_page_token`.
- `{"status":"api_error","detail":"190 ..."}` → token expiré/invalide : refaire l'étape token.
- `{"status":"api_error","detail":"(10) ..."}` → permissions manquantes sur l'app
  (`pages_messaging` / `pages_read_engagement`).
- La veille (`watch`) suit la même source automatiquement : `source=api` fonctionne
  navigateur fermé, `source=dom+api` surveille les deux boîtes.

## Preuve que ça marche

Envoyer un message test à la Page depuis un autre compte, puis relancer l'api-scan :
le message apparaît dans `new[]` avec `channel:"api"`, et la file le traite comme tout
message (règles, brouillon, « envoie p-XXXX »).
