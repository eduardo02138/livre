"""Rastreador de mao em dois estagios, via ONNX Runtime.

    palm_detection.onnx   [1,3,128,128] -> regressors [1,896,18]
                                           classificators [1,896,1]
    hand_landmark.onnx    [1,3,224,224] -> marcos [1,63], score [1,1], lado [1,1]

Os dois estagios existem por necessidade, nao por elegancia: o modelo de
marcos foi treinado em recortes JUSTOS e ROTACIONADOS, com a mao preenchendo
o quadro. Alimentar ele com a cena inteira devolve 21 pontos de ruido com
aparencia de esqueleto. O detector de palma existe para produzir esse
recorte.

Depois do primeiro acerto, a regiao de interesse e reaproveitada a partir
dos proprios marcos e o detector so volta a rodar quando a mao se perde —
que e o que torna o custo por quadro aceitavel numa CPU.
"""

import math
import os
import time

import numpy as np

try:
    import cv2
except ImportError:
    cv2 = None

try:
    import onnxruntime as ort
except ImportError:
    ort = None

ENTRADA_PALMA = 128
ENTRADA_MARCOS = 224
N_ANCORAS = 896

# Recorte da mao a partir da caixa da palma, como no grafo original do
# MediaPipe: alarga 2.6x e desloca meia altura na direcao dos dedos.
ESCALA_ROI = 2.6
DESLOC_Y_ROI = -0.5

LIMIAR_PALMA = 0.45
LIMIAR_MARCOS = 0.50
# Piso de tamanho para candidato do detector de palma, como FRACAO DA ALTURA
# do quadro — nao em pixels absolutos, que sao validos so em 640x480.
#
# Serve para descartar sombra e dobra de roupa, que apareciam com 47-68px em
# 480 de altura (10-14%). Ficou em 27% (130px) por um tempo e isso era
# apertado demais: com a mao a ~60cm da camera o candidato legitimo cai perto
# de 130px e comeca a ser rejeitado, o detector devolve None, e o rastreio
# entra em ciclo de redeteccao — 6,4 por segundo, que na tela aparece como
# esqueleto travando.
#
# 19% (90px em 480) fica acima do ruido de fundo com folga e recupera a faixa
# de distancia em que a mao realmente e usada.
MIN_ROI_FRACAO_ALTURA = 0.19
MIN_ROI_PIXELS = 130.0  # mantido para compatibilidade; use _min_roi_px()


def _ancoras():
    """As 896 ancoras SSD do BlazePalm 128x128.

    Grade 16x16 com 2 ancoras por celula (passo 8) mais tres grades 8x8 com
    2 ancoras cada (passo 16): 512 + 384 = 896.
    """
    saida = []
    for passo, n_por_celula in ((8, 2), (16, 6)):
        lado = ENTRADA_PALMA // passo
        for y in range(lado):
            for x in range(lado):
                cx = (x + 0.5) / lado
                cy = (y + 0.5) / lado
                for _ in range(n_por_celula):
                    saida.append((cx, cy))
    return np.array(saida, dtype=np.float32)


def _sigmoide(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -50.0, 50.0)))


