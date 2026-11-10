#!/usr/bin/env bash
# Vérifie le matériel du robot depuis le PC (détails : robot/check.py). Usage : ./check.sh [délai_s]
# Code de sortie 0 si tout va bien, 1 si un élément est KO, 255 si le robot est injoignable.
cd "$(dirname "$0")"
# Le script part par l'entrée standard : rien à copier sur le Jetson au préalable.
# FastDDS du robot journalise des Mo sur la sortie standard, en lignes horodatées : on les retire.
ssh -o ConnectTimeout=8 -J agi@192.168.88.88 agi@10.0.1.41 \
  "source /opt/ros/humble/setup.bash && source ~/hackathon/aimdk/install/setup.bash && python3 - $*" \
  < robot/check.py 2>&1 | grep --line-buffered -v -E '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9:.]+ \['
code=${PIPESTATUS[0]}
[ "$code" = 255 ] && echo "KO  robot injoignable : le PC est-il sur le hotspot du robot ?"
exit "$code"
