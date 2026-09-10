"""Traduz 21 marcos de mao em acoes de mouse e teclado com alta precisao biomecanica.

Melhorias de precisao:
  • Fusao de angulo articular com encurtamento da falange (invariante a perspectiva 2D)
  • Medicao de oposicao do polegar (distancia ponta -> base do indicador)
  • Exclusividade mutua entre W e S (o dedo mais dobrado vence, eliminando anulacao)
  • Limiares anatomicos individualizados por dedo
  • Supressao de arrasto de tendao entre medio, anelar e mindinho
"""

import math
from dataclasses import dataclass, field

from .filtros import Histerese, OneEuro, velocidade

from .biomecanica import (  # noqa: F401  reexportado por compatibilidade
    ASPECTO_PADRAO,
    DEDOS,
    PULSO,
    _angulo,
    _desfazer_aspecto,
    centro_palma,
    flexao_dedo,
    flexao_polegar,
)
from .perfis import (  # noqa: F401  reexportado por compatibilidade
    LIMIARES_INDIVIDUAIS,
    PERFIL_DIRETO,
    PERFIL_HIBRIDO,
    perfil_por_nome,
)


# Janela da mediana movel sobre a flexao. Em 5 quadros (~170 ms a 30 fps),
# um pico de ate 2 quadros e descartado sem afetar o valor de repouso.
TAMANHO_MEDIANA = 5

# Media de flexao dos quatro dedos longos para reconhecer punho fechado.
# Medido: acordes deliberados se concentram em 70-79%; punho real vai a 80-99%.
MEDIA_PUNHO = 0.82


@dataclass
class Estado:
    vel_x: float = 0.0
    vel_y: float = 0.0
    teclas: set = field(default_factory=set)
    botoes: set = field(default_factory=set)
    flexoes: dict = field(default_factory=dict)
    modo_movimento: bool = False
    palma: tuple = (0.5, 0.5)
    dx: float = 0.0
    dy: float = 0.0
    perfil: str = "DIRETO"
    eventos_novos: list = field(default_factory=list)
    punho_fechado: bool = False
    rosto_ativo: bool = False
    ear_dir: float = 0.30
    ear_esq: float = 0.30
    mar: float = 0.05
    piscadela_direita: bool = False
    piscadela_esquerda: bool = False
    boca_aberta: bool = False
    piscando_ambos: bool = False


