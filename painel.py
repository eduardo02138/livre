#!/usr/bin/env python3
"""
=============================================================================
PROJETO UMA MÃO NO AR — FASE 4: PAINEL & CENTRAL DE CONFIGURAÇÃO INTERATIVA
=============================================================================
Permite calibrar e configurar individualmente:
  • Sensibilidade de CADA dedo individual (Polegar, Indicador, Médio, Anelar, Mindinho)
  • Ação/Tecla atribuída a CADA dedo (W, S, A, D, E, F, Espaço, Shift, Cliques do Mouse)
  • Sensibilidade do Mouse (Velocidade máxima e Zona Morta)
  • Persistência automática em 'configuracao.json'

Atalhos no teclado:
  [TAB]      -> Abrir / Fechar Central de Configuração Completa
  [1] a [5]  -> Selecionar o dedo para ajustar (1=Pol, 2=Ind, 3=Med, 4=Ane, 5=Min)
  [+] ou [-] -> Aumentar / Diminuir sensibilidade do dedo selecionado
  [A]        -> Trocar tecla/ação do dedo selecionado (cicla entre opções)
  [<] ou [>] -> Ajustar velocidade máxima do mouse
  [Z]        -> Ajustar zona morta do mouse
  [C] ou [R] -> Recentralizar centro de repouso e re-travar rastreador
  [J]        -> Alternar saída uinput (Modo Jogo)
  [Q]        -> Sair
"""

import sys
import os
import time
import argparse
import math
from datetime import datetime

# Garante importação do pacote local 'livre'
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from livre.filtros import Histerese, OneEuro, velocidade
from livre.mapeamento import Mapeador, DEDOS, PULSO, centro_palma
from livre.fonte import MaoSintetica
from livre.telemetria import GravadorTelemetria
from livre.config import Configuracao, ACOES_DISPONIVEIS
from livre.rosto import RastreadorRostoONNX, EstadoRosto

try:
    import cv2
    import numpy as np
except ImportError:
    print("❌ OpenCV não encontrado!")
    print("Instale executando: sudo pacman -S --needed python-opencv python-onnxruntime-cpu")
    sys.exit(1)

CONEXOES_MAO = [
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20),
    (0, 17)
]

DEDOS_ORDEM = ["polegar", "indicador", "medio", "anelar", "mindinho"]
DEDOS_LABELS = {"polegar": "1:POL", "indicador": "2:IND", "medio": "3:MED", "anelar": "4:ANE", "mindinho": "5:MIN"}


def parse_args():
    parser = argparse.ArgumentParser(description="Painel HUD & Central de Configuração")
    parser.add_argument("--simulado", "-s", action="store_true", help="Usa gerador sintético em vez da webcam")
    parser.add_argument("--jogar", "-j", action="store_true", help="Ativa uinput imediatamente para controlar o jogo")
    parser.add_argument("--gravar-video", "--gravar", "-g", action="store_true", default=False,
                        help="Grava vídeo duplo (limpo + anotado) na pasta telemetria/")
    parser.add_argument("--camera", "-c", type=int, default=0, help="Índice da câmera (padrão: 0)")
    parser.add_argument("--sem-rosto", action="store_true", help="Desativa rastreamento de rosto e olhos")
    parser.add_argument("--largura", type=int, default=640, help="Largura do frame (padrão: 640)")
    parser.add_argument("--altura", type=int, default=480, help="Altura do frame (padrão: 480)")
    return parser.parse_args()


