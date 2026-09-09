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

PULSO = 0
DEDOS = {
    "polegar": (1, 2, 3, 4),
    "indicador": (5, 6, 7, 8),
    "medio": (9, 10, 11, 12),
    "anelar": (13, 14, 15, 16),
    "mindinho": (17, 18, 19, 20),
}

# Limiares anatomicos individualizados calibrados com base na telemetria
LIMIARES_INDIVIDUAIS = {
    "indicador": (0.48, 0.28),  # Excelente isolamento motor -> W
    "medio":     (0.50, 0.30),  # Excelente isolamento motor -> S
    "polegar":   (0.48, 0.28),  # Oposicao da ponta -> Clique Esquerdo
    "mindinho":  (0.55, 0.35),  # Leve arrasto com anelar -> Clique Direito
    "anelar":    (0.62, 0.42),  # Tendao acoplado ao medio -> E
}

# Perfil 1: Mapeamento Direto
PERFIL_DIRETO = {
    "indicador": ("tecla", "W"),
    "medio":     ("tecla", "S"),
    "polegar":   ("botao", "ESQUERDO"),
    "mindinho":  ("botao", "DIREITO"),
    "anelar":    ("tecla", "E"),
}

# Perfil 2: Modo Híbrido
PERFIL_HIBRIDO = {
    "polegar":   ("botao", "ESQUERDO"),
    "mindinho":  ("botao", "DIREITO"),
    "indicador": ("tecla", "E"),
    "anelar":    ("tecla", "F"),
    "medio":     ("modo", "MOVIMENTO"),
}


ASPECTO_PADRAO = 640.0 / 480.0


def _desfazer_aspecto(p, aspecto=ASPECTO_PADRAO):
    """Converte coordenadas normalizadas para espaço isométrico (desfaz a distorção 4:3)."""
    return (p[0] * aspecto, p[1])


