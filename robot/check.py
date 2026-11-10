#!/usr/bin/env python3
"""Vérifie le matériel et nos services avant de travailler : caméras, micro, relais, détection YOLO.

Sur le Jetson : python3 check.py [délai_s] (environnement ROS chargé). Depuis le PC : ./check.sh.
Lecture seule : ne change rien au robot. Code de sortie 0 si tout va bien, 1 sinon.
"""
import glob
import math
import os
import sys
import time
import urllib.request

import numpy as np
import rclpy
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CompressedImage, Image
from aimdk_msgs.msg import AudioCapture

WAIT = float(sys.argv[1]) if len(sys.argv) > 1 else 15  # s ; SoC2 peut mettre ~2 min à servir la frontale
CAM = '/aima/hal/sensor/'
CAMERAS = {  # topic -> (nom, type)
    CAM + 'rgb_head_front_center/rgb_image/compressed': ('caméra frontale', CompressedImage),
    CAM + 'rgb_head_rear/rgb_image/compressed': ('caméra arrière', CompressedImage),
    CAM + 'stereo_head_front_left/rgb_image/compressed': ('stéréo gauche', CompressedImage),
    CAM + 'stereo_head_front_right/rgb_image/compressed': ('stéréo droite', CompressedImage),
    CAM + 'rgbd_head_front/rgb_image/compressed': ('RGB-D couleur', CompressedImage),
    CAM + 'rgbd_head_front/depth_image': ('RGB-D profondeur', Image),
}
YOLO_TOPIC = '/hackathon/epi/detections/compressed'  # detect.py ne calcule que s'il a un abonné : on en devient un
MIC_TOPIC = '/aima/hal/audio/capture'
SILENT = -80  # dBFS ; shortcut: seuil deviné (interne ~-43, DJI allumé ~-50), mesurer le DJI débranché pour l'affiner
RELAY = 'http://10.0.1.41:8081/topics'
DETECT_DIR = os.path.expanduser('~/hackathon/epi_detection')
STARTUP = 120  # s : détection plus jeune que ça = encore en démarrage, pas une panne


def mic_verdict(channels, mic_channels, db):
    """(ok, texte) pour le micro à partir du nombre de canaux et du niveau RMS des voies micro."""
    source = {6: 'interne', 2: 'externe (jack, DJI)'}.get(channels, f'{channels} canaux, source inconnue')
    if db < SILENT:
        hint = ' : DJI débranché ou éteint ?' if mic_channels == 1 else ''
        return False, f'micro {source} muet ({db:.0f} dBFS){hint}'
    return True, f'micro {source}, {db:.0f} dBFS'


assert mic_verdict(6, 4, -43) == (True, 'micro interne, -43 dBFS')
assert not mic_verdict(2, 1, -95)[0] and 'DJI' in mic_verdict(2, 1, -95)[1]


def detect_pids():
    """PID des processus « python detect.py » (le lanceur uv en est un parent, il ne compte pas)."""
    pids = []
    for path in glob.glob('/proc/[0-9]*/cmdline'):
        try:
            argv = open(path, 'rb').read().split(b'\0')
        except OSError:
            continue
        if len(argv) > 1 and argv[0].endswith(b'python') and argv[1] == b'detect.py':
            pids.append(int(path.split('/')[2]))
    return pids


results = []  # (ok, texte)


def report(ok, text):
    results.append(ok)
    print(('OK  ' if ok else 'KO  ') + text, flush=True)


# --- relais et détection : sans ROS ---
try:
    with urllib.request.urlopen(RELAY, timeout=3) as r:
        report(True, f'relais web ({RELAY})')
except Exception as e:
    report(False, f'relais web injoignable ({e}) : bash ~/hackathon/webui/start.sh')

pids = detect_pids()
age = min((time.time() - os.stat(f'/proc/{p}').st_ctime for p in pids), default=0)
if not os.path.exists(f'{DETECT_DIR}/best.pt'):
    report(False, f'modèle YOLO absent : {DETECT_DIR}/best.pt')
if len(pids) > 1:
    report(False, f'{len(pids)} detect.py lancés (PID {pids}) : un seul à la fois, sinon les caméras plantent')
elif not pids:
    report(False, 'détection YOLO arrêtée : ./run.sh depuis le PC la relance')
else:
    log = open(f'{DETECT_DIR}/detect.log', errors='replace').read() if os.path.exists(f'{DETECT_DIR}/detect.log') else ''
    report('sur GPU' in log or age < STARTUP, 'détection YOLO lancée' + (' sur GPU' if 'sur GPU' in log else
           ' (démarrage en cours)' if age < STARTUP else ' mais pas sur GPU : voir detect.log'))

# --- flux ROS : on s'abonne à tout en même temps et on attend la première donnée de chacun ---
rclpy.init()
node = rclpy.create_node('webui_check')
first = {}   # topic -> instant de la 1re donnée
count = {}
mic = {'ss': 0.0, 'n': 0}


def seen(topic):
    first.setdefault(topic, time.monotonic())
    count[topic] = count.get(topic, 0) + 1


def on_mic(msg):
    seen(MIC_TOPIC)
    x = np.frombuffer(bytes(msg.data.data), '<i2').reshape(-1, msg.info.channels)[:, :msg.mic_channels]
    mic.update(ss=mic['ss'] + float(np.square(x.astype(np.float64)).sum()), n=mic['n'] + x.size,
               channels=msg.info.channels, mic_channels=msg.mic_channels)


for topic, (_, kind) in CAMERAS.items():
    node.create_subscription(kind, topic, lambda _m, t=topic: seen(t), qos_profile_sensor_data)
if pids:
    node.create_subscription(CompressedImage, YOLO_TOPIC, lambda _m: seen(YOLO_TOPIC), qos_profile_sensor_data)
node.create_subscription(AudioCapture, MIC_TOPIC, on_mic, 10)

start = time.monotonic()
expected = len(CAMERAS) + 1 + bool(pids)
# 3 données par flux, ou au moins 2 s de son, ou le délai écoulé
while time.monotonic() - start < WAIT and not (len(first) == expected and min(count.values()) >= 3
                                               and time.monotonic() - first[MIC_TOPIC] > 2):
    rclpy.spin_once(node, timeout_sec=0.1)

for topic, (name, _) in CAMERAS.items():
    if topic in first:
        report(True, f'{name}, 1re image en {first[topic] - start:.1f} s')
    else:
        hint = ' (SoC2 peut mettre ~2 min : ./check.sh 150)' if 'front_center' in topic else ''
        report(False, f'{name} : aucune image en {WAIT:.0f} s{hint} ; caméra plantée ? '
                      'STATUS_TIMEOUT dans hal_camera.log = redémarrer le robot')
if pids:
    report(YOLO_TOPIC in first or age < STARTUP, 'YOLO publie des détections' if YOLO_TOPIC in first else
           f'YOLO : aucune détection en {WAIT:.0f} s' + (' (démarrage en cours)' if age < STARTUP else
                                                          ' (caméra frontale ? voir detect.log)'))
if MIC_TOPIC in first:
    report(*mic_verdict(mic['channels'], mic['mic_channels'],
                        10 * math.log10(max(mic['ss'] / mic['n'], 1e-3) / 32768 ** 2)))
else:
    report(False, f'micro : aucun son sur {MIC_TOPIC} en {WAIT:.0f} s (service audio de SoC2)')

node.destroy_node()
rclpy.try_shutdown()
sys.exit(0 if all(results) else 1)
