#!/usr/bin/env python3
"""Relais WebSocket <-> ROS 2 pour l'interface opérateur servie par le PC."""
import array
import asyncio
import math
import threading
import time

import cv2
import numpy as np
import rclpy
from aiohttp import WSMsgType, web
from cv_bridge import CvBridge
from rclpy.executors import SingleThreadedExecutor
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CompressedImage, Image
from aimdk_msgs.msg import (AudioCapture, AudioData, AudioInfo, AudioPlayback, CommonRequest,
                            CommonState, FocusRequester, McInputAction, McLocomotionVelocity,
                            MessageHeader)
from aimdk_msgs.srv import AbandonAudioFocus, GetMcAction, RequestAudioFocus, SetMcInputSource

HOST, PORT = '10.0.1.41', 8081  # réseau interne du robot seulement, pas l'interface Tailscale
FPS, MAX_W = 10, 960            # allège le wifi : 10 i/s, 960 px de large max
MAX_LIN, MIN_LIN = 0.5, 0.2     # m/s : le MC ignore < 0.2 ; bridé à 0.5 par prudence
MAX_ANG, MIN_ANG = 0.5, 0.1     # rad/s
DEADMAN = 0.3                   # s sans commande reçue -> vitesse nulle
SOURCE = 'node.locomotion_velocity'  # nom de source de l'exemple officiel
AUDIO_PKG = 'webui_relay'
IMAGE_TYPES = {'sensor_msgs/msg/Image': Image, 'sensor_msgs/msg/CompressedImage': CompressedImage}
CORS = {'Access-Control-Allow-Origin': '*'}


def limit(v, vmin, vmax):
    """0, ou |v| ramené dans [vmin, vmax] (seuils de démarrage du MC)."""
    if not isinstance(v, (int, float)) or not math.isfinite(v) or abs(v) < 0.05:
        return 0.0
    return math.copysign(min(max(abs(v), vmin), vmax), v)


assert limit(0.01, .2, .5) == 0 and limit(0.1, .2, .5) == .2 and limit(-3, .2, .5) == -.5
assert limit(float('nan'), .2, .5) == 0 and limit('1', .2, .5) == 0

rclpy.init()
node = rclpy.create_node('webui_relay')
bridge = CvBridge()
vel_pub = node.create_publisher(McLocomotionVelocity, '/aima/mc/locomotion/velocity', 10)
audio_pub = node.create_publisher(AudioPlayback, '/aima/hal/audio/playback', 10)
input_cli = node.create_client(SetMcInputSource, '/aimdk_5Fmsgs/srv/SetMcInputSource')
mode_cli = node.create_client(GetMcAction, '/aimdk_5Fmsgs/srv/GetMcAction')
focus_cli = node.create_client(RequestAudioFocus, '/aimdk_5Fmsgs/srv/RequestAudioFocus')
unfocus_cli = node.create_client(AbandonAudioFocus, '/aimdk_5Fmsgs/srv/AbandonAudioFocus')
# mono-thread : le MultiThreadedExecutor de Humble perdait l'audio. Callbacks légers, calculs dans asyncio.
executor = SingleThreadedExecutor()
executor.add_node(node)
threading.Thread(target=executor.spin, daemon=True).start()
drive_busy = False


async def call(client, req, tries=8):
    """Appel de service ROS depuis asyncio, avec les relances des exemples AimDK."""
    for _ in range(30):
        if client.service_is_ready():
            break
        await asyncio.sleep(0.1)
    else:
        raise RuntimeError(f'service {client.srv_name} indisponible')
    loop = asyncio.get_running_loop()
    for _ in range(tries):
        done = loop.create_future()
        fut = client.call_async(req)
        fut.add_done_callback(lambda f: loop.call_soon_threadsafe(
            lambda: done.done() or done.set_result(f)))
        try:
            return (await asyncio.wait_for(done, 0.25)).result()
        except asyncio.TimeoutError:
            client.remove_pending_request(fut)
    raise RuntimeError(f'service {client.srv_name} sans réponse')