def _angulo(a, b, c):
    """Angulo em graus no vertice b, entre os segmentos b->a e b->c."""
    v1 = (a[0] - b[0], a[1] - b[1])
    v2 = (c[0] - b[0], c[1] - b[1])
    n1 = math.hypot(*v1)
    n2 = math.hypot(*v2)
    if n1 < 1e-9 or n2 < 1e-9:
        return 180.0
    cos = (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


def flexao_dedo(marcos, dedo, aspecto=ASPECTO_PADRAO):
    """Calcula flexao combinando angulo articular e encurtamento em espaço isométrico."""
    mcp, pip, dip, tip = (_desfazer_aspecto(marcos[i], aspecto) for i in dedo)

    # 1. Encurtamento: razao entre distancia ponta-base e o comprimento somado dos ossos
    comp_ossos = (math.hypot(pip[0]-mcp[0], pip[1]-mcp[1]) +
                  math.hypot(dip[0]-pip[0], dip[1]-pip[1]) +
                  math.hypot(tip[0]-dip[0], tip[1]-dip[1]))
    if comp_ossos < 1e-6:
        return 0.0

    dist_direta = math.hypot(tip[0]-mcp[0], tip[1]-mcp[1])
    f_encurtamento = max(0.0, min(1.0, (1.0 - (dist_direta / comp_ossos)) / 0.60))

    # 2. Angulo medio das articulacoes interfalangicas
    ang = (_angulo(mcp, pip, dip) + _angulo(pip, dip, tip)) / 2.0
    f_angulo = max(0.0, min(1.0, (180.0 - ang) / 100.0))

    # Fusao ponderada: 60% encurtamento + 40% angulo (elimina distorcao de profundidade)
    return max(0.0, min(1.0, 0.60 * f_encurtamento + 0.40 * f_angulo))


def flexao_polegar(marcos, aspecto=ASPECTO_PADRAO):
    """Mede a flexao do polegar em direcao a base do indicador (oposicao isométrica)."""
    pulso = _desfazer_aspecto(marcos[0], aspecto)
    tip = _desfazer_aspecto(marcos[4], aspecto)
    base_indicador = _desfazer_aspecto(marcos[5], aspecto)

    dist_oposta = math.hypot(tip[0] - base_indicador[0], tip[1] - base_indicador[1])
    comp_palma = math.hypot(base_indicador[0] - pulso[0], base_indicador[1] - pulso[1])
    if comp_palma < 1e-6:
        return 0.0

    razao = dist_oposta / comp_palma
    # Aberto: razao ~ 0.85; dobrado para clique: razao ~ 0.35..0.45
    return max(0.0, min(1.0, (0.80 - razao) / 0.50))


def centro_palma(marcos):
    """Centroide do pulso com as quatro bases dos dedos."""
    idx = (PULSO,) + tuple(d[0] for d in DEDOS.values() if d[0] != 1)
    return (
        sum(marcos[i][0] for i in idx) / len(idx),
        sum(marcos[i][1] for i in idx) / len(idx),
    )


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


class Mapeador:
    def __init__(
        self,
        centro=(0.5, 0.5),
        zona_morta=0.15,
        expo=2.0,
        vel_max=900.0,
        perfil_padrao="DIRETO",
        aspecto=640.0 / 480.0,
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
        self._fx = OneEuro()
        self._fy = OneEuro()
        self.perfil_nome = perfil_padrao
        self.acoes = PERFIL_DIRETO if perfil_padrao == "DIRETO" else PERFIL_HIBRIDO

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

    @property
    def aciona(self):
        # Valor de referencia para o indicador
        return LIMIARES_INDIVIDUAIS["indicador"][0] + self.offset_sensibilidade

    @property
    def libera(self):
        return LIMIARES_INDIVIDUAIS["indicador"][1] + self.offset_sensibilidade

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

        m = config.dados.get("mouse", {})
        self.vel_max = m.get("vel_max", self.vel_max)
        self.zona_morta = m.get("zona_morta", self.zona_morta)
        self.expo = m.get("expo", self.expo)

    def trocar_perfil(self):
        if self.perfil_nome == "DIRETO":
            self.perfil_nome = "HIBRIDO"
            self.acoes = PERFIL_HIBRIDO
        else:
            self.perfil_nome = "DIRETO"
            self.acoes = PERFIL_DIRETO
        return self.perfil_nome

    def recentrar(self, marcos):
        self.centro = centro_palma(marcos)

    def __call__(self, marcos, t):
        est = Estado(perfil=self.perfil_nome)

        # Os marcos chegam normalizados por LARGURA e ALTURA separadamente,
        # o que num quadro 4:3 distorce angulos — e flexao E angulo. Aqui a
        # distorcao e desfeita so para a geometria dos dedos; a mira segue
        # usando 'marcos' cru, que e o espaco em que o painel desenha.
        marcos_geo = [(x * self.aspecto, y) for x, y in marcos]

        dobrado = {}
        for nome, idx in DEDOS.items():
            if nome == "polegar":
                f = flexao_polegar(marcos_geo)
            else:
                f = flexao_dedo(marcos_geo, idx)

            est.flexoes[nome] = f
            dobrado[nome] = self._hist[nome](f)

        # -------------------------------------------------------------
        # REGRA BIOMECÂNICA DE EXCLUSIVIDADE MÚTUA W vs S
        # -------------------------------------------------------------
        # Se tanto o indicador (W) quanto o médio (S) atingirem o limiar,
        # o que estiver mais dobrado assume 100% da intenção.
        if self.perfil_nome == "DIRETO":
            f_ind = est.flexoes.get("indicador", 0.0)
            f_med = est.flexoes.get("medio", 0.0)
            if dobrado["indicador"] and dobrado["medio"]:
                if f_ind >= f_med:
                    dobrado["medio"] = False
                else:
                    dobrado["indicador"] = False

            # Desacoplamento do anelar: quando o médio se dobra muito,
            # o tendão do anelar sobe por inércia física. Suprimimos essa falsa ativação.
            if f_med > 0.50 and est.flexoes.get("anelar", 0.0) < (f_med + 0.12):
                dobrado["anelar"] = False

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
                est.vel_x = velocidade(dx, self.zona_morta, self.expo, self.vel_max)
                est.vel_y = velocidade(dy, self.zona_morta, self.expo, self.vel_max)
        else:
            est.vel_x = velocidade(dx, self.zona_morta, self.expo, self.vel_max)
            est.vel_y = velocidade(dy, self.zona_morta, self.expo, self.vel_max)

        self._estado_anterior_teclas = est.teclas.copy()
        self._estado_anterior_botoes = est.botoes.copy()

        return est
