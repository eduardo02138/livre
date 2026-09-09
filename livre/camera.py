"""Captura de webcam e extração de marcos de mão.

Usa primariamente o Rastreador ONNX Runtime (compatível nativamente com
Python 3.14 no CachyOS) com fallback para MediaPipe.
"""

import sys
import time

try:
    import cv2
except ImportError:
    cv2 = None


class CameraMao:
    """Fonte de marcos a partir da webcam real."""

    def __init__(self, indice_camera=0, largura=640, altura=480, fps=30):
        if cv2 is None:
            raise RuntimeError(
                "OpenCV não instalado.\n"
                "Execute: sudo pacman -S --needed python-opencv python-onnxruntime-cpu"
            )

        self.cap = cv2.VideoCapture(indice_camera, cv2.CAP_V4L2)
        if not self.cap.isOpened():
            self.cap = cv2.VideoCapture(indice_camera)

        if not self.cap.isOpened():
            raise RuntimeError(f"Não foi possível abrir a câmera no índice {indice_camera}.")

        # Força formato MJPG para garantir 30 FPS sem gargalo
        # OpenCV 5 moveu o helper para VideoWriter.fourcc; 4.x usa o nome antigo.
        _fourcc = getattr(cv2, "VideoWriter_fourcc", None) or cv2.VideoWriter.fourcc
        fourcc = _fourcc(*"MJPG")
        self.cap.set(cv2.CAP_PROP_FOURCC, fourcc)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, largura)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, altura)
        self.cap.set(cv2.CAP_PROP_FPS, fps)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        # Desativa redução automática de frame rate do driver UVC (garante 30 FPS constantes)
        import subprocess
        try:
            subprocess.run(
                ["v4l2-ctl", "-d", f"/dev/video{indice_camera}", "--set-ctrl=exposure_dynamic_framerate=0"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except Exception:
            pass

        self.largura = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.altura = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        print(f"📷 Câmera iniciada: {self.largura}x{self.altura} @ {fps} FPS")

        # Inicializa o rastreador
        self.tracker_onnx = None
        self.tracker_mp = None

        try:
            from .rastreador_onnx import RastreadorONNX
            self.tracker_onnx = RastreadorONNX()
        except Exception as e:
            print(f"Aviso ao carregar ONNX: {e}")
            try:
                import mediapipe as mp
                self.mp_hands = mp.solutions.hands
                self.tracker_mp = self.mp_hands.Hands(
                    static_image_mode=False,
                    max_num_hands=1,
                    model_complexity=1,
                    min_detection_confidence=0.6,
                    min_tracking_confidence=0.6,
                )
            except Exception as e2:
                print(f"Aviso ao carregar MediaPipe: {e2}")

        if not self.tracker_onnx and not self.tracker_mp:
            raise RuntimeError(
                "Nenhum mecanismo de inferência disponível.\n"
                "Instale com: sudo pacman -S --needed python-opencv python-onnxruntime-cpu"
            )

    def ler(self):
        """Captura um quadro e extrai os 21 marcos normalizados [0..1]."""
        sucesso, frame = self.cap.read()
        if not sucesso or frame is None:
            return None, None

        # Espelhamento horizontal essencial
        frame = cv2.flip(frame, 1)

        marcos = None
        if self.tracker_onnx:
            marcos = self.tracker_onnx.estimar_marcos(frame)
        elif self.tracker_mp:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            res = self.tracker_mp.process(rgb)
            if res.multi_hand_landmarks:
                mao = res.multi_hand_landmarks[0]
                marcos = [(lm.x, lm.y) for lm in mao.landmark]

        return frame, marcos

    def fechar(self):
        if self.cap and self.cap.isOpened():
            self.cap.release()
        if self.tracker_mp:
            self.tracker_mp.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.fechar()