def depth_to_bgr(d):
    mm = np.nan_to_num(d.astype(np.float32) * (1000 if d.dtype == np.float32 else 1))
    near, far = 300, 4000
    x = np.clip((far - mm) / (far - near), 0, 1)  # 1 = proche = rouge
    img = cv2.applyColorMap((x * 255).astype(np.uint8), cv2.COLORMAP_TURBO)
    img[mm <= 0] = 0
    h, w = mm.shape
    c = mm[h // 2 - 5:h // 2 + 5, w // 2 - 5:w // 2 + 5]
    c = c[c > 0]
    cv2.drawMarker(img, (w // 2, h // 2), (255, 255, 255), cv2.MARKER_CROSS, 40, 2)
    for i, txt in enumerate([f'centre : {np.median(c) / 1000:.2f} m' if c.size else 'centre : inconnu',
                             'rouge = proche (0.3 m)   bleu = loin (4 m et +)   noir = inconnu']):
        org, scale = (20, 50 + 45 * i), 1.2 if i == 0 else 0.8
        cv2.putText(img, txt, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 6, cv2.LINE_AA)
        cv2.putText(img, txt, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), 2, cv2.LINE_AA)
    return img


def to_jpeg(msg):
    if isinstance(msg, CompressedImage):
        # shortcut: taille du fichier comme indice de résolution (> 300 ko ≈ 2688 px) ; décodage moitié = 2x plus rapide
        flag = cv2.IMREAD_REDUCED_COLOR_2 if len(msg.data) > 300_000 else cv2.IMREAD_COLOR
        img = cv2.imdecode(np.frombuffer(msg.data, np.uint8), flag)
        if img is None:
            raise ValueError(f'image compressée illisible (format {msg.format})')
    elif msg.encoding in ('16UC1', '32FC1'):
        img = depth_to_bgr(bridge.imgmsg_to_cv2(msg))
    else:
        img = bridge.imgmsg_to_cv2(msg, 'bgr8')
    if img.shape[1] > MAX_W:
        img = cv2.resize(img, (MAX_W, round(img.shape[0] * MAX_W / img.shape[1])),
                         interpolation=cv2.INTER_AREA)
    return cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 70])[1].tobytes()


async def topics(_request):
    names = dict(node.get_topic_names_and_types())
    imgs = [n for n, t in sorted(names.items())
            if t[0] in IMAGE_TYPES and not n.endswith('compressedDepth')
            and n + '/compressed' not in names  # le brut pèse jusqu'à 15 Mo par image
            and not n.startswith(('/mono_perception/', '/perception/'))  # debug AgiBot, n'émet rien
            and not n.startswith('/camera/')  # doublon de rgbd_head_front, pilote orbbec intermittent
            and node.count_publishers(n) > 0]
    return web.json_response(imgs, headers=CORS)


async def serve(request, sender, on_message=None, cleanup=None):
    """Ouvre la WebSocket, lance `sender(ws)` et passe chaque message reçu à `on_message`."""
    ws = web.WebSocketResponse(heartbeat=5)
    await ws.prepare(request)
    task = asyncio.create_task(sender(ws))
    try:
        async for msg in ws:
            if on_message:
                try:
                    await on_message(ws, msg)
                except Exception as e:
                    await ws.send_json({'error': str(e)})
    finally:
        task.cancel()
        if cleanup:
            await cleanup()
    return ws


async def video(request):
    topic = request.query.get('topic', '')
    loop = asyncio.get_running_loop()
    latest, ready, last = {}, asyncio.Event(), [0.0]

    def deliver(msg):
        latest['msg'] = msg
        ready.set()

    def cb(msg):
        now = time.monotonic()
        if now - last[0] >= 1 / FPS:
            last[0] = now
            loop.call_soon_threadsafe(deliver, msg)

    async def sender(ws):
        types = dict(node.get_topic_names_and_types()).get(topic, [None])
        if types[0] not in IMAGE_TYPES:
            await ws.send_json({'error': f'topic image inconnu : {topic}'})
            return
        sub = node.create_subscription(IMAGE_TYPES[types[0]], topic, cb, qos_profile_sensor_data)
        try:
            while True:
                try:
                    await asyncio.wait_for(ready.wait(), 3)
                except asyncio.TimeoutError:
                    await ws.send_json({'error': f'aucune image reçue depuis 3 s sur {topic}'})
                    continue
                ready.clear()
                try:
                    await ws.send_bytes(await loop.run_in_executor(None, to_jpeg, latest['msg']))
                except Exception as e:  # image illisible : on le dit et on attend la suivante
                    await ws.send_json({'error': f'conversion impossible : {e}'})
        finally:
            node.destroy_subscription(sub)

    return await serve(request, sender)


async def focus(on):
    req = (RequestAudioFocus if on else AbandonAudioFocus).Request()
    req.request = CommonRequest()
    req.focus_requester = FocusRequester(pkg_name=AUDIO_PKG, priority=8)
    res = await call(focus_cli if on else unfocus_cli, req)
    if res.reponse.status.value != CommonState.SUCCESS:
        raise RuntimeError(f'focus audio refusé : {res.reponse.message}')
    return res.focus_response.focus_gain


