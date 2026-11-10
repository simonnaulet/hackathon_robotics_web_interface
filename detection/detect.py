"""Détection EPI en direct : caméra avant du robot -> YOLO -> image annotée publiée pour Foxglove."""
import argparse
import time

import cv2
import numpy as np
import rclpy
import torch
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import CompressedImage
from ultralytics import YOLO

IN_TOPIC = "/aima/hal/sensor/rgb_head_front_center/rgb_image/compressed"
OUT_TOPIC = "/hackathon/epi/detections/compressed"


class EpiDetector(Node):
    def __init__(self, model_path, conf, out_width):
        super().__init__("epi_detector")
        self.device = 0 if torch.cuda.is_available() else "cpu"
        self.model = YOLO(model_path)
        self.conf = conf
        self.out_width = out_width
        # Une seule image en attente : si YOLO est plus lent que la caméra, on traite toujours la plus récente.
        qos = QoSProfile(reliability=ReliabilityPolicy.BEST_EFFORT, history=HistoryPolicy.KEEP_LAST, depth=1)
        self.pub = self.create_publisher(CompressedImage, OUT_TOPIC, qos)
        self.create_subscription(CompressedImage, IN_TOPIC, self.on_image, qos)
        self.frames, self.t0 = 0, time.time()
        self.get_logger().info(f"{model_path} sur {'GPU' if self.device == 0 else 'CPU'}, classes : {self.model.names}")

    def on_image(self, msg):
        # Personne ne regarde : on reste abonné à la caméra (SoC2 met ~2 min à servir un nouvel abonné) sans calcul GPU.
        if self.pub.get_subscription_count() == 0:
            self.frames, self.t0 = 0, time.time()
            return
        # Décodage à mi-résolution : 4x moins de travail, et YOLO réduit l'image à 640 px de toute façon.
        img = cv2.imdecode(np.frombuffer(msg.data, np.uint8), cv2.IMREAD_REDUCED_COLOR_2)
        if img is None:
            self.get_logger().warn("image JPEG illisible, ignorée")
            return
        result = self.model.predict(img, conf=self.conf, device=self.device, verbose=False)[0]

        out = result.plot()
        h, w = out.shape[:2]
        out = cv2.resize(out, (self.out_width, h * self.out_width // w))
        ok, jpg = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if ok:
            self.pub.publish(CompressedImage(header=msg.header, format="jpeg", data=jpg.tobytes()))

        self.frames += 1
        if self.frames % 50 == 0:
            now = time.time()
            fps, self.t0 = 50 / (now - self.t0), now  # sur les 50 dernières images
            names = [self.model.names[int(c)] for c in result.boxes.cls]
            self.get_logger().info(f"{fps:.1f} images/s, dernière image : {names or 'rien'}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="best.pt")
    parser.add_argument("--conf", type=float, default=0.4, help="seuil de confiance")
    parser.add_argument("--width", type=int, default=960, help="largeur de l'image publiée (px)")
    args, _ = parser.parse_known_args()

    rclpy.init()
    node = EpiDetector(args.model, args.conf, args.width)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
