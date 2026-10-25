# AGOA DATA
Application Windows de la gamme AGOA : **volumétrie d'un bâtiment à partir du nuage de points LiDAR HD de l'IGN**.
Saisir une adresse (Paris par défaut) : l'application trouve la parcelle, les bâtiments et les voies, télécharge le morceau utile
du nuage de points, calcule la volumétrie et trace les coupes.

## Installer
Via le **launcher AGOA** (le programme apparaît dans la liste une fois ajouté à `catalog.json`, voir plus bas),
ou en téléchargeant `AGOA-DATA-Setup-x.y.z.exe` dans la [dernière Release](https://github.com/agence-rt/data/releases/latest).
L'application s'installe dans `C:\AGOA\data`.

## Ce que fait l'application
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
Rangés dans `<dossier choisi>\<NOM>` (nom proposé selon la convention de l'agence : `CHAMPIONNET198`, modifiable) :

| Fichier | Contenu |
|---|---|
| `synthese.txt` | chiffres clés, à coller dans un rapport |
| `resultats.json` | tous les résultats (parcelle, bâtiments, coupes) pour d'autres programmes AGOA |
| `nuage_decoupe.laz` | nuage de points autour du bâtiment (40 m), Lambert 93 / IGN69, classification IGN conservée : s'ouvre dans CloudCompare |
| `modele_toiture.asc` (+ `.prj`) | modèle numérique de toiture 0,5 m (altitudes NGF) : s'ouvre dans QGIS |
| `coupe_N_perp_<voie>.png`, `coupe_N_para_<voie>.png` | coupes |
| `plan_coupes.png` | plan de situation des coupes |
| `plan_cadastral_<IDU>.pdf` | **plan cadastral** A4 vectoriel à l'échelle normalisée (1/200 à 1/5000), parcelle en rouge, numéros, bâtiments, noms de voies, échelle graphique, nord. Dessiné d'après les données ouvertes IGN : ce n'est pas l'extrait officiel DGFiP (à demander sur cadastre.gouv.fr si une valeur juridique est requise) |

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
