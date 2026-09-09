#!/usr/bin/env python3
"""Mede onde vai o tempo de cada quadro, sem HUD e sem uinput.

O log do rastreador diz que a inferencia custa ~4 ms, mas o laco do painel
roda a 10 fps (100 ms). Este script separa as etapas para achar os outros
~96 ms. Feche o painel antes: a camera so aceita um dono.

    python3 perfilar.py [n_quadros]
"""

import statistics
import sys
import time

import cv2

from livre.rastreador_onnx import RastreadorONNX


def medir(n=120):
    cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
    if not cap.isOpened():
        sys.exit("nao consegui abrir /dev/video0 — o painel ainda esta rodando?")

    _fourcc = getattr(cv2, "VideoWriter_fourcc", None) or cv2.VideoWriter.fourcc
    cap.set(cv2.CAP_PROP_FOURCC, _fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FPS, 30)

    rast = RastreadorONNX()
    tempos = {"leitura": [], "espelho": [], "rastreio": [], "total": []}
    achou = 0

    print(f"medindo {n} quadros — mantenha a mao na frente da camera\n")
    for i in range(n):
        t0 = time.perf_counter()
        ok, quadro = cap.read()
        t1 = time.perf_counter()
        if not ok:
            continue
        quadro = cv2.flip(quadro, 1)
        t2 = time.perf_counter()
        marcos = rast.estimar_marcos(quadro)
        t3 = time.perf_counter()

        if marcos:
            achou += 1
        tempos["leitura"].append((t1 - t0) * 1000)
        tempos["espelho"].append((t2 - t1) * 1000)
        tempos["rastreio"].append((t3 - t2) * 1000)
        tempos["total"].append((t3 - t0) * 1000)

        if (i + 1) % 30 == 0:
            print(f"  {i + 1}/{n}...")

    cap.release()

    print(f"\n{'etapa':<12} {'mediana':>9} {'p95':>9} {'max':>9}")
    print("-" * 42)
    for nome in ("leitura", "espelho", "rastreio", "total"):
        v = sorted(tempos[nome])
        if not v:
            continue
        p95 = v[int(len(v) * 0.95) - 1]
        print(f"{nome:<12} {statistics.median(v):7.1f}ms {p95:7.1f}ms {max(v):7.1f}ms")

    med = statistics.median(tempos["total"])
    print(f"\nteto do laco sem HUD: {1000 / med:.1f} fps")
    print(f"mao encontrada em {achou}/{len(tempos['total'])} quadros")
    print("\nse 'leitura' dominar, o gargalo e a captura (buffer/USB), nao o modelo.")
    print("se o teto aqui for ~30 fps, o custo esta no desenho do HUD.")


if __name__ == "__main__":
    medir(int(sys.argv[1]) if len(sys.argv) > 1 else 120)