async def audio(request):
    """Micro du robot -> navigateur ; navigateur -> haut-parleur du robot. PCM S16LE 16 kHz mono."""
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue(maxsize=50)
    talking = [False]

    def cb(msg):
        pcm = np.frombuffer(bytes(msg.data.data), '<i2').reshape(-1, msg.info.channels)[:, 0]
        loop.call_soon_threadsafe(lambda: queue.full() or queue.put_nowait(pcm.tobytes()))

    async def sender(ws):
        sub = node.create_subscription(AudioCapture, '/aima/hal/audio/capture', cb, 10)
        try:
            while True:
                try:
                    await ws.send_bytes(await asyncio.wait_for(queue.get(), 3))
                except asyncio.TimeoutError:
                    await ws.send_json({'error': 'aucun son reçu du micro du robot depuis 3 s'})
        finally:
            node.destroy_subscription(sub)

    async def on_message(ws, msg):
        if msg.type == WSMsgType.TEXT:
            want = bool(msg.json().get('talk'))
            talking[0] = want and await focus(True)
            if not want:
                await focus(False)
            await ws.send_json({'talk': talking[0]})
        elif msg.type == WSMsgType.BINARY and talking[0]:
            out = AudioPlayback(pkg_name=AUDIO_PKG, token_id=AUDIO_PKG)
            out.info = AudioInfo(channels=1, sample_rate=16000, sample_format='S16LE', coding_format='pcm')
            out.data = AudioData(data=array.array('B', msg.data))
            audio_pub.publish(out)

    async def cleanup():
        if talking[0]:
            await focus(False)

    return await serve(request, sender, on_message, cleanup)


def publish_velocity(vx=0.0, vy=0.0, wz=0.0):
    m = McLocomotionVelocity(source=SOURCE, forward_velocity=vx, lateral_velocity=vy, angular_velocity=wz)
    m.header = MessageHeader()
    m.header.stamp = node.get_clock().now().to_msg()
    vel_pub.publish(m)


async def register_input_source():
    req = SetMcInputSource.Request()
    req.input_source.name, req.input_source.priority, req.input_source.timeout = SOURCE, 40, 1000
    for action in (McInputAction.INPUTACTION_ADD, McInputAction.INPUTACTION_ENABLE):
        req.action.value = action
        req.request.header.stamp = node.get_clock().now().to_msg()
        if (await call(input_cli, req)).response.header.code == 0:
            return
    raise RuntimeError('enregistrement de la source de commande refusé par le MC')


async def drive(request):
    global drive_busy
    cmd = {'t': 0.0, 'v': (0.0, 0.0, 0.0)}
    owner = not drive_busy
    drive_busy = True

    async def sender(ws):
        if not owner:
            await ws.send_json({'error': 'robot déjà piloté depuis un autre navigateur'})
            return
        try:
            req = GetMcAction.Request()
            req.request = CommonRequest()
            await ws.send_json({'mode': (await call(mode_cli, req)).info.action_desc})
            await register_input_source()
            await ws.send_json({'status': 'prêt'})
        except Exception as e:
            await ws.send_json({'error': str(e)})
            return
        while True:
            fresh = time.monotonic() - cmd['t'] < DEADMAN
            publish_velocity(*(cmd['v'] if fresh else (0.0, 0.0, 0.0)))
            await asyncio.sleep(0.02)

    async def on_message(_ws, msg):
        if msg.type == WSMsgType.TEXT and owner:
            d = msg.json()
            cmd['v'] = (limit(d.get('vx'), MIN_LIN, MAX_LIN), limit(d.get('vy'), MIN_LIN, MAX_LIN),
                        limit(d.get('wz'), MIN_ANG, MAX_ANG))
            cmd['t'] = time.monotonic()

    async def cleanup():
        global drive_busy
        if owner:
            for _ in range(10):  # arrêt net à la déconnexion
                publish_velocity()
                await asyncio.sleep(0.02)
            drive_busy = False

    return await serve(request, sender, on_message, cleanup)


app = web.Application()
app.add_routes([web.get('/topics', topics), web.get('/video', video),
                web.get('/audio', audio), web.get('/drive', drive)])
try:
    web.run_app(app, host=HOST, port=PORT, shutdown_timeout=1)  # sinon l'ancien relais vit 60 s de plus
finally:
    rclpy.shutdown()
