# Notes pour les agents

Interface web opérateur pour le robot humanoïde AgiBot X2 Ultra (hackathon) : flux caméras, appel audio/vidéo d'alerte, pilotage. Remplace Foxglove, qu'on ne doit plus utiliser.

## Robot

- 3 cartes reliées en ROS 2 Humble : **SoC0** (RK3588, moteurs + hotspot wifi, `192.168.88.88`), **SoC1** (Jetson Orin NX, caméras/GPU, `10.0.1.41`, **on travaille ici**), **SoC2** (RK3588S, audio/voix, `10.0.1.42`).
- Le PC rejoint le hotspot du robot (identifiants : demander à l'équipe, jamais dans le dépôt). Il joint directement `10.0.1.41`.
- SSH : `ssh -J agi@192.168.88.88 agi@10.0.1.41` (clé déjà installée sur le PC de Simon).
- Environnement ROS sur le Jetson : `source /opt/ros/humble/setup.bash && source ~/hackathon/aimdk/install/setup.bash`.
- SDK AimDK : doc https://x2-aimdk.agibot.com, exemples dans `~/hackathon/aimdk/src/py_examples/py_examples/`.
- Tailscale tourne sur le Jetson, sur le compte d'un tiers : n'exposer aucun service sur cette interface.

## Règles

- Sur le Jetson, n'écrire que dans `~/hackathon/webui` (relais) et `~/hackathon/epi_detection` (détection). Le reste du home appartient à d'autres développeurs, une autre session agent peut travailler en parallèle.
- Ne pas modifier ni relancer les services AgiBot (utilisateur `run` : caméras, orbbec, MC…).
- Ne jamais faire bouger le robot pendant un test (`/drive`, `/aima/mc/locomotion/velocity`) ni changer son mode, sans accord explicite et sans quelqu'un à côté.
- Commits au nom de Simon, sans co-auteur IA.

## Codebase

```
web/          pages servies par le PC (HTML + JS vanilla, aucune build)
  index.html    Admin : menu déroulant des flux vidéo
  alerte.html   Alerte : caméra frontale, audio bidirectionnel, pilotage flèches / manette PS5
  common.js     adresse du relais (?robot=hôte:port pour changer), lecture d'un flux vidéo
serve.mjs     serveur statique Node sur http://localhost:8000 (localhost requis pour micro et manette)
run.sh        copie robot/ vers ~/hackathon/webui, relance le relais, démarre la détection si absente, puis lance serve.mjs
robot/        relais Python sur le Jetson (aiohttp + rclpy, Python système, sans dépendance à installer)
  relay.py      écoute 10.0.1.41:8081
  start.sh      (re)lance le relais en arrière-plan, journal dans relay.log
detection/    copie de ~/hackathon/epi_detection (YOLO EPI, uv) ; best.pt non versionné
```

API du relais :

| Route | Rôle |
|---|---|
| `GET /topics` | topics image ayant un émetteur (JSON) |
| `WS /video?topic=` | JPEG ≤ 960 px à 10 i/s ; texte `{"error"}` si problème ; profondeur 16UC1 colorisée |
| `WS /audio` | binaire = PCM S16LE 16 kHz mono (micro robot → PC, PC → haut-parleur robot) ; texte `{"talk": bool}` pour le focus audio |
| `WS /drive` | le client envoie `{vx, vy, wz}` à 20 Hz ; le relais publie à 50 Hz, vitesse nulle après 0,3 s sans commande, un seul pilote à la fois |

Détection : `detect.py` lit la caméra frontale et publie `/hackathon/epi/detections/compressed`. Sans spectateur du flux, elle reste abonnée à la caméra mais ne calcule rien (GPU au repos) : la laisser tourner, car SoC2 met jusqu'à ~2 min à servir la caméra frontale à un nouvel abonné. Rien ne la lance au démarrage du robot : `run.sh` la démarre si besoin. Lancement manuel sur le Jetson : `cd ~/hackathon/epi_detection && setsid nohup ./detect.sh > detect.log 2>&1 < /dev/null &`.

## Pièges connus

- Un seul `detect.py` à la fois : deux instances ont surchargé le Jetson, et les caméras arrière/stéréo ont planté (`STATUS_TIMEOUT` dans `/agibot/data/log/log_*/hal_camera/hal_camera.log`). Seul un redémarrage du robot les rétablit.
- Le pilote orbbec (rgbd, `/camera/*`) s'arrête parfois seul : `/camera/*` est masqué, utiliser `/aima/hal/sensor/rgbd_head_front/*`.
- `MultiThreadedExecutor` perd l'audio : le relais garde un exécuteur mono-thread avec des callbacks légers, les calculs se font dans asyncio.
- Micro interne : 6 canaux (0-3 micros, 4-5 référence haut-parleur), très faible (~-43 dBFS), amplifié côté navigateur. L'audio est à l'alternat : le son du robot est coupé quand l'opérateur parle.
- MC : vitesse 0 ou |v| ∈ [0,2 ; 1] m/s et |ω| ∈ [0,1 ; 1] rad/s, bridée à 0,5 m/s et 1 rad/s. La politique de marche multiplie ω par 1,5, plafonne le recul à -0,5 m/s et piétine aux faibles consignes : flèches à 1 rad/s et -0,5 m/s comme la télécommande officielle. Le robot ne marche qu'en mode `LOCOMOTION_DEFAULT`.
- Lancement en arrière-plan par SSH : rediriger `< /dev/null`, sinon la session SSH reste bloquée.
- Images brutes `rgb_image` jusqu'à 15 Mo : toujours passer par `/compressed`.
