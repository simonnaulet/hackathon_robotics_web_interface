#!/bin/bash
# (Re)lance le relais en arrière-plan sur le Jetson. Journal : relay.log
cd "$(dirname "$0")"
# après un reboot l'ancien PID peut être un autre processus : ne tuer que si c'est bien le relais
pid=$(cat relay.pid 2>/dev/null)
[ -n "$pid" ] && grep -qs relay.py "/proc/$pid/cmdline" && kill "$pid" && sleep 1
source /opt/ros/humble/setup.bash
source ~/hackathon/aimdk/install/setup.bash
setsid nohup python3 relay.py > relay.log 2>&1 < /dev/null &
echo $! > relay.pid
