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
    parser.add_argument("--camera", "-c", type=int, default=0, help="Índice da câmera (padrão: 0)")
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

    def desenhar_vetor_mira(self, tela, centro_repouso, pos_palma, vel_x, vel_y, modo_movimento, zona_morta=0.15):
        cx_rep = int(centro_repouso[0] * self.w)
        cy_rep = int(centro_repouso[1] * self.h)
        raio_zm = int((zona_morta / 2.0) * min(self.w, self.h))

        cv2.circle(tela, (cx_rep, cy_rep), raio_zm, (70, 70, 90), 1, cv2.LINE_AA)
        cv2.circle(tela, (cx_rep, cy_rep), 4, (120, 120, 140), -1, cv2.LINE_AA)
        cv2.putText(tela, "REPOUSO [C]", (cx_rep - 38, cy_rep - raio_zm - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, (140, 140, 160), 1, cv2.LINE_AA)

        px = int(pos_palma[0] * self.w)
        py = int(pos_palma[1] * self.h)

        cor_vetor = (0, 215, 255) if not modo_movimento else (0, 255, 100)
        cv2.line(tela, (cx_rep, cy_rep), (px, py), cor_vetor, 2, cv2.LINE_AA)
        cv2.circle(tela, (px, py), 5, cor_vetor, -1, cv2.LINE_AA)

        mag = math.hypot(vel_x, vel_y)
        if mag > 1.0:
            cv2.putText(tela, f"{int(mag)}px/s", (px + 10, py + 4),
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

    def desenhar_painel_teclas(self, tela, teclas_ativas, botoes_ativos, modo_uinput, perfil_nome):
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

    def desenhar_central_configuracao(self, tela, config, dedo_selecionado):
        """Desenha o modal completo de configuração na tela quando TAB é pressionado."""
        overlay = tela.copy()
        cx, cy = self.w // 2, self.h // 2
        card_w, card_h = 560, 380
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

        # Linhas para cada dedo
        y_item = y1 + 75
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

        # Seção do Mouse
        y_item += 40
        cv2.line(tela, (x1 + 20, y_item - 12), (x2 - 20, y_item - 12), (70, 90, 110), 1)
        m = config.dados.get("mouse", {})
        cv2.putText(tela, f"MIRA DO MOUSE: Vel. Máxima: {int(m.get('vel_max', 900))} px/s [< / >]  |  Zona Morta: {int(m.get('zona_morta', 0.15)*100)}% [Z]",
                    (x1 + 30, y_item + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (0, 215, 255), 1, cv2.LINE_AA)

        # Rodapé de Ajuda
        y_item += 40
        cv2.rectangle(tela, (x1 + 20, y_item - 10), (x2 - 20, y_item + 25), (25, 30, 42), -1)
        cv2.putText(tela, "[1-5]: Seleciona Dedo  |  [A]: Troca Ação  |  [+/-]: Sensibilidade  |  [TAB]: Fechar",
                    (x1 + 28, y_item + 12), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 255, 255), 1, cv2.LINE_AA)


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

    telemetria = GravadorTelemetria(largura=largura, altura=altura, gravar_video=True)

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
    cv2.namedWindow(nome_janela, cv2.WINDOW_NORMAL)
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
    print("  • [A]: Cicla a tecla/ação do dedo selecionado")
    print("  • [<] / [>]: Velocidade do mouse | [Z]: Zona morta")
    print("  • [C] ou [R]: Recentralizar e re-travar rastreador na mão")
    print("  • [J]: Alternar saída uinput (Modo Jogo)")
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
            else:
                tela = np.zeros((altura, largura, 3), dtype=np.uint8)
                t_dec = t_agora - t_inicio
                fonte_sintetica.deslocamento = (0.08 * math.sin(t_dec * 1.5), 0.08 * math.cos(t_dec * 1.5))
                marcos = fonte_sintetica.marcos()

            if marcos:
                if not ja_estava_vinculado:
                    t_inicio_vinculo = t_agora
                    ja_estava_vinculado = True
                    mapeador.recentrar(marcos)
                    telemetria.registrar_evento(f"Centro do mouse auto-calibrado na postura da mao: ({mapeador.centro[0]:.2f}, {mapeador.centro[1]:.2f})", categoria="CALIB")
                tempo_vinculado = t_agora - t_inicio_vinculo

                hud.desenhar_esqueleto(tela, marcos)
                hud.desenhar_guia_biometria(tela, tem_mao=True, tempo_vinculado=tempo_vinculado)
                estado = mapeador(marcos, t_agora)

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
                    zona_morta=mapeador.zona_morta
                )

                hud.desenhar_barras_calibracao(tela, estado.flexoes, config, dedo_selecionado)
                hud.desenhar_painel_teclas(tela, estado.teclas, estado.botoes, modo_uinput, mapeador.perfil_nome)
                hud.desenhar_log_eventos(tela)

                # Telemetria ao vivo no console
                if t_agora - t_ultimo_log > 0.10:
                    t_ultimo_log = t_agora
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
                        f"POL: {pol:2d}% | MIN: {min_:2d}% | ANE: {ane:2d}% | "
                        f"TECLAS: [{t_str}] | MOUSE: [{b_str}]   "
                    )
                    sys.stdout.flush()

                if modo_uinput and saida:
                    saida.aplicar(estado, dt)
            else:
                ja_estava_vinculado = False
                telemetria.registrar_quadro(fps=1.0/dt, estado=None, aciona=mapeador.aciona)
                hud.desenhar_guia_biometria(tela, tem_mao=False, tempo_vinculado=0.0)
                hud.desenhar_painel_teclas(tela, set(), set(), modo_uinput, mapeador.perfil_nome)
                hud.desenhar_log_eventos(tela)

                # Auto-release: solta imediatamente qualquer tecla/botão no kernel e limpa histórico
                evs_reset = mapeador.reset()
                if evs_reset:
                    hud.registrar_eventos(evs_reset)
                    for ev in evs_reset:
                        telemetria.registrar_evento(ev, categoria="ACAO")

                if modo_uinput and saida:
                    saida.soltar_tudo()

            # Desenha Central de Configuração se TAB estiver ativo
            if menu_aberto:
                hud.desenhar_central_configuracao(tela, config, dedo_selecionado)

            # FPS e indicador de gravação de vídeo duplo
            fps = 1.0 / dt
            cv2.putText(tela, f"{int(fps)} FPS", (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)
            cv2.circle(tela, (115, 24), 5, (0, 0, 255), -1, cv2.LINE_AA)
            cv2.putText(tela, "REC VIDEO DUPLO", (126, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 120, 255), 1, cv2.LINE_AA)
            cv2.putText(tela, f"Gravando: telemetria/{telemetria.id_sessao} (CSV + 2x MP4)", (20, altura - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, (130, 160, 200), 1, cv2.LINE_AA)

            # Gravação contínua das duas trilhas de vídeo (limpo + anotado)
            frame_limpo = frame if camera else tela
            telemetria.gravar_quadros_video(frame_limpo, tela)

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
                novo_tipo, novo_alvo = config.ciclo_acao(dedo_selecionado)
                mapeador.aplicar_configuracao(config)
                telemetria.registrar_evento(f"{dedo_selecionado.upper()} remapeado para {novo_alvo} ({novo_tipo})", categoria="CONFIG")
                print(f"\n🎮 {dedo_selecionado.upper()} remapeado para: [{novo_alvo}] ({novo_tipo})")
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
            elif tecla in (ord(','), ord('<')):
                vel, _ = config.ajustar_mouse(delta_vel=-50)
                mapeador.aplicar_configuracao(config)
                telemetria.registrar_evento(f"Velocidade mouse: {int(vel)} px/s", categoria="CONFIG")
                print(f"\n🖱️ Velocidade Mouse: {int(vel)} px/s")
            elif tecla in (ord('.'), ord('>')):
                vel, _ = config.ajustar_mouse(delta_vel=+50)
                mapeador.aplicar_configuracao(config)
                telemetria.registrar_evento(f"Velocidade mouse: {int(vel)} px/s", categoria="CONFIG")
                print(f"\n🖱️ Velocidade Mouse: {int(vel)} px/s")
            elif tecla in (ord('z'), ord('Z')):
                delta = 0.03 if config.dados['mouse']['zona_morta'] < 0.25 else -0.15
                _, zm = config.ajustar_mouse(delta_zm=delta)
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
