"""Rastreador de face, olhos e boca via ONNX Runtime.

Utiliza os modelos MediaPipe (BlazeFace + Face Landmark 468) em ONNX:
  • face_detection.onnx (128x128) -> Detecção de bounding box da face
  • face_landmark.onnx (192x192)  -> 468 marcos tridimensionais da face

Biometria calculada:
  • EAR (Eye Aspect Ratio) para Olho Direito e Olho Esquerdo
  • Diferenciação rigorosa: Piscadela Unilateral vs Piscada Bilateral Natural
  • MAR (Mouth Aspect Ratio) para Abertura da Boca (Habilidade Suprema / Ultimate)
"""

import math
import os
from dataclasses import dataclass, field
import numpy as np

try:
    import cv2
except ImportError:
    cv2 = None

try:
    import onnxruntime as ort
except ImportError:
    ort = None

from .filtros import Histerese

ENTRADA_FACE = 128
ENTRADA_MARCOS_FACE = 192
# Quadros de olhos fechados para a piscada longa contar como comando.
# 12 quadros a 30 fps = ~400 ms; a piscada natural fica em 100-150 ms.
QUADROS_PISCADA_LONGA = 12

LIMIAR_FACE = 0.50
ESCALA_ROI_FACE = 1.45

# Índices MediaPipe Face Mesh para EAR e MAR
# Na câmera espelhada (cv2.flip(frame, 1)), o olho direito do usuário aparece no lado direito da tela (maior x).
INDICES_OLHO_DIR = {
    "cantos": (263, 362),
    "topo": (385, 387),
    "base": (380, 373),
}

INDICES_OLHO_ESQ = {
    "cantos": (33, 133),
    "topo": (160, 158),
    "base": (144, 153),
}

INDICES_BOCA = {
    "labios": (13, 14),
    "cantos": (61, 291),
}


@dataclass
class EstadoRosto:
    tem_rosto: bool = False
    ear_dir: float = 0.30
    ear_esq: float = 0.30
    mar: float = 0.05
    piscadela_direita: bool = False
    piscadela_esquerda: bool = False
    boca_aberta: bool = False
    piscando_ambos: bool = False
    piscada_longa: bool = False
    caixa_rosto: tuple = None
    marcos_olho_dir: list = field(default_factory=list)
    marcos_olho_esq: list = field(default_factory=list)
    marcos_boca: list = field(default_factory=list)


def _ancoras_face():
    """896 âncoras SSD para BlazeFace 128x128."""
    saida = []
    for passo, n_por_celula in ((8, 2), (16, 6)):
        lado = ENTRADA_FACE // passo
        for y in range(lado):
            for x in range(lado):
                cx = (x + 0.5) / lado
                cy = (y + 0.5) / lado
                for _ in range(n_por_celula):
                    saida.append((cx, cy))
    return np.array(saida, dtype=np.float32)


def _sigmoide(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -50.0, 50.0)))


