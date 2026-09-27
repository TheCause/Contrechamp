# Lot 1 — Des contrôles qui mordent

*Fork `TheCause/OpenMontage`, 27 sept 2026. Source : banc d'essai de 3 rendus réels (explainer
vertical FR, épisode muet à carton, animatique), 0 €. 13 faux verts relevés, tous dans la revue
finale de `video_compose` (`_run_final_review`) ou dans les statuts d'outils.*

## Le problème, en une phrase

La revue finale **affirme** des choses qu'elle ne **mesure** pas : `unreadable_text`,
`broken_overlays`, `missing_assets` valent `False` par défaut et ne sont jamais calculés ;
`music_present` vaut `True` dès que le volume moyen dépasse −50 dB ; un défaut hors d'une liste
de six mots-clés laisse le statut à `pass`. Deux rendus différents — l'un cassé, l'autre bon —
ont reçu un `visual_spotcheck` **identique**.

## Principe

> Un contrôle qui n'a pas tourné le **dit**. Un contrôle qui a tourné montre **ce qu'il a lu**.
> `pass` n'existe que si tous les contrôles attendus ont tourné et qu'aucun défaut n'est ouvert.

## Ce qui change

### 1. Trois états au lieu de deux

Chaque constat visuel/audio devient `true | false | null`. `null` = **non vérifié**, avec la
raison dans `not_checked: {champ: raison}`. Plus aucune valeur par défaut rassurante.

### 2. Échantillonnage par scène, pas par pourcentage

Les 4 images à 10/35/65/90 % tombent au hasard des scènes (1 seule dans le carton du banc B).
Nouveau : **une image au milieu de chaque segment** de `edit_decisions.cuts` (plafond 24),
plus les 4 points historiques si `cuts` est absent.

### 3. Texte à l'image : lu, pas supposé (OCR local, tesseract)

- Si `edit_decisions` déclare du texte attendu (surimpressions, cartons, sous-titres) : OCR de
  l'image du segment concerné, dans la langue du projet.
- `unreadable_text = true` si le texte attendu n'est pas retrouvé (similarité de chaîne < seuil,
  normalisation espaces/casse **désactivable** pour le texte sacré — lot 3).
- **Mots collés** (défaut `CaptionOverlay` du banc A) : un mot lu de plus de 25 lettres, ou
  l'absence d'espaces là où le texte attendu en a, est un défaut.
- Le texte **lu** est écrit dans le rapport (`ocr_readings`), pour qu'un humain le compare.
- Tesseract ou la langue absents → `null` + raison. Jamais `false`.

### 4. Coutures et boucle

- Sur les coupes déclarées **continues** (même source de part et d'autre : image figée, maintien,
  `continuity: true` sur la coupe) : SSIM et luminance moyenne (filtres ffmpeg `ssim` et
  `signalstats`, aucune dépendance Python nouvelle) sur les images de part et d'autre.
  Défaut si SSIM < 0,90 ou saut de luminance > 4 points (banc B, tour 1 : 8,9 points ; tour 2 :
  mauvaise image).
- Si `edit_decisions.metadata.loop` est vrai : même mesure entre la dernière et la première image.
- Les coupes franches entre plans différents ne sont **pas** mesurées (un saut y est normal).

### 5. Musique : présence mesurée

`music_present` = de l'énergie audio **entre** les phrases de narration. Mesure : `silencedetect`
(−50 dB, 0,3 s) ; si les silences numériques couvrent les intervalles hors voix → `false`.
Sans narration connue → mesure sur toute la piste. Si le plan de musique disait « pas de musique »,
l'absence n'est pas un défaut.

### 6. Statut honnête

- `pass` : tous les contrôles attendus ont tourné, aucun défaut.
- `revise` : au moins un défaut ouvert, **ou** un contrôle attendu à `null`.
- `fail` : conteneur invalide (inchangé).
- La liste de mots-clés « critiques » disparaît : **tout défaut ouvert empêche `pass`**.
- Le schéma `final_review` suit : champs `boolean | null`, `not_checked`, `ocr_readings`,
  `seams`, `frames_sampled` minimum 1.

### 7. Statuts d'outils qui mentent (préflight)

- `piper_tts` : `AVAILABLE` seulement si le binaire **et** au moins un modèle de voix sont
  trouvés (banc A : « Unable to find voice »). Binaire cherché aussi dans le `bin/` de
  l'environnement Python courant (banc : vu indisponible hors venv activé).
- `pixabay_music` : pas `AVAILABLE` « sans configuration » sans preuve ; `DEGRADED` avec la raison
  (banc A : HTTP 403).

## Preuve exigée (règle du contrôle)

Chaque contrôle est livré avec **le test qui le fait échouer** sur un défaut réel, et un test
qui le laisse **silencieux** sur le cas sain :

| Contrôle | Doit échouer sur | Doit se taire sur |
|---|---|---|
| OCR texte | sous-titre aux mots collés (fixture ffmpeg `drawtext`) | même texte bien espacé |
| OCR texte | carton au texte altéré d'un mot | carton exact |
| Couture | image figée assombrie de 9 points | image figée exacte |
| Couture | image figée d'un autre plan | même plan |
| Boucle | dernière image ≠ première | boucle propre |
| Musique | narration seule, silences à −91 dB | narration + nappe à −30 dB |
| Statut | un défaut non « critique » | rendu sans défaut |
| `null` | tesseract absent (simulé) → `null` + raison, statut `revise` | — |

Fixtures **synthétiques** (ffmpeg `lavfi`, `drawtext`) : aucun contenu du banc n'entre dans le
dépôt public. Contrôle réel à part, **hors dépôt**, sur le M4 : les rendus cassés et bons du banc
doivent recevoir des verdicts **différents**.

## Hors lot 1

Correction de `CaptionOverlay`, sous-titres alignés sur le script (lot 2) ; profils muet / série /
texte sacré (lot 3) ; `approval_policy` et fichier de montage unique (lot 4).
