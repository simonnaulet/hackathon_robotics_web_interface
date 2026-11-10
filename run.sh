#!/usr/bin/env bash
# Envoie le relais sur le Jetson, le relance, démarre la détection si besoin, puis sert l'interface sur http://localhost:8000
# La détection reste lancée : SoC2 met ~2 min à servir la caméra frontale à un nouvel abonné. Sans spectateur, elle ne calcule rien.
set -e
cd "$(dirname "$0")"
JETSON=(ssh -J agi@192.168.88.88 agi@10.0.1.41)
rsync -az -e 'ssh -J agi@192.168.88.88' robot/ agi@10.0.1.41:hackathon/webui/
"${JETSON[@]}" 'bash ~/hackathon/webui/start.sh'
# Un seul detect.py à la fois (deux ont fait planter les caméras) ; "[d]" évite que pgrep se trouve lui-même.
# Le if, et non ||, garde le & sur la seule commande redirigée : sinon la session SSH reste ouverte.
"${JETSON[@]}" 'cd ~/hackathon/epi_detection; if ! pgrep -f "[d]etect.py" > /dev/null; then setsid nohup ./detect.sh > detect.log 2>&1 < /dev/null & fi'
node serve.mjs