class RastreadorRostoONNX:
    def __init__(self, diretorio_modelos=None):
        self._cont_piscada_longa = 0
        if ort is None or cv2 is None:
            raise RuntimeError("OpenCV e ONNX Runtime são necessários para o rastreamento facial.")

        if diretorio_modelos is None:
            raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            diretorio_modelos = os.path.join(raiz, "modelos")

        caminho_fd = os.path.join(diretorio_modelos, "face_detection.onnx")
        caminho_fl = os.path.join(diretorio_modelos, "face_landmark.onnx")

        if not os.path.exists(caminho_fd) or not os.path.exists(caminho_fl):
            raise FileNotFoundError(f"Modelos faciais ONNX não encontrados em {diretorio_modelos}. Execute baixar_modelos.sh.")

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        self._s_face = ort.InferenceSession(caminho_fd, opts, providers=["CPUExecutionProvider"])
        self._s_marcos = ort.InferenceSession(caminho_fl, opts, providers=["CPUExecutionProvider"])

        self._in_face = self._s_face.get_inputs()[0].name
        self._in_marcos = self._s_marcos.get_inputs()[0].name

        self._ancoras = _ancoras_face()
        self._roi = None  # (cx, cy, lado, ang) para rastreamento temporal

        # Histerese temporal para cada gesto facial (elimina flickers)
        # 1.0 quando ativo, 0.0 quando inativo
        self._hist_olho_dir = Histerese(0.70, 0.30, quadros_confirmacao=2, quadros_libera=2)
        self._hist_olho_esq = Histerese(0.70, 0.30, quadros_confirmacao=2, quadros_libera=2)
        self._hist_boca = Histerese(0.38, 0.26, quadros_confirmacao=2, quadros_libera=2)

    def _detectar_rosto(self, quadro_bgr):
        """Executa BlazeFace no quadro completo para obter o recorte inicial."""
        h, w = quadro_bgr.shape[:2]
        lado_q = max(h, w)
        quadrado = np.zeros((lado_q, lado_q, 3), dtype=np.uint8)
        quadrado[:h, :w] = quadro_bgr

        img = cv2.resize(cv2.cvtColor(quadrado, cv2.COLOR_BGR2RGB), (ENTRADA_FACE, ENTRADA_FACE))
        tensor = (img.astype(np.float32) / 255.0)[None]

        out = self._s_face.run(None, {self._in_face: tensor})
        cls = np.concatenate([out[0][0], out[1][0]], axis=0)
        reg = np.concatenate([out[2][0], out[3][0]], axis=0)

        scores = _sigmoide(cls[:, 0])
        candidatos = np.where(scores > LIMIAR_FACE)[0]
        if candidatos.size == 0:
            return None

        idx = candidatos[np.argmax(scores[candidatos])]

        cx = (reg[idx, 0] / ENTRADA_FACE + self._ancoras[idx, 0]) * lado_q
        cy = (reg[idx, 1] / ENTRADA_FACE + self._ancoras[idx, 1]) * lado_q
        bw = (reg[idx, 2] / ENTRADA_FACE) * lado_q
        bh = (reg[idx, 3] / ENTRADA_FACE) * lado_q

        # Keypoints: olho direito (reg 4,5), olho esquerdo (reg 6,7)
        re_x = (reg[idx, 4] / ENTRADA_FACE + self._ancoras[idx, 0]) * lado_q
        re_y = (reg[idx, 5] / ENTRADA_FACE + self._ancoras[idx, 1]) * lado_q
        le_x = (reg[idx, 6] / ENTRADA_FACE + self._ancoras[idx, 0]) * lado_q
        le_y = (reg[idx, 7] / ENTRADA_FACE + self._ancoras[idx, 1]) * lado_q

        dx = le_x - re_x
        dy = le_y - re_y
        ang = math.degrees(math.atan2(dy, dx))
        tamanho = max(bw, bh) * ESCALA_ROI_FACE

        return (cx, cy, tamanho, ang)

    def _marcos_do_roi(self, quadro_bgr, roi):
        """Extrai os 468 marcos tridimensionais a partir da região de interesse."""
        cx, cy, lado, ang = roi
        if lado < 16:
            return None, 0.0

        M = cv2.getRotationMatrix2D((cx, cy), ang, ENTRADA_MARCOS_FACE / lado)
        M[0, 2] += ENTRADA_MARCOS_FACE / 2 - cx
        M[1, 2] += ENTRADA_MARCOS_FACE / 2 - cy

        recorte = cv2.warpAffine(quadro_bgr, M, (ENTRADA_MARCOS_FACE, ENTRADA_MARCOS_FACE), borderMode=cv2.BORDER_REPLICATE)
        img = cv2.cvtColor(recorte, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        tensor = np.transpose(img, (2, 0, 1))[None]

        out = self._s_marcos.run(None, {self._in_marcos: tensor})
        pontos = out[0].reshape(468, 3)
        score_val = float(out[1].reshape(-1)[0])

        Minv = cv2.invertAffineTransform(M)
        xy = np.stack([pontos[:, 0], pontos[:, 1], np.ones(468)], axis=1)
        pontos_quadro = xy @ Minv.T

        return pontos_quadro, score_val

    def _roi_dos_marcos(self, marcos):
        """Recalcula ROI da face a partir dos marcos tridimensionais."""
        xs = marcos[:, 0]
        ys = marcos[:, 1]
        x1, x2 = np.min(xs), np.max(xs)
        y1, y2 = np.min(ys), np.max(ys)
        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0
        bw = x2 - x1
        bh = y2 - y1

        # Rotação pelos cantos dos olhos (33 e 263)
        dx = marcos[263, 0] - marcos[33, 0]
        dy = marcos[263, 1] - marcos[33, 1]
        ang = math.degrees(math.atan2(dy, dx))
        lado = max(bw, bh) * 1.35
        return (cx, cy, lado, ang)

    def estimar(self, quadro_bgr):
        """Processa o quadro e retorna métricas biométricas dos olhos e boca."""
        if self._roi is None:
            self._roi = self._detectar_rosto(quadro_bgr)
            if self._roi is None:
                return EstadoRosto(tem_rosto=False)

        marcos, score = self._marcos_do_roi(quadro_bgr, self._roi)
        if marcos is None or score < 10.0:  # Score do Face Landmark varia em torno de 20-50
            self._roi = self._detectar_rosto(quadro_bgr)
            if self._roi is None:
                return EstadoRosto(tem_rosto=False)
            marcos, score = self._marcos_do_roi(quadro_bgr, self._roi)
            if marcos is None or score < 10.0:
                self._roi = None
                return EstadoRosto(tem_rosto=False)

        # Atualiza ROI para o próximo quadro
        self._roi = self._roi_dos_marcos(marcos)

        # -------------------------------------------------------------
        # CÁLCULO DE EAR (Eye Aspect Ratio)
        # -------------------------------------------------------------
        def calc_ear(cfg):
            p1, p4 = (marcos[i] for i in cfg["cantos"])
            p2, p3 = (marcos[i] for i in cfg["topo"])
            p6, p5 = (marcos[i] for i in cfg["base"])
            largura = math.hypot(p1[0] - p4[0], p1[1] - p4[1])
            if largura < 1e-5:
                return 0.30
            v1 = math.hypot(p2[0] - p6[0], p2[1] - p6[1])
            v2 = math.hypot(p3[0] - p5[0], p3[1] - p5[1])
            return (v1 + v2) / (2.0 * largura)

        ear_dir = calc_ear(INDICES_OLHO_DIR)
        ear_esq = calc_ear(INDICES_OLHO_ESQ)

        # -------------------------------------------------------------
        # CÁLCULO DE MAR (Mouth Aspect Ratio)
        # -------------------------------------------------------------
        p_sup, p_inf = (marcos[i] for i in INDICES_BOCA["labios"])
        p_c1, p_c2 = (marcos[i] for i in INDICES_BOCA["cantos"])
        largura_boca = math.hypot(p_c1[0] - p_c2[0], p_c1[1] - p_c2[1])
        altura_boca = math.hypot(p_sup[0] - p_inf[0], p_sup[1] - p_inf[1])
        mar = altura_boca / max(1e-5, largura_boca)

        # -------------------------------------------------------------
        # FILTRO ANTI-FALSO-POSITIVO (Piscada Natural Bilateral vs Unilateral)
        # -------------------------------------------------------------
        # Se ambos os olhos fecharem (< 0.19), é uma piscada fisiológica involuntária -> IGNORAR
        piscando_ambos = (ear_dir < 0.19 and ear_esq < 0.19)

        # Piscadela unilateral direita: olho direito fechado, esquerdo bem aberto
        candidato_wink_dir = (ear_dir < 0.17 and ear_esq >= 0.22) and not piscando_ambos
        # Piscadela unilateral esquerda: olho esquerdo fechado, direito bem aberto
        candidato_wink_esq = (ear_esq < 0.17 and ear_dir >= 0.22) and not piscando_ambos

        # Histerese suave
        piscadela_dir = self._hist_olho_dir(1.0 if candidato_wink_dir else 0.0)
        piscadela_esq = self._hist_olho_esq(1.0 if candidato_wink_esq else 0.0)
        boca_aberta = self._hist_boca(mar)

        # PISCADA LONGA: fechar os DOIS olhos e SEGURAR.
        #
        # Piscar os dois e involuntario — acontece 15 a 20 vezes por minuto —,
        # e por isso 'piscando_ambos' sozinho nunca pode virar comando: dispararia
        # uma habilidade a cada poucos segundos sem voce querer.
        #
        # O que separa e a DURACAO. Piscada natural dura 100-150 ms; segurar os
        # olhos fechados de proposito passa facil de 400 ms. Com QUADROS_PISCADA_LONGA
        # quadros a 30 fps o piso fica em ~400 ms, acima da natural com folga.
        if piscando_ambos:
            self._cont_piscada_longa += 1
        else:
            self._cont_piscada_longa = 0
        piscada_longa = self._cont_piscada_longa >= QUADROS_PISCADA_LONGA

        cx_box, cy_box, lado_box, _ = self._roi
        caixa = (
            int(cx_box - lado_box / 2),
            int(cy_box - lado_box / 2),
            int(lado_box),
            int(lado_box)
        )

        return EstadoRosto(
            tem_rosto=True,
            ear_dir=ear_dir,
            ear_esq=ear_esq,
            mar=mar,
            piscadela_direita=piscadela_dir,
            piscadela_esquerda=piscadela_esq,
            boca_aberta=boca_aberta,
            piscando_ambos=piscando_ambos,
            piscada_longa=piscada_longa,
            caixa_rosto=caixa,
            marcos_olho_dir=[marcos[i] for i in INDICES_OLHO_DIR["cantos"] + INDICES_OLHO_DIR["topo"] + INDICES_OLHO_DIR["base"]],
            marcos_olho_esq=[marcos[i] for i in INDICES_OLHO_ESQ["cantos"] + INDICES_OLHO_ESQ["topo"] + INDICES_OLHO_ESQ["base"]],
            marcos_boca=[marcos[i] for i in INDICES_BOCA["labios"] + INDICES_BOCA["cantos"]],
        )