def _iou(caixa, outras):
    x1 = np.maximum(caixa[0], outras[:, 0])
    y1 = np.maximum(caixa[1], outras[:, 1])
    x2 = np.minimum(caixa[2], outras[:, 2])
    y2 = np.minimum(caixa[3], outras[:, 3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    area = lambda c: (c[..., 2] - c[..., 0]) * (c[..., 3] - c[..., 1])
    return inter / (area(caixa) + area(outras) - inter + 1e-9)


class RastreadorONNX:
    def __init__(self, diretorio_modelos=None):
        if cv2 is None or ort is None:
            raise RuntimeError(
                "faltam dependencias. instale com:\n"
                "  sudo pacman -S --needed python-opencv python-onnxruntime-cpu"
            )

        raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if diretorio_modelos is None:
            diretorio_modelos = os.path.join(raiz, "modelos")

        caminhos = {
            "palma": os.path.join(diretorio_modelos, "palm_detection.onnx"),
            "marcos": os.path.join(diretorio_modelos, "hand_landmark.onnx"),
        }
        for nome, c in caminhos.items():
            if not os.path.exists(c):
                raise FileNotFoundError(f"modelo '{nome}' ausente: {c}")

        opcoes = ort.SessionOptions()
        opcoes.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        opcoes.intra_op_num_threads = 2
        opcoes.log_severity_level = 3   # cala os avisos de initializer nao usado
        prov = ["CPUExecutionProvider"]

        self._s_palma = ort.InferenceSession(caminhos["palma"], opcoes, providers=prov)
        self._s_marcos = ort.InferenceSession(caminhos["marcos"], opcoes, providers=prov)
        self._in_palma = self._s_palma.get_inputs()[0].name
        self._in_marcos = self._s_marcos.get_inputs()[0].name

        self._ancoras = _ancoras()
        self._roi = None          # (cx, cy, lado, angulo_graus) em pixels do quadro
        self.score = 0.0
        self.detectou_palma = False
        self._perdas_consecutivas = 0
        self._ultimo_resultado = None

        self._w = 640
        self._h = 480
        pasta_log = os.path.join(raiz, "logs")
        try:
            os.makedirs(pasta_log, exist_ok=True)
            self._arquivo_log = os.path.join(pasta_log, "rastreador.log")
            with open(self._arquivo_log, "a") as f:
                f.write(f"\n--- sessao iniciada {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        except OSError:
            self._arquivo_log = None
        self._m = {"quadros": 0, "perdidos": 0, "sem_mao": 0, "perdeu_rastreio": 0, "redeteccoes": 0,
                   "ms": 0.0, "score": 0.0, "lado": 0.0, "t": time.time()}

    # ---------------------------------------------------------------- palma

    def _min_roi_px(self):
        """Piso do candidato de palma, em pixels, para a resolucao atual."""
        return MIN_ROI_FRACAO_ALTURA * float(self._h or 480)

    def _detectar_palma(self, quadro):
        """Roda o detector na cena inteira. Devolve ROI em pixels ou None."""
        h, w = quadro.shape[:2]
        lado = max(h, w)
        ox, oy = (lado - w) // 2, (lado - h) // 2

        quadrado = np.zeros((lado, lado, 3), dtype=quadro.dtype)
        quadrado[oy:oy + h, ox:ox + w] = quadro

        img = cv2.resize(cv2.cvtColor(quadrado, cv2.COLOR_BGR2RGB),
                         (ENTRADA_PALMA, ENTRADA_PALMA))
        tensor = np.transpose(img.astype(np.float32) / 255.0, (2, 0, 1))[None]

        reg, cls = self._s_palma.run(None, {self._in_palma: tensor})
        reg = reg[0]                       # (896, 18)
        pontos = _sigmoide(cls[0][:, 0])   # (896,)

        candidatos = np.where(pontos > LIMIAR_PALMA)[0]
        if candidatos.size == 0:
            return None

        anc = self._ancoras[candidatos]
        r = reg[candidatos]
        cx = r[:, 0] / ENTRADA_PALMA + anc[:, 0]
        cy = r[:, 1] / ENTRADA_PALMA + anc[:, 1]
        bw = r[:, 2] / ENTRADA_PALMA
        bh = r[:, 3] / ENTRADA_PALMA

        caixas = np.stack([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], axis=1)
        ordem = np.argsort(-pontos[candidatos])
        caixas, r, cx, cy, bw, bh = (a[ordem] for a in (caixas, r, cx, cy, bw, bh))

        mantidos = []
        restantes = np.arange(len(caixas))
        while restantes.size:
            i = restantes[0]
            lado_roi = max(bw[i], bh[i]) * lado * ESCALA_ROI
            if lado_roi >= self._min_roi_px():
                mantidos.append(i)
            if restantes.size == 1:
                break
            restantes = restantes[1:][_iou(caixas[i], caixas[restantes[1:]]) < 0.3]

        if not mantidos:
            return None
        i = mantidos[0]

        # Keypoints 0 (base da palma) e 2 (base do dedo medio) dao a rotacao.
        kx0 = r[i, 4] / ENTRADA_PALMA + self._ancoras[candidatos][ordem][i, 0]
        ky0 = r[i, 5] / ENTRADA_PALMA + self._ancoras[candidatos][ordem][i, 1]
        kx2 = r[i, 8] / ENTRADA_PALMA + self._ancoras[candidatos][ordem][i, 0]
        ky2 = r[i, 9] / ENTRADA_PALMA + self._ancoras[candidatos][ordem][i, 1]

        # Vetor base -> dedo medio (direcao anatomica dos dedos)
        vx = (kx2 - kx0) * lado
        vy = (ky2 - ky0) * lado
        rot = math.atan2(vx, -vy)
        ang = math.degrees(rot)

        # Desloca o centro da palma em direcao aos dedos (-0.5 da altura na orientacao)
        alt = bh[i] * lado
        dx = 0.5 * alt * math.sin(rot)
        dy = -0.5 * alt * math.cos(rot)
        px = cx[i] * lado - ox + dx
        py = cy[i] * lado - oy + dy

        maior = max(bw[i], bh[i]) * lado
        return (px, py, maior * ESCALA_ROI, ang)

    # --------------------------------------------------------------- marcos

    def _marcos_do_roi(self, quadro, roi):
        """Recorta, endireita e roda o modelo de marcos. Devolve pontos em pixels."""
        cx, cy, lado, ang = roi
        if lado < 8:
            return None, 0.0

        M = cv2.getRotationMatrix2D((cx, cy), ang, ENTRADA_MARCOS / lado)
        M[0, 2] += ENTRADA_MARCOS / 2 - cx
        M[1, 2] += ENTRADA_MARCOS / 2 - cy
        recorte = cv2.warpAffine(
            quadro, M, (ENTRADA_MARCOS, ENTRADA_MARCOS),
            borderMode=cv2.BORDER_REPLICATE
        )

        img = cv2.cvtColor(recorte, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        tensor = np.transpose(img, (2, 0, 1))[None]

        saidas = self._s_marcos.run(None, {self._in_marcos: tensor})
        pontos = saidas[0].reshape(-1)[: 21 * 3].reshape(21, 3)
        # O modelo ONNX do MediaPipe já inclui ativação sigmoide no score de saída
        val_score = float(np.array(saidas[1]).reshape(-1)[0])
        score = _sigmoide(val_score) if val_score > 1.0 or val_score < 0.0 else val_score

        # Do espaco 224x224 de volta para pixels do quadro original.
        Minv = cv2.invertAffineTransform(M)
        xy = np.stack([pontos[:, 0], pontos[:, 1], np.ones(21)], axis=1)
        volta = xy @ Minv.T
        return volta, score

    # ---------------------------------------------------------------- publico

    def estimar_marcos(self, quadro_bgr):
        """Devolve 21 pares (x, y) normalizados no quadro, ou None."""
        t_ini = time.perf_counter()
        h, w = quadro_bgr.shape[:2]
        self._h, self._w = h, w
        self.detectou_palma = False

        tinha_roi = self._roi is not None

        # Reaproveita a ROI do quadro anterior; so chama o detector se perdeu.
        if self._roi is None:
            self._roi = self._detectar_palma(quadro_bgr)
            self.detectou_palma = self._roi is not None
            if self._roi is None:
                self.score = 0.0
                return self._registrar(None, t_ini, motivo="sem_mao")

        pontos, score = self._marcos_do_roi(quadro_bgr, self._roi)

        # Perdeu a mao ou confianca baixa: tenta redetectar palma no mesmo quadro
        if pontos is None or score < LIMIAR_MARCOS:
            nova_roi = self._detectar_palma(quadro_bgr)
            if nova_roi is not None:
                novos_pontos, novo_score = self._marcos_do_roi(quadro_bgr, nova_roi)
                if novos_pontos is not None and novo_score >= LIMIAR_MARCOS:
                    self._roi = nova_roi
                    pontos = novos_pontos
                    score = novo_score
                    self.detectou_palma = True

            if pontos is None or score < LIMIAR_MARCOS:
                self._roi = None
                self._ultimo_resultado = None
                self.score = score
                motivo = "perdeu_rastreio" if tinha_roi else "sem_mao"
                return self._registrar(None, t_ini, motivo=motivo)

        self._perdas_consecutivas = 0
        self.score = score
        self._roi = self._roi_dos_marcos(pontos, max_lado=float(max(h, w) * 1.5))
        if self._roi is None:
            self._ultimo_resultado = None
            return self._registrar(None, t_ini, motivo="perdeu_rastreio")
        resultado = [(float(p[0]) / w, float(p[1]) / h) for p in pontos]
        self._ultimo_resultado = resultado
        return self._registrar(resultado, t_ini)

    # ------------------------------------------------------------- telemetria

    def _registrar(self, resultado, t_ini, motivo=None):
        """Acumula metricas e grava um resumo em arquivo a cada segundo.

        Distingue claramente entre 'sem_mao' (ausente) e 'perdeu_rastreio' (falha real).
        """
        self._m["quadros"] += 1
        self._m["ms"] += (time.perf_counter() - t_ini) * 1000.0
        if self.detectou_palma:
            self._m["redeteccoes"] += 1
        if resultado is None:
            self._m["perdidos"] += 1
            if motivo == "sem_mao":
                self._m["sem_mao"] += 1
            else:
                self._m["perdeu_rastreio"] += 1
        else:
            self._m["score"] += self.score
            if self._roi:
                self._m["lado"] += self._roi[2]

        agora = time.time()
        if agora - self._m["t"] >= 1.0 and self._arquivo_log:
            n = max(1, self._m["quadros"])
            ok = max(1, n - self._m["perdidos"])
            pct_sem_mao = 100.0 * self._m["sem_mao"] / n
            pct_perdeu = 100.0 * self._m["perdeu_rastreio"] / n
            linha = (
                f"{time.strftime('%H:%M:%S')} "
                f"fps={n / (agora - self._m['t']):5.1f} "
                f"ms/quadro={self._m['ms'] / n:5.1f} "
                f"sem_mao={pct_sem_mao:5.1f}% "
                f"perdeu_rastreio={pct_perdeu:5.1f}% "
                f"redeteccoes={self._m['redeteccoes']:3d} "
                f"score={self._m['score'] / ok:.3f} "
                f"roi_lado={self._m['lado'] / ok:6.1f}px\n"
            )
            try:
                with open(self._arquivo_log, "a") as f:
                    f.write(linha)
            except OSError:
                self._arquivo_log = None      # disco cheio ou sem permissao
            self._m = {"quadros": 0, "perdidos": 0, "sem_mao": 0, "perdeu_rastreio": 0, "redeteccoes": 0,
                       "ms": 0.0, "score": 0.0, "lado": 0.0, "t": agora}

        return resultado

    def _roi_dos_marcos(self, pontos, max_lado=800.0):
        """Regenera a regiao de interesse a partir dos proprios marcos.

        Ancorada em DOIS pontos da palma — pulso (0) e base do medio (9).
        Alarga a ROI quando a mão se aproxima do limite do quadro para não cortar dedos.
        """
        pulso = pontos[0, :2]
        medio = pontos[9, :2]
        v = medio - pulso                       # vetor da palma (pulso -> base do medio)
        L = float(np.linalg.norm(v))
        if L < 1e-3:
            return self._roi

        # Convencao MediaPipe/OpenCV: angulo que alinha o vetor verticalmente para cima
        ang = math.degrees(math.atan2(v[0], -(v[1])))

        # A mao inteira se estende ~2.0L a partir do pulso; centra no meio dela (1.05 * v)
        cx = float(pulso[0] + 1.05 * v[0])
        cy = float(pulso[1] + 1.05 * v[1])

        # Alargamento adaptativo da ROI próximo à borda do quadro:
        # Se a mão se aproximar dos limites do sensor, expande o fator de margem
        # de 2.9L até 3.4L para evitar que a ponta dos dedos seja cortada da caixa
        fator_margem = 2.9
        if hasattr(self, "_w") and hasattr(self, "_h") and self._w and self._h:
            dist_bx = min(cx, self._w - cx) / self._w
            dist_by = min(cy, self._h - cy) / self._h
            dist_borda = min(dist_bx, dist_by)
            if dist_borda < 0.20:
                fator_margem += 0.5 * max(0.0, (0.20 - dist_borda) / 0.20)

        novo_lado = float(np.clip(L * fator_margem, 40.0, max_lado))

        # Suavização da caixa de rastreio:
        # Não suavizamos o centro (cx, cy) para eliminar completamente o atraso (lag)
        # durante movimentação rápida da mão (elimina cortes acidentais de dedos na borda da caixa).
        # Apenas lado e ângulo recebem atenuação suave para evitar tremor.
        if self._roi is not None:
            ant_cx, ant_cy, ant_lado, ant_ang = self._roi
            novo_lado = 0.80 * novo_lado + 0.20 * ant_lado
            diff_ang = (ang - ant_ang + 180.0) % 360.0 - 180.0
            ang = ant_ang + 0.80 * diff_ang

        return (cx, cy, novo_lado, ang)
