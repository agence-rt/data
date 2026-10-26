# AGOA DATA
Application Windows de la gamme AGOA : **volumétrie d'un bâtiment à partir du nuage de points LiDAR HD de l'IGN**.
Saisir une adresse (Paris par défaut) : l'application trouve la parcelle, les bâtiments et les voies, télécharge le morceau utile
du nuage de points, calcule la volumétrie et trace les coupes.

## Installer
Via le **launcher AGOA** (le programme apparaît dans la liste une fois ajouté à `catalog.json`, voir plus bas),
ou en téléchargeant `AGOA-DATA-Setup-x.y.z.exe` dans la [dernière Release](https://github.com/agence-rt/data/releases/latest).
L'application s'installe dans `C:\AGOA\data`.

## Quatre fonctions, quatre boutons
Saisir une adresse (suggestions pendant la frappe), choisir le dossier **ENREGISTRER SOUS**, puis :

1. **CADASTRES (PDF A3)** — trois plans cadastraux A3 vectoriels (1/500, 1/1000, 1/2000), voir *Livrables*.
2. **VOLUMÉTRIE (nuage de points)** — volume du bâtiment, modèle de toiture, coupes, nuage découpé (détail ci-dessous).
3. **PLANS DWG (depuis PDF)** — fond de plan DWG à partir de plans PDF que vous possédez :
   - choisir un ou plusieurs PDF (plans vectoriels ou **scannés**) ; l'échelle est lue dans le PDF (« 1/50 », « Échelle 1:100 »), sinon elle est demandée
     (toujours pour un scan) ;
   - le logiciel sépare les murs (traits épais et poché), les traits fins et, pour un PDF vectoriel, les textes et valeurs de cotes ; pour un
     **scan** (300 dpi), les traits sont réduits à leur axe (squelette) : murs = polylignes avec leur épaisseur réelle, traits fins (lignes de cote…),
     et calque `COTES` pour les petits tracés (tirets, flèches, chiffres écrits à la main) ; cadre de la feuille et taches de scan sont écartés ;
   - le plan est **recalé sur le cadastre** : rotation, translation et, si l'écart reste raisonnable (±15 %) et le recouvrement bon, correction des
     dimensions, par comparaison de l'emprise des murs avec le bâtiment BD TOPO / la parcelle ; un aperçu `apercu_recalage_*.png` permet de contrôler ;
   - livrable : `fond_de_plan_<NOM>.dxf` (calques `CADASTRE_*` et `<PLAN>_MURS`, `_TRAITS`, `_TEXTES_COTES`, `_TEXTES`, mètres, Lambert 93) puis
     `.dwg` si l'**ODA File Converter** (gratuit, opendesign.com) est installé sur le poste ; sinon le DXF s'ouvre dans AutoCAD / BricsCAD /
     etc. et s'enregistre en DWG en un clic. Le rapport de recalage est écrit dans `recalage_<NOM>.json`.
   - limites : un scan est vectorisé par traitement d'image (traits fins parfois fragmentés, textes et cotes restent des tracés) ; si le plan ne couvre
     qu'une partie du bâtiment, le recalage est signalé « peu fiable » et il faut vérifier l'aperçu ; plusieurs plans d'un même immeuble sont superposés
     sur le même emplacement, chacun dans ses calques.

4. **FAÇADES SUR RUE** — dessin de chaque façade donnant sur une voie, dans `<NOM>\facade` : `facade_<n>_<VOIE>.png` (enveloppe),
   `facades.dxf` (calques `F<n>_<VOIE>_SOL / FACADE / TOITURE / NIVEAUX / BAIES / BALCONS / COTES / TEXTES`, mètres, altitudes NGF).
   - *Niveau 1* : longueur (BD TOPO / cadastre), sol, égout, silhouette du toit (LiDAR HD), niveaux (BD TOPO ou 2,90 m estimés).
   - *Niveau 2 — forme générale* (avant tout détail). Le logiciel demande le **nombre de niveaux au-dessus du RDC visibles depuis la rue**
     (les attiques en retrait n'en font pas partie) : la hauteur sol → égout est répartie entre un **RDC** (hauteur lue sur la ligne de
     bandeau des photos, sinon estimée) et des niveaux courants égaux, planchers recalés sur les lignes horizontales des photos.
     Photos **Panoramax** (base prioritaire, 360° et plates) projetées géométriquement sur le plan de la façade (position, azimut, tangage),
     fusionnées en une bande à l'échelle sur toute la longueur ; vos photos (champ **PHOTOS DE FAÇADE**, ex. dossier façade du Dropbox)
     sont redressées puis recalées sur cette bande. Sur la bande : **rythme des baies** (axes verticaux, largeurs régularisées),
     baies par case axe × niveau, balcons continus, et **RDC analysé à part** (porches, portes, vitrines). Les tailles de baies
     précises viendront dans un second temps. Aperçu de validation `facade_<n>_<VOIE>_ouvertures.png`, bande `…_elevation.jpg`,
     calques DXF `BAIES / BALCONS / RDC / NIVEAUX`.
   - *Façade sur rue, méthode « une vue »* (v0.9+) : une vue 360° Panoramax centrée est redressée par rotation ; haut du RDC (corniche),
     **travées numérotées**, **linteaux** comme repère régulier des niveaux (plancher = dessus de dalle au-dessus du linteau du niveau inférieur),
     sommet de façade lu sur le nuage : **terrasse à l'alignement** (dessus de dalle = surface LiDAR − 0,30 m) et **attiques en retrait**.
     Livrables dans `assemblage_<n>` : `3_travees_numerotees.jpg`, `4_forme_generale.png`, `forme_generale.dxf/json`, `diagnostic.json` ;
     `coupes_simplifiees.dxf` (contour simplifié des coupes) dans le dossier du mode.
   - **Limites** : la détection des ouvertures est automatique mais approximative (trame de baies, balcons continus) — l'aperçu sert à la
     valider ; sans image Panoramax exploitable, seul le niveau 1 est produit (vos photos seules n'ont ni position ni échelle) ; les
     véhicules, arbres et masques à l'avant-plan ne sont pas retirés ; la hauteur de prise de vue est supposée à 2,3 m.

## Volumétrie : ce que fait l'application
1. **Adresse** → point (API de géocodage de la Géoplateforme, base adresse nationale). La recherche est **interactive** : des suggestions
   (Paris d'abord) s'affichent pendant la frappe ; ↑ ↓ + Entrée ou clic pour choisir, Échap pour fermer.
2. **Parcelle** cadastrale (Parcellaire Express) puis **bâtiments** de la BD TOPO situés sur la parcelle.
3. **Voies** nommées (BD TOPO) à moins de 25 m du bâtiment principal.
4. **Dalles LiDAR HD** (nuage classifié, format COPC) couvrant l'emprise : seuls les morceaux utiles sont lus par requêtes HTTP partielles
   (≈ 15 Mo au lieu de ≈ 100 Mo par dalle ; le serveur de l'IGN limite à 1 requête par seconde, d'où 30 s à 1 min pour un premier calcul).
5. **Volumétrie** : altitude du sol (médiane des points « sol » autour du bâtiment), modèle de toiture au pas de 0,5 m
   (points classés « bâtiment »), volume sol → toit, hauteurs moyenne / max, comparaison avec l'estimation BD TOPO (emprise × hauteur).
6. **Coupes** dans le nuage (tranche de 1,5 m) :
   - **une coupe perpendiculaire à chaque voie** qui borde le bâtiment (rue, villa, passage…), au droit de la façade qui lui fait face ;
   - une coupe parallèle à la voie la plus proche ;
   - lecture automatique de l'**égout** (versant côté voie prolongé jusqu'au nu de façade), du **faîtage** (point haut robuste aux souches
     et antennes) et de la **pente** indicative du versant.

## Livrables
Rangés dans `<ENREGISTRER SOUS>\<NOM>\<fonction>` (nom automatique selon la convention de l'agence : `CHAMPIONNET198` ; sous-dossiers `cadastre`, `volumetrie`, `plan_dwg`) :

| Fichier | Contenu |
|---|---|
| `synthese.txt` | chiffres clés, à coller dans un rapport |
| `resultats.json` | tous les résultats (parcelle, bâtiments, coupes) pour d'autres programmes AGOA |
| `nuage_decoupe.dxf` | même nuage en DXF (points 3D, calques par classe : `NUAGE_SOL`, `NUAGE_BATI`, `NUAGE_VEGETATION`…, + `EMPRISE_BATIMENT` et `COUPES_TRACES`), Lambert 93 / NGF ; au-delà de 600 000 points, sol et végétation sont éclaircis (le bâti est conservé) |
| `coupes.dxf` | coupes en élévation côte à côte (x = distance le long de la coupe, y = altitude NGF) : points de la tranche, profil du toit, sol, nus de façade, égout et faîtage avec valeurs, calques par coupe |
| `nuage_decoupe.laz` | nuage de points autour du bâtiment (40 m), Lambert 93 / IGN69, classification IGN conservée : s'ouvre dans CloudCompare |
| `modele_toiture.asc` (+ `.prj`) | modèle numérique de toiture 0,5 m (altitudes NGF) : s'ouvre dans QGIS |
| `coupe_N_perp_<voie>.png`, `coupe_N_para_<voie>.png` | coupes |
| `plan_coupes.png` | plan de situation des coupes |
| `plan_cadastral_<IDU>_1-500.pdf`, `_1-1000.pdf`, `_1-2000.pdf` | **plans cadastraux** A3 paysage, centrés sur la parcelle, **PDF vectoriels** sans mise en page (lignes de parcelles, bâtiments, axes de voies, numéros et noms en vrais textes) à récupérer dans un logiciel de dessin ; 1 mm sur la feuille = échelle/1000 m. Données IGN ouvertes, pas l'extrait officiel DGFiP (cadastre.gouv.fr) |

## Limites
- Ordre de grandeur (≈ 10 points/m²), pas un relevé de géomètre : lucarnes, souches et cours étroites sont mal restituées.
- Le volume va du sol au toit : combles et niveau bas inclus, **sous-sols exclus**.
- Le sol est une altitude unique (médiane). En terrain en pente, les hauteurs varient de ± 0,5 m selon la façade ; les coupes donnent la hauteur au pied de façade.
- Si le toit n'est pas classé « bâtiment » dans le nuage, le programme le dit au lieu de donner un volume faux.
- Seuls les bâtiments BD TOPO couvrant ≥ 25 % de leur emprise sur la parcelle sont traités ; les coupes concernent le bâtiment principal.

## Ligne de commande
```
py -m agoa_data "198 rue Championnet, Paris" "C:\Resultats"
```

## Développement
- Python 3.10+, interface tkinter ; calcul : numpy, shapely, pyproj, laspy (+ lazrs), matplotlib.
- `py -m unittest discover -s tests` : tests du cœur (nuage synthétique, serveur HTTP local) et de la fenêtre (ignorés sans écran).
- `build.bat` : compile `AGOA DATA.exe` en local. `python tools/make_icon.py DA` régénère `icon.ico` (carré rouge agence, code en gras).
- Un seul serveur est interrogé : `data.geopf.fr` (Géoplateforme IGN). Il doit être autorisé si le poste filtre le réseau.

## Publier une version
Augmenter `APP_VERSION` dans `agoa_data/__init__.py` puis pousser sur `main` : GitHub Actions lance les tests, compile le programme,
fabrique l'installateur (`installer.nsi`) et crée la Release (étiquette automatique).

## Ajouter AGOA DATA au launcher
Dans `catalog.json` du dépôt [agence-rt/agoa](https://github.com/agence-rt/agoa), ajouter à la liste `apps` (aucune recompilation du launcher) :

```json
{
  "id": "data",
  "code": "DA",
  "nom": "AGOA DATA",
  "description": "Volumétrie d'un bâtiment par nuage de points LiDAR HD : parcelle, coupes perpendiculaires aux voies, égout et faîtage",
  "repo": "agence-rt/data",
  "asset": "AGOA-DATA-Setup-*.exe",
  "exe": "AGOA DATA.exe"
}
```

## Sources et licence des données
Géoplateforme IGN : géocodage, Parcellaire Express, BD TOPO, LiDAR HD — Licence Ouverte 2.0.
