#!/bin/bash
# (Re)lance le relais en arrière-plan sur le Jetson. Journal : relay.log
cd "$(dirname "$0")"
[ -f relay.pid ] && kill "$(cat relay.pid)" 2>/dev/null && sleep 1
source /opt/ros/humble/setup.bash
source ~/hackathon/aimdk/install/setup.bash
setsid nohup python3 relay.py > relay.log 2>&1 < /dev/null &
echo $! > relay.pid