class HUD:
    def __init__(self, largura, altura):
        self.w = largura
        self.h = altura
        self.historico_eventos = []

    def registrar_eventos(self, eventos):
        agora = datetime.now().strftime("%H:%M:%S")
        for ev in eventos:
            cor = (0, 255, 120) if "LIGADO" in ev else (140, 140, 220)
            self.historico_eventos.append((f"[{agora}] {ev}", cor, time.time()))
        if len(self.historico_eventos) > 6:
            self.historico_eventos = self.historico_eventos[-6:]

    def desenhar_esqueleto(self, tela, marcos):
        pts = [(int(x * self.w), int(y * self.h)) for x, y in marcos]
        for p1, p2 in CONEXOES_MAO:
            cv2.line(tela, pts[p1], pts[p2], (60, 220, 255), 2, cv2.LINE_AA)
        for i, pt in enumerate(pts):
            if i in (4, 8, 12, 16, 20):
                cv2.circle(tela, pt, 7, (0, 255, 180), -1, cv2.LINE_AA)
                cv2.circle(tela, pt, 9, (255, 255, 255), 1, cv2.LINE_AA)
            elif i == 0:
                cv2.circle(tela, pt, 8, (255, 100, 50), -1, cv2.LINE_AA)
            else:
                cv2.circle(tela, pt, 4, (40, 180, 240), -1, cv2.LINE_AA)

    def desenhar_vetor_mira(self, tela, centro_repouso, pos_palma, vel_x, vel_y, modo_movimento, zona_morta=0.15, modo_mouse="relativo", punho_fechado=False):
        px = int(pos_palma[0] * self.w)
        py = int(pos_palma[1] * self.h)
        mag = math.hypot(vel_x, vel_y)

        if punho_fechado:
            cv2.circle(tela, (px, py), 7, (0, 215, 255), -1, cv2.LINE_AA)
            cv2.putText(tela, "✊ CLUTCH (EMBREAGEM)", (px + 12, py + 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.40, (0, 215, 255), 1, cv2.LINE_AA)
            return

        if modo_mouse == "joystick":
            cx_rep = int(centro_repouso[0] * self.w)
            cy_rep = int(centro_repouso[1] * self.h)
            raio_zm = int((zona_morta / 2.0) * min(self.w, self.h))

            cv2.circle(tela, (cx_rep, cy_rep), raio_zm, (70, 70, 90), 1, cv2.LINE_AA)
            cv2.circle(tela, (cx_rep, cy_rep), 4, (120, 120, 140), -1, cv2.LINE_AA)
            cv2.putText(tela, "REPOUSO [C]", (cx_rep - 38, cy_rep - raio_zm - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, (140, 140, 160), 1, cv2.LINE_AA)

            cor_vetor = (0, 215, 255) if not modo_movimento else (0, 255, 100)
            cv2.line(tela, (cx_rep, cy_rep), (px, py), cor_vetor, 2, cv2.LINE_AA)
            cv2.circle(tela, (px, py), 5, cor_vetor, -1, cv2.LINE_AA)

            if mag > 1.0:
                cv2.putText(tela, f"{int(mag)}px/s (JOYSTICK)", (px + 10, py + 4),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.40, (0, 215, 255), 1, cv2.LINE_AA)
        else:
            cor_ponto = (0, 255, 120) if mag < 1.0 else (0, 215, 255)
            cv2.circle(tela, (px, py), 5, cor_ponto, -1, cv2.LINE_AA)
            if mag < 1.0:
                cv2.putText(tela, "MOUSE PARADO (0 px/s)", (px + 10, py + 4),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.40, (0, 255, 120), 1, cv2.LINE_AA)
            else:
                cv2.putText(tela, f"{int(mag)}px/s (RELATIVO)", (px + 10, py + 4),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.40, (0, 215, 255), 1, cv2.LINE_AA)

    def desenhar_guia_biometria(self, tela, tem_mao, tempo_vinculado=0.0):
        """Desenha a moldura de centralização biométrica (estilo FaceID / enquadramento)."""
        cx = self.w // 2
        cy = int(self.h * 0.52)
        bw = 250
        bh = 300
        x1, y1 = cx - bw // 2, cy - bh // 2
        x2, y2 = cx + bw // 2, cy + bh // 2
        tamanho_canto = 35

        if not tem_mao:
            cor = (0, 215, 255)  # Amarelo/Dourado de busca
            # Cantos da moldura
            for px, py, dx, dy in [
                (x1, y1, 1, 1), (x2, y1, -1, 1),
                (x1, y2, 1, -1), (x2, y2, -1, -1)
            ]:
                cv2.line(tela, (px, py), (px + dx * tamanho_canto, py), cor, 2, cv2.LINE_AA)
                cv2.line(tela, (px, py), (px, py + dy * tamanho_canto), cor, 2, cv2.LINE_AA)

            # Ponto de mira central
            cv2.drawMarker(tela, (cx, cy), (100, 150, 180), cv2.MARKER_CROSS, 20, 1, cv2.LINE_AA)
            cv2.ellipse(tela, (cx, cy), (bw // 2 - 10, bh // 2 - 10), 0, 0, 360, (50, 80, 100), 1, cv2.LINE_AA)

            # Textos de instrução
            cv2.putText(tela, "[ ENQUADRE SUA MAO AQUI ]", (cx - 130, y1 - 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.48, (0, 255, 255), 1, cv2.LINE_AA)
            cv2.putText(tela, "Distancia ideal: 40 a 55 cm  |  Iluminacao adequada", (cx - 165, y2 + 25),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.38, (180, 180, 200), 1, cv2.LINE_AA)
        else:
            # Mão detectada! Se vinculou recentemente (< 2.5s), exibe confirmação visual
            if tempo_vinculado < 2.5:
                cor_ok = (0, 255, 120)
                for px, py, dx, dy in [
                    (x1, y1, 1, 1), (x2, y1, -1, 1),
                    (x1, y2, 1, -1), (x2, y2, -1, -1)
                ]:
                    cv2.line(tela, (px, py), (px + dx * tamanho_canto, py), cor_ok, 2, cv2.LINE_AA)
                    cv2.line(tela, (px, py), (px, py + dy * tamanho_canto), cor_ok, 2, cv2.LINE_AA)

                cv2.putText(tela, "MAO VINCULADA COM SUCESSO!", (cx - 140, y1 - 18),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.48, cor_ok, 1, cv2.LINE_AA)

            # Badge discreto permanente no topo direito
            cv2.circle(tela, (self.w - 180, 25), 5, (0, 255, 120), -1, cv2.LINE_AA)
            cv2.putText(tela, "RASTREIO ATIVO", (self.w - 168, 29),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 255, 120), 1, cv2.LINE_AA)

    def desenhar_alerta_borda(self, tela, pos_palma):
        """Exibe indicador visual quando a mão está próxima dos limites do sensor."""
        if pos_palma is None:
            return
        cx, cy = pos_palma
        margem = 0.10
        perto_esq = cx < margem
        perto_dir = cx > (1.0 - margem)
        perto_cima = cy < margem
        perto_baixo = cy > (1.0 - margem)

        if perto_esq or perto_dir or perto_cima or perto_baixo:
            cor_alerta = (0, 140, 255)  # Laranja de advertência
            if perto_esq:
                cv2.line(tela, (2, 0), (2, self.h), cor_alerta, 4)
            if perto_dir:
                cv2.line(tela, (self.w - 3, 0), (self.w - 3, self.h), cor_alerta, 4)
            if perto_cima:
                cv2.line(tela, (0, 2), (self.w, 2), cor_alerta, 4)
            if perto_baixo:
                cv2.line(tela, (0, self.h - 3), (self.w, self.h - 3), cor_alerta, 4)

            cv2.rectangle(tela, (self.w // 2 - 130, 44), (self.w // 2 + 130, 68), (20, 20, 30), -1)
            cv2.rectangle(tela, (self.w // 2 - 130, 44), (self.w // 2 + 130, 68), cor_alerta, 1)
            cv2.putText(tela, "⚠️ APROXIMANDO DA BORDA", (self.w // 2 - 110, 61),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.40, cor_alerta, 1, cv2.LINE_AA)

    def desenhar_barras_calibracao(self, tela, flexoes, config, dedo_selecionado):
        """Desenha as barras de flexão com destaque do dedo selecionado e controles."""
        x_base = 20
        y_base = self.h - 150
        largura_barra = 24
        altura_barra = 95

        # Barra de status superior dos controles
        d_cfg = config.obter_dedo(dedo_selecionado)
        txt_topo = f"DEDO SELECIONADO: [{dedo_selecionado.upper()}] | Acao: [{d_cfg['alvo']}] [A] | Limiar: {int(d_cfg['aciona']*100)}% [+/-] | [TAB] Menu"
        cv2.putText(tela, txt_topo, (x_base, y_base - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 255, 255), 1, cv2.LINE_AA)

        for i, nome in enumerate(DEDOS_ORDEM):
            bx = x_base + i * 46
            val = flexoes.get(nome, 0.0)
            pct = int(val * 100)
            cfg_d = config.obter_dedo(nome)
            aciona = cfg_d["aciona"]
            libera = cfg_d["libera"]

            # Destaca a barra do dedo atualmente selecionado para edição
            selecionado = (nome == dedo_selecionado)
            if selecionado:
                cv2.rectangle(tela, (bx - 3, y_base - 16), (bx + largura_barra + 3, y_base + altura_barra + 32),
                              (0, 255, 255), 1, cv2.LINE_AA)

            # Porcentagem numérica em cima da barra
            cor_pct = (0, 255, 120) if val >= aciona else (220, 220, 220)
            cv2.putText(tela, f"{pct}%", (bx - 1, y_base - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.32, cor_pct, 1, cv2.LINE_AA)

            # Fundo da barra
            cv2.rectangle(tela, (bx, y_base), (bx + largura_barra, y_base + altura_barra), (35, 35, 45), -1)
            cv2.rectangle(tela, (bx, y_base), (bx + largura_barra, y_base + altura_barra), (70, 70, 90), 1)

            # Preenchimento
            alt_preenchida = int(val * altura_barra)
            y_preenchida = y_base + altura_barra - alt_preenchida
            cor = (0, 230, 120) if val >= aciona else (0, 180, 240) if val >= libera else (130, 130, 150)
            cv2.rectangle(tela, (bx, y_preenchida), (bx + largura_barra, y_base + altura_barra), cor, -1)

            # Linha de acionamento do dedo específico (amarelo)
            y_aciona = int(y_base + altura_barra * (1.0 - aciona))
            y_libera = int(y_base + altura_barra * (1.0 - libera))
            cv2.line(tela, (bx - 2, y_aciona), (bx + largura_barra + 2, y_aciona), (0, 255, 255), 1)
            cv2.line(tela, (bx - 2, y_libera), (bx + largura_barra + 2, y_libera), (80, 120, 255), 1)

            # Rótulo com atalho [1-5]
            cor_lbl = (0, 255, 255) if selecionado else (190, 190, 210)
            cv2.putText(tela, DEDOS_LABELS[nome], (bx - 2, y_base + altura_barra + 14),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.32, cor_lbl, 1)

            # Ação atribuída a este dedo
            alvo = cfg_d["alvo"]
            txt_acao = "ESQ" if alvo == "ESQUERDO" else "DIR" if alvo == "DIREITO" else alvo
            cor_acao = (0, 255, 120) if val >= aciona else (0, 200, 255)
            cv2.putText(tela, txt_acao, (bx, y_base + altura_barra + 26),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, cor_acao, 1)

    def desenhar_painel_teclas(self, tela, teclas_ativas, botoes_ativos, modo_uinput, perfil_nome, punho_fechado=False):
        painel_x = self.w - 280
        painel_y = 15

        cor_uinput = (0, 255, 100) if modo_uinput else (80, 80, 180)
        txt_uinput = "SAIDA: MODO JOGO [J]" if modo_uinput else "SAIDA: VISUAL [J]"
        cv2.rectangle(tela, (painel_x, painel_y), (painel_x + 265, painel_y + 24), (20, 20, 30), -1)
        cv2.rectangle(tela, (painel_x, painel_y), (painel_x + 265, painel_y + 24), cor_uinput, 1)
        cv2.putText(tela, txt_uinput, (painel_x + 8, painel_y + 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, cor_uinput, 1, cv2.LINE_AA)

        teclas = ["W", "A", "S", "D", "E", "F"]
        tw, th = 38, 38
        ini_x = painel_x + 8
        ini_y = painel_y + 40
        b_y = ini_y + th + 8
        bw, bh = 126, 26

        if punho_fechado:
            cv2.rectangle(tela, (painel_x, ini_y), (painel_x + 265, b_y + bh), (30, 25, 15), -1)
            cv2.rectangle(tela, (painel_x, ini_y), (painel_x + 265, b_y + bh), (0, 215, 255), 2)
            cv2.putText(tela, "PUNHO FECHADO", (painel_x + 35, ini_y + 26),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.52, (0, 255, 255), 2, cv2.LINE_AA)
            cv2.putText(tela, "ACOES SUSPENSAS (REPOUSO)", (painel_x + 18, ini_y + 54),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 215, 255), 1, cv2.LINE_AA)
            return

        for i, t in enumerate(teclas):
            tx = ini_x + i * (tw + 6)
            ativa = t in teclas_ativas
            cor_bg = (0, 220, 100) if ativa else (30, 30, 40)
            cor_txt = (0, 0, 0) if ativa else (170, 170, 180)

            cv2.rectangle(tela, (tx, ini_y), (tx + tw, ini_y + th), cor_bg, -1)
            cv2.rectangle(tela, (tx, ini_y), (tx + tw, ini_y + th), (70, 70, 90), 1)
            cv2.putText(tela, t, (tx + 12, ini_y + 26), cv2.FONT_HERSHEY_SIMPLEX, 0.55, cor_txt, 2, cv2.LINE_AA)

        b_y = ini_y + th + 8
        bw, bh = 126, 26
        esq_ativo = "ESQUERDO" in botoes_ativos
        bg_esq = (0, 200, 255) if esq_ativo else (30, 30, 40)
        txt_esq = (0, 0, 0) if esq_ativo else (170, 170, 180)
        cv2.rectangle(tela, (ini_x, b_y), (ini_x + bw, b_y + bh), bg_esq, -1)
        cv2.rectangle(tela, (ini_x, b_y), (ini_x + bw, b_y + bh), (70, 70, 90), 1)
        cv2.putText(tela, "CLIQUE ESQ", (ini_x + 20, b_y + 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, txt_esq, 1 if not esq_ativo else 2, cv2.LINE_AA)

        dir_ativo = "DIREITO" in botoes_ativos
        bg_dir = (0, 200, 255) if dir_ativo else (30, 30, 40)
        txt_dir = (0, 0, 0) if dir_ativo else (170, 170, 180)
        cv2.rectangle(tela, (ini_x + bw + 12, b_y), (ini_x + bw * 2 + 12, b_y + bh), bg_dir, -1)
        cv2.rectangle(tela, (ini_x + bw + 12, b_y), (ini_x + bw * 2 + 12, b_y + bh), (70, 70, 90), 1)
        cv2.putText(tela, "CLIQUE DIR", (ini_x + bw + 32, b_y + 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, txt_dir, 1 if not dir_ativo else 2, cv2.LINE_AA)

    def desenhar_painel_rosto(self, tela, estado_rosto, acoes_rosto=None):
        """Desenha o widget de rastreamento facial e especiais de Overwatch."""
        painel_x = self.w - 280
        painel_y = 125

        tem_rosto = estado_rosto is not None and estado_rosto.tem_rosto
        cor_borda = (255, 200, 0) if tem_rosto else (70, 70, 80)

        # Fundo do cabeçalho
        cv2.rectangle(tela, (painel_x, painel_y), (painel_x + 265, painel_y + 22), (20, 20, 30), -1)
        cv2.rectangle(tela, (painel_x, painel_y), (painel_x + 265, painel_y + 22), cor_borda, 1)

        txt_hdr = "ROSTO & ESPECIAIS (OVERWATCH)" if tem_rosto else "ROSTO: PROCURANDO CAMERA..."
        cv2.putText(tela, txt_hdr, (painel_x + 8, painel_y + 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.36, (0, 255, 255) if tem_rosto else (140, 140, 150), 1, cv2.LINE_AA)

        if not tem_rosto:
            cv2.rectangle(tela, (painel_x, painel_y + 24), (painel_x + 265, painel_y + 92), (25, 25, 35), -1)
            cv2.rectangle(tela, (painel_x, painel_y + 24), (painel_x + 265, painel_y + 92), (50, 50, 60), 1)
            cv2.putText(tela, "Posicione o rosto na camera", (painel_x + 35, painel_y + 52),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.38, (130, 130, 140), 1, cv2.LINE_AA)
            cv2.putText(tela, "Shift: Pisc. Dir | E: Pisc. Esq | Q: Boca", (painel_x + 12, painel_y + 74),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.32, (100, 100, 120), 1, cv2.LINE_AA)
            return

        # Fundo do painel de itens
        cv2.rectangle(tela, (painel_x, painel_y + 24), (painel_x + 265, painel_y + 102), (25, 25, 35), -1)
        cv2.rectangle(tela, (painel_x, painel_y + 24), (painel_x + 265, painel_y + 102), (60, 70, 80), 1)

        # 1. Olho Direito -> Habilidade 1 (SHIFT)
        y1 = painel_y + 28
        h_row = 21
        pisc_dir = estado_rosto.piscadela_direita
        bg_dir = (0, 200, 100) if pisc_dir else (35, 38, 48)
        txt_dir_cor = (0, 0, 0) if pisc_dir else (220, 220, 230)
        cv2.rectangle(tela, (painel_x + 6, y1), (painel_x + 259, y1 + h_row), bg_dir, -1)
        cv2.rectangle(tela, (painel_x + 6, y1), (painel_x + 259, y1 + h_row), (0, 255, 150) if pisc_dir else (70, 75, 85), 1)
        txt_od = "PISC. DIR -> [SHIFT] HAB. 1" + (" *" if pisc_dir else "")
        cv2.putText(tela, txt_od, (painel_x + 12, y1 + 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, txt_dir_cor, 1 if not pisc_dir else 2, cv2.LINE_AA)
        cv2.putText(tela, f"{estado_rosto.ear_dir:.2f}", (painel_x + 225, y1 + 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.32, (0, 0, 0) if pisc_dir else (140, 160, 180), 1, cv2.LINE_AA)

        # 2. Olho Esquerdo -> Habilidade 2 (E)
        y2 = y1 + h_row + 4
        pisc_esq = estado_rosto.piscadela_esquerda
        bg_esq = (0, 200, 100) if pisc_esq else (35, 38, 48)
        txt_esq_cor = (0, 0, 0) if pisc_esq else (220, 220, 230)
        cv2.rectangle(tela, (painel_x + 6, y2), (painel_x + 259, y2 + h_row), bg_esq, -1)
        cv2.rectangle(tela, (painel_x + 6, y2), (painel_x + 259, y2 + h_row), (0, 255, 150) if pisc_esq else (70, 75, 85), 1)
        txt_oe = "PISC. ESQ -> [E] HAB. 2" + (" *" if pisc_esq else "")
        cv2.putText(tela, txt_oe, (painel_x + 12, y2 + 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, txt_esq_cor, 1 if not pisc_esq else 2, cv2.LINE_AA)
        cv2.putText(tela, f"{estado_rosto.ear_esq:.2f}", (painel_x + 225, y2 + 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.32, (0, 0, 0) if pisc_esq else (140, 160, 180), 1, cv2.LINE_AA)

        # 3. Boca -> Ultimate (Q)
        y3 = y2 + h_row + 4
        boca_ab = estado_rosto.boca_aberta
        bg_boca = (0, 140, 255) if boca_ab else (35, 38, 48)
        txt_boca_cor = (0, 0, 0) if boca_ab else (220, 220, 230)
        cv2.rectangle(tela, (painel_x + 6, y3), (painel_x + 259, y3 + h_row), bg_boca, -1)
        cv2.rectangle(tela, (painel_x + 6, y3), (painel_x + 259, y3 + h_row), (0, 215, 255) if boca_ab else (70, 75, 85), 1)
        txt_bc = "ABRIR BOCA -> [Q] ULTIMATE" + (" *SUPREMA!*" if boca_ab else "")
        cv2.putText(tela, txt_bc, (painel_x + 12, y3 + 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, txt_boca_cor, 1 if not boca_ab else 2, cv2.LINE_AA)
        cv2.putText(tela, f"{estado_rosto.mar:.2f}", (painel_x + 225, y3 + 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.32, (0, 0, 0) if boca_ab else (140, 160, 180), 1, cv2.LINE_AA)

        # Indicador de piscada natural ignorada
        if getattr(estado_rosto, "piscando_ambos", False):
            cv2.rectangle(tela, (painel_x, painel_y + 105), (painel_x + 265, painel_y + 123), (20, 30, 45), -1)
            cv2.putText(tela, "[Piscada Bilateral: Ignorada]", (painel_x + 42, painel_y + 118),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.33, (0, 255, 255), 1, cv2.LINE_AA)

    def desenhar_rosto_marcos(self, tela, estado_rosto):
        """Desenha pontos discretos nos olhos e boca sobre a imagem da câmera."""
        if estado_rosto is None or not estado_rosto.tem_rosto:
            return

        cor_od = (0, 255, 255) if estado_rosto.piscadela_direita else (0, 220, 120)
        for p in estado_rosto.marcos_olho_dir:
            cv2.circle(tela, (int(p[0]), int(p[1])), 2, cor_od, -1)

        cor_oe = (0, 255, 255) if estado_rosto.piscadela_esquerda else (0, 220, 120)
        for p in estado_rosto.marcos_olho_esq:
            cv2.circle(tela, (int(p[0]), int(p[1])), 2, cor_oe, -1)

        cor_bc = (0, 100, 255) if estado_rosto.boca_aberta else (160, 160, 170)
        for p in estado_rosto.marcos_boca:
            cv2.circle(tela, (int(p[0]), int(p[1])), 2, cor_bc, -1)

    def desenhar_log_eventos(self, tela):
        x = 20
        y = 65
        agora = time.time()
        cv2.putText(tela, "LOG DE ACOES RECENTES:", (x, y - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (180, 180, 200), 1, cv2.LINE_AA)

        for i, (msg, cor, t_ev) in enumerate(reversed(self.historico_eventos[-4:])):
            idade = agora - t_ev
            alfa_cor = cor if idade < 3.0 else (120, 120, 130)
            cv2.putText(tela, f"• {msg}", (x, y + 12 + i * 16),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.38, alfa_cor, 1, cv2.LINE_AA)

    def desenhar_central_configuracao(self, tela, config, dedo_selecionado, perfil_nome="DIRETO"):
        """Desenha o modal completo de configuração na tela quando TAB é pressionado."""
        overlay = tela.copy()
        cx, cy = self.w // 2, self.h // 2
        card_w, card_h = 560, 470
        x1 = cx - card_w // 2
        y1 = cy - card_h // 2
        x2 = x1 + card_w
        y2 = y1 + card_h

        # Fundo escuro translúcido
        cv2.rectangle(overlay, (x1, y1), (x2, y2), (18, 20, 28), -1)
        cv2.addWeighted(overlay, 0.92, tela, 0.08, 0, tela)
        cv2.rectangle(tela, (x1, y1), (x2, y2), (0, 255, 255), 2)

        # Cabeçalho
        cv2.putText(tela, "⚙️  CENTRAL DE CONFIGURAÇÃO — UMA MÃO NO AR", (x1 + 25, y1 + 32),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 2, cv2.LINE_AA)
        cv2.line(tela, (x1 + 20, y1 + 45), (x2 - 20, y1 + 45), (70, 90, 110), 1)

        # Faixa do perfil ativo, com o ciclo inteiro visivel
        from livre.perfis import PERFIS
        y_perf = y1 + 68
        cv2.rectangle(tela, (x1 + 20, y_perf - 16), (x2 - 20, y_perf + 8), (30, 40, 58), -1)
        cv2.putText(tela, "PERFIL [P]:", (x1 + 30, y_perf),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (180, 200, 220), 1, cv2.LINE_AA)
        x_nome = x1 + 130
        for nome in PERFIS:
            ativo = (nome == perfil_nome)
            cor = (0, 255, 255) if ativo else (110, 125, 145)
            largura = len(nome) * 9 + 12
            if ativo:
                cv2.rectangle(tela, (x_nome - 5, y_perf - 14), (x_nome + largura - 8, y_perf + 6),
                              (0, 90, 110), -1)
            cv2.putText(tela, nome, (x_nome, y_perf),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.40, cor, 2 if ativo else 1, cv2.LINE_AA)
            x_nome += largura

        # Linhas para cada dedo
        y_item = y1 + 105
        cv2.putText(tela, "DEDO          AÇÃO ATRIBUÍDA       LIMIAR ACIONA   SOLTA", (x1 + 30, y_item),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (180, 200, 220), 1, cv2.LINE_AA)

        for i, nome in enumerate(DEDOS_ORDEM):
            y_item += 30
            d = config.obter_dedo(nome)
            selecionado = (nome == dedo_selecionado)

            if selecionado:
                cv2.rectangle(tela, (x1 + 20, y_item - 18), (x2 - 20, y_item + 8), (40, 55, 80), -1)
                cv2.rectangle(tela, (x1 + 20, y_item - 18), (x2 - 20, y_item + 8), (0, 255, 255), 1)

            cor_txt = (0, 255, 255) if selecionado else (220, 220, 220)
            cv2.putText(tela, f"[{i+1}] {nome.capitalize():<10}", (x1 + 30, y_item),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, cor_txt, 1 if not selecionado else 2, cv2.LINE_AA)

            txt_acao = f"{d['alvo']} ({d['tipo']})"
            cv2.putText(tela, f"{txt_acao:<18}", (x1 + 150, y_item),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 230, 120), 1 if not selecionado else 2, cv2.LINE_AA)

            cv2.putText(tela, f"{int(d['aciona']*100):3d}%", (x1 + 350, y_item),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 100), 1, cv2.LINE_AA)
            cv2.putText(tela, f"{int(d['libera']*100):3d}%", (x1 + 450, y_item),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, (100, 180, 255), 1, cv2.LINE_AA)

        # Seção do Rosto — muda junto com o perfil
        y_item += 34
        cv2.line(tela, (x1 + 20, y_item - 12), (x2 - 20, y_item - 12), (70, 90, 110), 1)
        r = config.dados.get("rosto", {})
        rotulos = {
            "olho_direito":  "piscadela dir",
            "olho_esquerdo": "piscadela esq",
            "piscada_longa": "2 olhos 400ms",
            "boca":          "boca aberta",
        }
        partes = []
        for gesto, rotulo in rotulos.items():
            if isinstance(r.get(gesto), dict):
                partes.append(f"{rotulo}->{r[gesto]['alvo']}")
        txt_rosto = "ROSTO: " + ("  |  ".join(partes) if partes else "sem gestos")
        cv2.putText(tela, txt_rosto, (x1 + 30, y_item + 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.36, (200, 160, 255), 1, cv2.LINE_AA)

        # Seção do Mouse
        y_item += 34
        cv2.line(tela, (x1 + 20, y_item - 12), (x2 - 20, y_item - 12), (70, 90, 110), 1)
        m = config.dados.get("mouse", {})
        modo_m = m.get("modo", "relativo").upper()
        if modo_m == "RELATIVO":
            txt_mouse = f"MOUSE: MODO [{modo_m}] [M]  |  Sensibilidade: {int(m.get('sensibilidade', 1800))} [< / >]"
        else:
            txt_mouse = f"MOUSE: MODO [{modo_m}] [M]  |  Vel. Max: {int(m.get('vel_max', 900))} px/s [< / >]  |  ZM: {int(m.get('zona_morta', 0.20)*100)}% [Z]"
        cv2.putText(tela, txt_mouse, (x1 + 30, y_item + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (0, 215, 255), 1, cv2.LINE_AA)

        # Rodapé de Ajuda
        y_item += 40
        cv2.rectangle(tela, (x1 + 20, y_item - 10), (x2 - 20, y_item + 25), (25, 30, 42), -1)
        cv2.putText(tela, "[1-5]: Dedo | [A]: Acao | [+/-]: Sens. | [P]: Perfil | [M]: Modo Mouse | [TAB]: Fechar",
                    (x1 + 24, y_item + 12), cv2.FONT_HERSHEY_SIMPLEX, 0.36, (0, 255, 255), 1, cv2.LINE_AA)


def main():
    args = parse_args()

    # Carrega a configuração persistente (configuracao.json)
    config = Configuracao()

    camera = None
    fonte_sintetica = None

    if not args.simulado:
        try:
            from livre.camera import CameraMao
            camera = CameraMao(indice_camera=args.camera, largura=args.largura, altura=args.altura)
        except Exception as e:
            print(f"⚠️  Aviso ao abrir câmera: {e}")
            print("🔄 Iniciando em modo SINTÉTICO...")
            fonte_sintetica = MaoSintetica()
    else:
        fonte_sintetica = MaoSintetica()

    largura = camera.largura if camera else 640
    altura = camera.altura if camera else 480

    hud = HUD(largura, altura)
    mapeador = Mapeador()
    mapeador.aplicar_configuracao(config)

    rastreador_rosto = None
    if not args.simulado and not getattr(args, "sem_rosto", False):
        try:
            rastreador_rosto = RastreadorRostoONNX()
            print("👁️  Rastreador Facial ONNX carregado com sucesso (Overwatch: Shift, E, Q)")
        except Exception as e:
            print(f"⚠️  Rastreamento facial desativado: {e}")

    telemetria = GravadorTelemetria(largura=largura, altura=altura, gravar_video=args.gravar_video)

    saida = None
    modo_uinput = args.jogar
    if modo_uinput:
        try:
            from livre.saida import Saida
            saida = Saida()
            print("✅ Saída uinput iniciada!")
        except Exception as e:
            print(f"❌ Erro ao inicializar uinput: {e}")
            modo_uinput = False

    nome_janela = "Uma Mao no Ar — Painel & Central de Controle"
    cv2.namedWindow(nome_janela, cv2.WINDOW_NORMAL | cv2.WINDOW_GUI_NORMAL)
    cv2.resizeWindow(nome_janela, 960, int(960 * altura / largura))

    t_ant = time.time()
    t_inicio = time.time()
    t_ultimo_log = time.time()

    dedo_selecionado = "indicador"
    menu_aberto = False

    print("\n" + "=" * 70)
    print("  ✋ CENTRAL DE CONTROLE & CALIBRAÇÃO ATIVA")
    print("  • Pressione [TAB] para abrir a Central de Configuração na tela")
    print("  • [1] a [5]: Seleciona o dedo para calibrar")
    print("  • [+] / [-]: Altera sensibilidade do dedo selecionado")
    print("  • [A]: Cicla a tecla/ação do dedo selecionado (com [TAB] aberto)")
    print("  • [P]: Trocar perfil da mao e do rosto (DIRETO / MEU / OVERWATCH / HIBRIDO)")
    print("  • [M]: Alternar modo do mouse (Relativo vs Joystick)")
    print("  • [<] / [>]: Sensibilidade / Velocidade do mouse | [Z]: Zona morta")
    print("  • [C] ou [R]: Recentralizar e re-travar rastreador na mão")
    print("  • [J]: Alternar saída uinput (Modo Jogo)")
    print("  • 👁️ Rosto (Overwatch): Pisc. Dir = SHIFT | Pisc. Esq = E | Boca = Q")
    print("  • [Q]: Sair")
    print("=" * 70 + "\n")

    t_inicio_vinculo = 0.0
    ja_estava_vinculado = False

    try:
        while True:
            t_agora = time.time()
            dt = max(1e-4, t_agora - t_ant)
            t_ant = t_agora

            if camera:
                frame, marcos = camera.ler()
                if frame is None:
                    continue
                tela = frame.copy()
                estado_rosto = rastreador_rosto.estimar(frame) if rastreador_rosto else None
            else:
                tela = np.zeros((altura, largura, 3), dtype=np.uint8)
                t_dec = t_agora - t_inicio
                fonte_sintetica.deslocamento = (0.08 * math.sin(t_dec * 1.5), 0.08 * math.cos(t_dec * 1.5))
                marcos = fonte_sintetica.marcos()
                estado_rosto = None

            if marcos:
                cx_p, cy_p = centro_palma(marcos)
                # Só vincula pela primeira vez se a mão estiver na região central segura
                pode_vincular = (0.18 <= cx_p <= 0.82 and 0.15 <= cy_p <= 0.85) if not ja_estava_vinculado else True
                if not pode_vincular:
                    hud.desenhar_esqueleto(tela, marcos)
                    hud.desenhar_guia_biometria(tela, tem_mao=False, tempo_vinculado=0.0)
                    cv2.putText(tela, "POSICIONE A MAO NO CENTRO PARA VINCULAR", (largura // 2 - 200, 45),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.46, (0, 255, 255), 1, cv2.LINE_AA)
                else:
                    if not ja_estava_vinculado:
                        t_inicio_vinculo = t_agora
                        ja_estava_vinculado = True
                        mapeador.recentrar(marcos)
                        telemetria.registrar_evento(f"Centro do mouse auto-calibrado na postura da mao: ({mapeador.centro[0]:.2f}, {mapeador.centro[1]:.2f})", categoria="CALIB")
                    tempo_vinculado = t_agora - t_inicio_vinculo

                    hud.desenhar_esqueleto(tela, marcos)
                    hud.desenhar_guia_biometria(tela, tem_mao=True, tempo_vinculado=tempo_vinculado)
                    estado = mapeador(marcos, t_agora, estado_rosto=estado_rosto)

                    if estado.eventos_novos:
                        hud.registrar_eventos(estado.eventos_novos)
                        for ev in estado.eventos_novos:
                            telemetria.registrar_evento(ev, categoria="ACAO")

                    telemetria.registrar_quadro(fps=1.0/dt, estado=estado, aciona=mapeador.aciona)

                    hud.desenhar_vetor_mira(
                        tela,
                        centro_repouso=mapeador.centro,
                        pos_palma=estado.palma,
                        vel_x=estado.vel_x,
                        vel_y=estado.vel_y,
                        modo_movimento=estado.modo_movimento,
                        zona_morta=mapeador.zona_morta,
                        modo_mouse=mapeador.modo_mouse,
                        punho_fechado=estado.punho_fechado,
                    )
                    hud.desenhar_alerta_borda(tela, estado.palma)

                hud.desenhar_barras_calibracao(tela, estado.flexoes, config, dedo_selecionado)
                hud.desenhar_painel_teclas(tela, estado.teclas, estado.botoes, modo_uinput, mapeador.perfil_nome, punho_fechado=estado.punho_fechado)
                hud.desenhar_painel_rosto(tela, estado_rosto, mapeador.acoes_rosto)
                hud.desenhar_rosto_marcos(tela, estado_rosto)
                hud.desenhar_log_eventos(tela)

                # Telemetria ao vivo no console
                if t_agora - t_ultimo_log > 0.10:
                    t_ultimo_log = t_agora
                    rosto_tag = ""
                    if estado.rosto_ativo:
                        partes = []
                        if estado.piscadela_direita:
                            partes.append("👁️DIR:SHIFT")
                        if estado.piscadela_esquerda:
                            partes.append("👁️ESQ:E")
                        if estado.boca_aberta:
                            partes.append("👄BOCA:Q")
                        rosto_tag = " | " + " ".join(partes) if partes else " | 👁️ROSTO:OK"

                    if estado.punho_fechado:
                        sys.stdout.write(
                            f"\r📊 FPS: {int(1.0/dt):2d} | ✊ PUNHO FECHADO [NEUTRO / REPOUSO]{rosto_tag}                "
                        )
                    else:
                        pol = int(estado.flexoes.get('polegar', 0) * 100)
                        ind = int(estado.flexoes.get('indicador', 0) * 100)
                        med = int(estado.flexoes.get('medio', 0) * 100)
                        ane = int(estado.flexoes.get('anelar', 0) * 100)
                        min_ = int(estado.flexoes.get('mindinho', 0) * 100)

                        t_str = ",".join(sorted(estado.teclas)) if estado.teclas else "-"
                        b_str = ",".join(sorted(estado.botoes)) if estado.botoes else "-"
                        sys.stdout.write(
                            f"\r📊 FPS: {int(1.0/dt):2d} | IND: {ind:2d}% ({'W' if 'W' in estado.teclas else ' '}) | "
                            f"MED: {med:2d}% ({'S' if 'S' in estado.teclas else ' '}) | "
                            f"POL: {pol:2d}% | MIN: {min_:2d}% | ANE: {ane:2d}%{rosto_tag} | "
                            f"TECLAS: [{t_str}] | MOUSE: [{b_str}]   "
                        )
                    sys.stdout.flush()

                if modo_uinput and saida:
                    saida.aplicar(estado, dt)
            else:
                ja_estava_vinculado = False
                estado = mapeador(None, t_agora, estado_rosto=estado_rosto)
                if estado.eventos_novos:
                    hud.registrar_eventos(estado.eventos_novos)
                    for ev in estado.eventos_novos:
                        telemetria.registrar_evento(ev, categoria="ACAO")

                telemetria.registrar_quadro(fps=1.0/dt, estado=estado, aciona=mapeador.aciona)
                hud.desenhar_guia_biometria(tela, tem_mao=False, tempo_vinculado=0.0)
                hud.desenhar_painel_teclas(tela, estado.teclas, estado.botoes, modo_uinput, mapeador.perfil_nome)
                hud.desenhar_painel_rosto(tela, estado_rosto, mapeador.acoes_rosto)
                hud.desenhar_rosto_marcos(tela, estado_rosto)
                hud.desenhar_log_eventos(tela)

                # Telemetria ao vivo no console quando a mão não está no quadro
                if t_agora - t_ultimo_log > 0.10:
                    t_ultimo_log = t_agora
                    rosto_tag = ""
                    if estado.rosto_ativo:
                        partes = []
                        if estado.piscadela_direita:
                            partes.append("👁️DIR:SHIFT")
                        if estado.piscadela_esquerda:
                            partes.append("👁️ESQ:E")
                        if estado.boca_aberta:
                            partes.append("👄BOCA:Q")
                        rosto_tag = " | " + " ".join(partes) if partes else " | 👁️ROSTO:OK"
                    t_str = ",".join(sorted(estado.teclas)) if estado.teclas else "-"
                    sys.stdout.write(
                        f"\r📊 FPS: {int(1.0/dt):2d} | ✋ MAO: AUSENTE{rosto_tag} | TECLAS: [{t_str}]                    "
                    )
                    sys.stdout.flush()

                if modo_uinput and saida:
                    saida.aplicar(estado, dt)

            # Desenha Central de Configuração se TAB estiver ativo
            if menu_aberto:
                hud.desenhar_central_configuracao(tela, config, dedo_selecionado, mapeador.perfil_nome)

            # FPS e indicador de gravação
            fps = 1.0 / dt
            cv2.putText(tela, f"{int(fps)} FPS", (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)
            if telemetria.gravar_video:
                cv2.circle(tela, (115, 24), 5, (0, 0, 255), -1, cv2.LINE_AA)
                cv2.putText(tela, "REC VIDEO DUPLO", (126, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 120, 255), 1, cv2.LINE_AA)
                cv2.putText(tela, f"Gravando: telemetria/{telemetria.id_sessao} (CSV + 2x MP4)", (20, altura - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.35, (130, 160, 200), 1, cv2.LINE_AA)
                frame_limpo = frame if camera else tela
                telemetria.gravar_quadros_video(frame_limpo, tela)
            else:
                cv2.putText(tela, f"Telemetria: telemetria/{telemetria.id_sessao} (CSV + LOG)", (20, altura - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.35, (130, 160, 200), 1, cv2.LINE_AA)

            cv2.imshow(nome_janela, tela)
            tecla = cv2.waitKey(1) & 0xFF

            if tecla in (ord('q'), ord('Q'), 27):
                break
            elif tecla == 9:  # Tecla TAB
                menu_aberto = not menu_aberto
                print(f"\n⚙️ Central de Configuração: {'ABERTA' if menu_aberto else 'FECHADA'}")
            elif tecla == ord('1'):
                dedo_selecionado = "polegar"
                print(f"\n👉 Dedo selecionado: POLEGAR")
            elif tecla == ord('2'):
                dedo_selecionado = "indicador"
                print(f"\n👉 Dedo selecionado: INDICADOR")
            elif tecla == ord('3'):
                dedo_selecionado = "medio"
                print(f"\n👉 Dedo selecionado: MÉDIO")
            elif tecla == ord('4'):
                dedo_selecionado = "anelar"
                print(f"\n👉 Dedo selecionado: ANELAR")
            elif tecla == ord('5'):
                dedo_selecionado = "mindinho"
                print(f"\n👉 Dedo selecionado: MINDINHO")
            elif tecla in (ord('a'), ord('A')):
                if menu_aberto:
                    novo_tipo, novo_alvo = config.ciclo_acao(dedo_selecionado)
                    mapeador.aplicar_configuracao(config)
                    telemetria.registrar_evento(f"{dedo_selecionado.upper()} remapeado para {novo_alvo} ({novo_tipo})", categoria="CONFIG")
                    print(f"\n🎮 {dedo_selecionado.upper()} remapeado para: [{novo_alvo}] ({novo_tipo})")
                else:
                    print(f"\n⚠️  Para remapear ações com [A], abra primeiro a central com [TAB].")
            elif tecla in (ord('+'), ord('=')):
                ac, lib = config.ajustar_limiar(dedo_selecionado, -0.03)
                mapeador.aplicar_configuracao(config)
                telemetria.registrar_evento(f"{dedo_selecionado.upper()} sensibilidade aumentada: {int(ac*100)}%", categoria="CONFIG")
                print(f"\n⬆️ {dedo_selecionado.upper()} sensibilidade AUMENTADA: aciona={int(ac*100)}% libera={int(lib*100)}%")
            elif tecla in (ord('-'), ord('_')):
                ac, lib = config.ajustar_limiar(dedo_selecionado, +0.03)
                mapeador.aplicar_configuracao(config)
                telemetria.registrar_evento(f"{dedo_selecionado.upper()} sensibilidade diminuida: {int(ac*100)}%", categoria="CONFIG")
                print(f"\n⬇️ {dedo_selecionado.upper()} sensibilidade DIMINUÍDA: aciona={int(ac*100)}% libera={int(lib*100)}%")
            elif tecla in (ord('p'), ord('P')):
                # Troca o perfil inteiro: dedos E gestos de rosto.
                # Passa pelo config para o menu da tela refletir a mudanca,
                # e so depois reaplica no mapeador — aplicar_configuracao le
                # do config, entao a ordem importa.
                novo_perfil = mapeador.trocar_perfil()
                config.aplicar_perfil(novo_perfil)
                mapeador.aplicar_configuracao(config)
                if saida:
                    saida.soltar_tudo()          # nada preso do perfil anterior
                mapeador.reset()
                telemetria.registrar_evento(f"Perfil trocado para: {novo_perfil}", categoria="CONFIG")
                print(f"\n🖐️ Perfil da mao: [{novo_perfil}]")
            elif tecla in (ord('m'), ord('M')):
                novo_modo = config.alternar_modo_mouse()
                mapeador.aplicar_configuracao(config)
                telemetria.registrar_evento(f"Modo do mouse alternado para: {novo_modo.upper()}", categoria="MODO")
                print(f"\n🖱️ Modo do Mouse alterado para: [{novo_modo.upper()}]")
            elif tecla in (ord(','), ord('<')):
                if mapeador.modo_mouse == "relativo":
                    _, _, sens = config.ajustar_mouse(delta_sens=-100)
                    mapeador.aplicar_configuracao(config)
                    telemetria.registrar_evento(f"Sensibilidade mouse: {int(sens)}", categoria="CONFIG")
                    print(f"\n🖱️ Sensibilidade Mouse: {int(sens)}")
                else:
                    vel, _, _ = config.ajustar_mouse(delta_vel=-50)
                    mapeador.aplicar_configuracao(config)
                    telemetria.registrar_evento(f"Velocidade mouse: {int(vel)} px/s", categoria="CONFIG")
                    print(f"\n🖱️ Velocidade Mouse: {int(vel)} px/s")
            elif tecla in (ord('.'), ord('>')):
                if mapeador.modo_mouse == "relativo":
                    _, _, sens = config.ajustar_mouse(delta_sens=+100)
                    mapeador.aplicar_configuracao(config)
                    telemetria.registrar_evento(f"Sensibilidade mouse: {int(sens)}", categoria="CONFIG")
                    print(f"\n🖱️ Sensibilidade Mouse: {int(sens)}")
                else:
                    vel, _, _ = config.ajustar_mouse(delta_vel=+50)
                    mapeador.aplicar_configuracao(config)
                    telemetria.registrar_evento(f"Velocidade mouse: {int(vel)} px/s", categoria="CONFIG")
                    print(f"\n🖱️ Velocidade Mouse: {int(vel)} px/s")
            elif tecla in (ord('z'), ord('Z')):
                delta = 0.03 if config.dados.get('mouse', {}).get('zona_morta', 0.20) < 0.25 else -0.15
                _, zm, _ = config.ajustar_mouse(delta_zm=delta)
                mapeador.aplicar_configuracao(config)
                telemetria.registrar_evento(f"Zona morta: {int(zm*100)}%", categoria="CONFIG")
                print(f"\n🎯 Zona morta mouse: {int(zm*100)}%")
            elif tecla in (ord('c'), ord('C'), ord('r'), ord('R')):
                if marcos:
                    mapeador.recentrar(marcos)
                if camera and hasattr(camera, 'tracker_onnx') and camera.tracker_onnx:
                    camera.tracker_onnx._roi = None
                telemetria.registrar_evento("Centro de repouso atualizado e rastreador resetado", categoria="CALIB")
                print("\n🎯 Centro de repouso atualizado e rastreador resetado!")
            elif tecla in (ord('j'), ord('J')):
                modo_uinput = not modo_uinput
                if modo_uinput and saida is None:
                    try:
                        from livre.saida import Saida
                        saida = Saida()
                        telemetria.registrar_evento("Modo Jogo ATIVADO (uinput)", categoria="MODO")
                        print("\n🎮 Modo Jogo ATIVADO (uinput ativo)")
                    except Exception as e:
                        print(f"\n❌ Erro ao ativar uinput: {e}")
                        modo_uinput = False
                elif not modo_uinput and saida:
                    saida.soltar_tudo()
                    telemetria.registrar_evento("Modo Apenas Visual ATIVADO", categoria="MODO")
                    print("\n👁️ Modo Apenas Visual ATIVADO (uinput pausado)")

    finally:
        print("\nFinalizando e salvando telemetria...")
        telemetria.fechar()
        if camera:
            camera.fechar()
        if saida:
            saida.fechar()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
