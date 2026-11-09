#!/bin/bash
# Lance la détection EPI. Options : --model best.pt --conf 0.4 --width 960
# En arrière-plan : setsid nohup ./detect.sh > detect.log 2>&1 < /dev/null &
# Arrêt : pkill -f "[d]etect.py"
cd "$(dirname "$0")"
source /opt/ros/humble/setup.bash
# Tout reste dans ~/hackathon : cache uv, réglages Ultralytics, et Python système (requis par rclpy).
export UV_CACHE_DIR="$HOME/hackathon/.uv-cache" YOLO_CONFIG_DIR="$PWD/.ultralytics" UV_PYTHON_DOWNLOADS=never
# libcupti et libcudss embarquées dans le venv (absentes du Jetson).
NV="$PWD/.venv/lib/python3.10/site-packages/nvidia"
export LD_LIBRARY_PATH="$NV/cuda_cupti/lib:$NV/cu12/lib:$LD_LIBRARY_PATH"
exec ~/hackathon/bin/uv run --python /usr/bin/python3.10 python detect.py "$@"