class Mapeador:
    def __init__(
        self,
        centro=(0.5, 0.5),
        zona_morta=0.15,
        expo=2.0,
        vel_max=900.0,
        perfil_padrao="DIRETO",
        aspecto=640.0 / 480.0,
        modo_mouse="relativo",
    ):
        self.centro = centro
        # Os marcos chegam normalizados por LARGURA e ALTURA separadamente.
        # Num quadro 4:3 isso distorce angulos — e flexao E angulo. Desfazemos
        # a distorcao so para a geometria dos dedos; a mira segue no espaco
        # normalizado, que e o que o painel desenha.
        self.aspecto = aspecto
        self.zona_morta = zona_morta
        self.expo = expo
        self.vel_max = vel_max
        self.modo_mouse = modo_mouse
        self.sensibilidade_mouse = 1800.0
        self.limiar_ruido_relativo = 0.0025  # ~1.6px em 640x480: elimina tremor de repouso
        self._palma_ant = None
        self._t_palma_ant = None
        self._fx = OneEuro()
        self._fy = OneEuro()
        self.perfil_nome = perfil_padrao
        self.acoes = perfil_por_nome(perfil_padrao)

        # Histerese individualizada com ajuste dinamico global
        self.offset_sensibilidade = 0.0
        self._hist = {
            nome: Histerese(
                LIMIARES_INDIVIDUAIS[nome][0],
                LIMIARES_INDIVIDUAIS[nome][1]
            )
            for nome in DEDOS
        }

        self._estado_anterior_teclas = set()
        self._estado_anterior_botoes = set()
        self._estado_anterior_dobrado = {nome: False for nome in DEDOS}
        self._marcos_suaves = None
        self._janela_mediana = {}
        self._punho_fechado = False
        self._cont_libera_punho = 0

        # Mapeamento e estado de ações faciais (Overwatch Specials & Ultimate)
        self.acoes_rosto = {
            "olho_direito":  ("tecla", "SHIFT"),
            "olho_esquerdo": ("tecla", "E"),
            "boca":          ("tecla", "Q"),
        }
        self._estado_anterior_rosto = {
            "olho_direito": False,
            "olho_esquerdo": False,
            "boca": False,
        }

    def _mediana(self, nome, valor):
        """Mediana movel sobre a flexao, para matar pico de 1-2 quadros.

        A telemetria mostra flexao com media de 10-15% e picos de 100% nos
        cinco dedos, e 75% dos acionamentos durando 3 quadros ou menos —
        forma de ruido impulsivo, nao de dedo dobrando. Subir o limiar nao
        resolve porque o pico vai a 100% e atravessa qualquer valor.

        Mediana e o filtro certo para impulso: um pico isolado numa janela
        de 5 simplesmente nao e o elemento do meio, entao desaparece sem
        deixar residuo. Uma media, no lugar dela, espalharia o pico pelos
        quadros vizinhos em vez de remove-lo.
        """
        janela = self._janela_mediana.setdefault(nome, [])
        janela.append(valor)
        if len(janela) > TAMANHO_MEDIANA:
            janela.pop(0)
        return sorted(janela)[len(janela) // 2]

    @property
    def aciona(self):
        # Valor real do limiar ativo para o indicador
        return self._hist["indicador"].aciona

    @property
    def libera(self):
        return self._hist["indicador"].libera

    def ajustar_sensibilidade(self, delta):
        """Ajusta todos os limiares mantendo as proporcoes anatomicas."""
        self.offset_sensibilidade = max(-0.25, min(0.30, self.offset_sensibilidade + delta))
        for nome, (ac, lib) in LIMIARES_INDIVIDUAIS.items():
            self._hist[nome].aciona = max(0.20, min(0.90, ac + self.offset_sensibilidade))
            self._hist[nome].libera = max(0.12, min(0.80, lib + self.offset_sensibilidade))

    def aplicar_configuracao(self, config):
        """Aplica parâmetros individuais da configuração persistente."""
        self.config = config
        for nome in DEDOS:
            d = config.obter_dedo(nome)
            self._hist[nome].aciona = d["aciona"]
            self._hist[nome].libera = d["libera"]
            self.acoes[nome] = (d["tipo"], d["alvo"])

        r_cfg = config.dados.get("rosto", {})
        for gesto in ("olho_direito", "olho_esquerdo", "boca"):
            if gesto in r_cfg:
                self.acoes_rosto[gesto] = (r_cfg[gesto]["tipo"], r_cfg[gesto]["alvo"])

        m = config.dados.get("mouse", {})
        self.modo_mouse = m.get("modo", self.modo_mouse)
        self.sensibilidade_mouse = float(m.get("sensibilidade", self.sensibilidade_mouse))
        self.vel_max = float(m.get("vel_max", self.vel_max))
        self.zona_morta = float(m.get("zona_morta", self.zona_morta))
        self.expo = float(m.get("expo", self.expo))

    def trocar_perfil(self):
        if self.perfil_nome == "DIRETO":
            self.perfil_nome = "HIBRIDO"
            self.acoes = perfil_por_nome("HIBRIDO")
        else:
            self.perfil_nome = "DIRETO"
            self.acoes = perfil_por_nome("DIRETO")
        return self.perfil_nome

    def recentrar(self, marcos):
        self.centro = centro_palma(marcos)
        self._palma_ant = None
        self._t_palma_ant = None

    def reset(self):
        """Limpa histórico de marcos e solta todos os estados internos."""
        self._marcos_suaves = None
        self._palma_ant = None
        self._t_palma_ant = None
        self._punho_fechado = False
        self._cont_libera_punho = 0
        eventos = []
        for nome, estava in self._estado_anterior_dobrado.items():
            if estava:
                tipo, alvo = self.acoes.get(nome, ("indefinido", ""))
                eventos.append(f"{nome.upper()} [0%] -> {alvo} SOLTO")
        self._estado_anterior_dobrado = {nome: False for nome in DEDOS}

        for gesto, estava in self._estado_anterior_rosto.items():
            if estava:
                tipo, alvo = self.acoes_rosto.get(gesto, ("indefinido", ""))
                eventos.append(f"ROSTO [{gesto.upper()}] -> {alvo} SOLTO")
        self._estado_anterior_rosto = {k: False for k in self._estado_anterior_rosto}

        self._estado_anterior_teclas.clear()
        self._estado_anterior_botoes.clear()
        return eventos

    def __call__(self, marcos, t, estado_rosto=None):
        est = Estado(perfil=self.perfil_nome)

        if marcos is not None and len(marcos) > 0:
            # Suavização temporal suave dos marcos (EMA alpha=0.70) para eliminar microjitter
            if self._marcos_suaves is None or len(self._marcos_suaves) != len(marcos):
                self._marcos_suaves = [(float(x), float(y)) for x, y in marcos]
            else:
                alpha = 0.70
                self._marcos_suaves = [
                    (alpha * x + (1.0 - alpha) * sx, alpha * y + (1.0 - alpha) * sy)
                    for (x, y), (sx, sy) in zip(marcos, self._marcos_suaves)
                ]

            # Os marcos chegam normalizados por LARGURA e ALTURA separadamente,
            # o que num quadro 4:3 distorce angulos — e flexao E angulo. Aqui a
            # distorcao e desfeita so para a geometria dos dedos; a mira segue
            # usando 'marcos' cru, que e o espaco em que o painel desenha.
            marcos_geo = [(x * self.aspecto, y) for x, y in self._marcos_suaves]

            # Trava de segurança de borda: se a mão estiver muito próxima da borda (< 5%),
            # inibe novos acionamentos para evitar picos causados pelo corte do sensor
            cx_p, cy_p = centro_palma(marcos)
            na_borda = (cx_p < 0.05 or cx_p > 0.95 or cy_p < 0.05 or cy_p > 0.95)

            dobrado = {}
            for nome, idx in DEDOS.items():
                if nome == "polegar":
                    f = flexao_polegar(marcos_geo)
                else:
                    f = flexao_dedo(marcos_geo, idx)

                f = self._mediana(nome, f)
                est.flexoes[nome] = f
                dobrado[nome] = False if na_borda else self._hist[nome](f)

            # -------------------------------------------------------------
            # DETECÇÃO BIOMECÂNICA DE PUNHO FECHADO (GESTO NEUTRO / CLUTCH)
            # -------------------------------------------------------------
            # Ao fechar a mão em punho, múltiplos dedos longos se curvam juntos.
            # Isso NÃO representa intenção de comandos simultâneos (ex: W + S + E + Botões).
            dedos_longos = ("indicador", "medio", "anelar", "mindinho")
            longos_flex = [est.flexoes.get(d, 0.0) for d in dedos_longos]
            media_longos = sum(longos_flex) / 4.0
            qtd_longos_flex = sum(1 for f in longos_flex if f >= 0.55)

            # Punho fechado = mao INTEIRA fechada com FORCA, nao "varios dedos
            # dobrados". A distincao importa porque num jogo de movimento se
            # aciona varias teclas ao mesmo tempo de proposito, e o criterio
            # antigo (media >= 62% com 3 dedos, ou o atalho indicador+medio+
            # anelar) suprimia 26,8% de todos os quadros com mao: um em cada
            # quatro.
            #
            # Duas medidas nos dados reais guiaram isto:
            #
            # O mindinho NAO serve para distinguir. Em 93-96% dos quadros com
            # 3 dedos longos dobrados ele ja esta dobrado junto — e o
            # acoplamento de tendao entre anelar e mindinho, involuntario. Por
            # isso exigir os 4 nao custa deteccao de punho real, mas descarta
            # o acorde em que o mindinho ficou para tras.
            #
            # O que separa e a PROFUNDIDADE. A media dos longos se concentra em
            # 70-79% nos acordes deliberados e sobe para 80-99% no punho de
            # verdade. Em 82% o criterio cai para 6,0% dos quadros.
            #
            # O polegar foi testado como requisito e reprovado: exigi-lo levaria
            # a 0,9%, e o gesto de punho praticamente deixaria de funcionar.
            candidato_punho = media_longos >= MEDIA_PUNHO and qtd_longos_flex >= 4

            if not self._punho_fechado:
                if candidato_punho:
                    self._punho_fechado = True
                    self._cont_libera_punho = 0
                    est.eventos_novos.append("PUNHO [FECHADO] -> ACOES SUSPENSAS (NEUTRO)")
            else:
                # Histerese de saída com debounce de 3 quadros (~100ms)
                # Evita que oclusões momentâneas do MediaPipe soltem o estado de punho por 1 quadro
                if media_longos < 0.42 or qtd_longos_flex < 2:
                    self._cont_libera_punho += 1
                    if self._cont_libera_punho >= 3:
                        self._punho_fechado = False
                        self._cont_libera_punho = 0
                        est.eventos_novos.append("PUNHO [ABERTO] -> CONTROLE ATIVO")
                else:
                    self._cont_libera_punho = 0

            est.punho_fechado = self._punho_fechado

            if self._punho_fechado:
                # Supressão total de comandos: punho fechado é neutro / descanso
                for nome in DEDOS:
                    dobrado[nome] = False
            else:
                # -------------------------------------------------------------
                # REGRA BIOMECÂNICA DE EXCLUSIVIDADE MÚTUA W vs S
                # -------------------------------------------------------------
                # Se tanto o indicador (W) quanto o médio (S) atingirem o limiar,
                # o que estiver mais dobrado assume 100% da intenção, com histerese
                # de transição para evitar oscilações a cada quadro.
                if self.perfil_nome == "DIRETO":
                    f_ind = est.flexoes.get("indicador", 0.0)
                    f_med = est.flexoes.get("medio", 0.0)
                    if dobrado["indicador"] and dobrado["medio"]:
                        ind_estava = self._estado_anterior_dobrado.get("indicador", False)
                        med_estava = self._estado_anterior_dobrado.get("medio", False)
                        if ind_estava and not med_estava:
                            if f_med > (f_ind + 0.08):
                                dobrado["indicador"] = False
                            else:
                                dobrado["medio"] = False
                        elif med_estava and not ind_estava:
                            if f_ind > (f_med + 0.08):
                                dobrado["medio"] = False
                            else:
                                dobrado["indicador"] = False
                        else:
                            if f_ind >= f_med:
                                dobrado["medio"] = False
                            else:
                                dobrado["indicador"] = False

                    # Desacoplamento do anelar: quando o médio se dobra muito,
                    # o tendão do anelar sobe por inércia física. Suprimimos essa falsa ativação.
                    if f_med > 0.50 and est.flexoes.get("anelar", 0.0) < (f_med + 0.12):
                        dobrado["anelar"] = False

                    # Desacoplamento inteligente do mindinho:
                    # O mindinho só é suprimido se for arraste passivo do médio ou do anelar
                    # (quando o dedo motor principal estiver consideravelmente mais flexionado que o mindinho).
                    # Se o mindinho for acionado deliberadamente, ele NÃO é bloqueado.
                    f_ane = est.flexoes.get("anelar", 0.0)
                    f_min = est.flexoes.get("mindinho", 0.0)
                    if f_med > 0.50 and f_min < (f_med - 0.10):
                        dobrado["mindinho"] = False
                    elif f_ane > 0.50 and f_min < (f_ane - 0.08):
                        dobrado["mindinho"] = False

            # Dispara eventos de telemetria para mudanças de estado
            for nome, ligado in dobrado.items():
                if dobrado[nome] and not self._estado_anterior_dobrado[nome]:
                    tipo, alvo = self.acoes.get(nome, ("indefinido", ""))
                    est.eventos_novos.append(f"{nome.upper()} [{int(est.flexoes[nome]*100)}%] -> {alvo} LIGADO")
                elif not dobrado[nome] and self._estado_anterior_dobrado[nome]:
                    tipo, alvo = self.acoes.get(nome, ("indefinido", ""))
                    est.eventos_novos.append(f"{nome.upper()} [{int(est.flexoes[nome]*100)}%] -> {alvo} SOLTO")

            self._estado_anterior_dobrado = dobrado.copy()

            # Atribuição de ações
            for nome, ligado in dobrado.items():
                if not ligado or nome not in self.acoes:
                    continue
                tipo, alvo = self.acoes[nome]
                if tipo == "botao":
                    est.botoes.add(alvo)
                elif tipo == "tecla":
                    est.teclas.add(alvo)

            # Mira do mouse
            cx, cy = centro_palma(marcos)
            cx = self._fx(cx, t)
            cy = self._fy(cy, t)
            est.palma = (cx, cy)

            dx = max(-1.0, min(1.0, (cx - self.centro[0]) * 2.0))
            dy = max(-1.0, min(1.0, (cy - self.centro[1]) * 2.0))
            est.dx = dx
            est.dy = dy

            # Cálculo de velocidade do mouse (Modo Relativo vs Modo Joystick)
            if self._punho_fechado:
                # Embreagem de reposicionamento (Clutch): cursor parado sem solavancos
                vx, vy = 0.0, 0.0
                self._palma_ant = (cx, cy)
                self._t_palma_ant = t
            elif self.modo_mouse == "relativo":
                if self._palma_ant is None or self._t_palma_ant is None:
                    self._palma_ant = (cx, cy)
                    self._t_palma_ant = t
                    vx, vy = 0.0, 0.0
                else:
                    dt_palma = max(1e-4, t - self._t_palma_ant)
                    delta_x = cx - self._palma_ant[0]
                    delta_y = cy - self._palma_ant[1]
                    dist = math.hypot(delta_x, delta_y)

                    if dist < self.limiar_ruido_relativo:
                        # Mão parada: elimina rigorosamente qualquer deriva e ruído de sensor
                        vx, vy = 0.0, 0.0
                    else:
                        # Movimento dinâmico proporcional com aceleração balística suave
                        ganho = (dist / 0.01) ** 0.15 if dist > 0.01 else 1.0
                        vx = (delta_x / dt_palma) * self.sensibilidade_mouse * ganho
                        vy = (delta_y / dt_palma) * self.sensibilidade_mouse * ganho
                        vx = max(-self.vel_max, min(self.vel_max, vx))
                        vy = max(-self.vel_max, min(self.vel_max, vy))

                    self._palma_ant = (cx, cy)
                    self._t_palma_ant = t
            else:
                vx = velocidade(dx, self.zona_morta, self.expo, self.vel_max)
                vy = velocidade(dy, self.zona_morta, self.expo, self.vel_max)

            if self.perfil_nome == "HIBRIDO":
                est.modo_movimento = dobrado.get("medio", False)
                if est.modo_movimento:
                    if dy < -self.zona_morta:
                        est.teclas.add("W")
                    if dy > self.zona_morta:
                        est.teclas.add("S")
                    if dx < -self.zona_morta:
                        est.teclas.add("A")
                    if dx > self.zona_morta:
                        est.teclas.add("D")
                else:
                    est.vel_x = vx
                    est.vel_y = vy
            else:
                est.vel_x = vx
                est.vel_y = vy
        else:
            # Mão ausente: solta os dedos se algum estava dobrado
            for nome, estava in self._estado_anterior_dobrado.items():
                if estava:
                    tipo, alvo = self.acoes.get(nome, ("indefinido", ""))
                    est.eventos_novos.append(f"{nome.upper()} [0%] -> {alvo} SOLTO")
            self._estado_anterior_dobrado = {nome: False for nome in DEDOS}
            self._marcos_suaves = None
            self._palma_ant = None
            self._t_palma_ant = None
            self._punho_fechado = False
            self._cont_libera_punho = 0

        # Processamento biométrico de ações faciais (Overwatch Specials & Ultimate)
        if estado_rosto is not None and estado_rosto.tem_rosto:
            est.rosto_ativo = True
            est.ear_dir = estado_rosto.ear_dir
            est.ear_esq = estado_rosto.ear_esq
            est.mar = estado_rosto.mar
            est.piscadela_direita = estado_rosto.piscadela_direita
            est.piscadela_esquerda = estado_rosto.piscadela_esquerda
            est.boca_aberta = estado_rosto.boca_aberta
            est.piscando_ambos = getattr(estado_rosto, "piscando_ambos", False)

            gestos_rosto = {
                "olho_direito": estado_rosto.piscadela_direita,
                "olho_esquerdo": estado_rosto.piscadela_esquerda,
                "boca": estado_rosto.boca_aberta,
            }

            for gesto, ativo in gestos_rosto.items():
                estava = self._estado_anterior_rosto.get(gesto, False)
                tipo, alvo = self.acoes_rosto.get(gesto, ("indefinido", ""))
                if ativo and not estava:
                    est.eventos_novos.append(f"ROSTO [{gesto.upper()}] -> {alvo} LIGADO")
                elif not ativo and estava:
                    est.eventos_novos.append(f"ROSTO [{gesto.upper()}] -> {alvo} SOLTO")

                if ativo:
                    if tipo == "tecla":
                        est.teclas.add(alvo)
                    elif tipo == "botao":
                        est.botoes.add(alvo)

            self._estado_anterior_rosto = gestos_rosto
        else:
            est.rosto_ativo = False
            for gesto, estava in self._estado_anterior_rosto.items():
                if estava:
                    tipo, alvo = self.acoes_rosto.get(gesto, ("indefinido", ""))
                    est.eventos_novos.append(f"ROSTO [{gesto.upper()}] -> {alvo} SOLTO")
            self._estado_anterior_rosto = {k: False for k in self._estado_anterior_rosto}

        self._estado_anterior_teclas = est.teclas.copy()
        self._estado_anterior_botoes = est.botoes.copy()

        return est
