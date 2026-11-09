#!/usr/bin/env bash
# Envoie le relais sur le Jetson, le relance, puis sert l'interface sur http://localhost:8000
set -e
cd "$(dirname "$0")"
rsync -az -e 'ssh -J agi@192.168.88.88' robot/ agi@10.0.1.41:hackathon/webui/
ssh -J agi@192.168.88.88 agi@10.0.1.41 'bash ~/hackathon/webui/start.sh'
node serve.mjs
