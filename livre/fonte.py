"""Fontes de marcos de mao.

A fonte real (camera + modelo) e a sintetica implementam a mesma interface,
de proposito: assim o resto do sistema — filtros, mapeamento e saida — pode
ser construido e depurado inteiro antes de existir qualquer visao
computacional, que e a parte mais dificil de diagnosticar.
"""

import math

# Mao aberta, palma para a camera, dedos para cima. Coordenadas
# normalizadas 0..1 como as que o modelo devolve.
NEUTRA = [
    (0.50, 0.85),                                              # 0  pulso
    (0.40, 0.76), (0.33, 0.68), (0.28, 0.62), (0.24, 0.56),    # polegar
    (0.42, 0.60), (0.40, 0.48), (0.39, 0.40), (0.38, 0.33),    # indicador
    (0.50, 0.58), (0.50, 0.44), (0.49, 0.35), (0.49, 0.28),    # medio
    (0.58, 0.60), (0.59, 0.47), (0.60, 0.39), (0.60, 0.32),    # anelar
    (0.66, 0.65), (0.69, 0.55), (0.70, 0.49), (0.71, 0.43),    # mindinho
]

_CADEIAS = {
    "polegar": (1, 2, 3, 4),
    "indicador": (5, 6, 7, 8),
    "medio": (9, 10, 11, 12),
    "anelar": (13, 14, 15, 16),
    "mindinho": (17, 18, 19, 20),
}

GRAUS_POR_JUNTA = 85.0


def _girar(p, centro, graus):
    r = math.radians(graus)
    dx, dy = p[0] - centro[0], p[1] - centro[1]
    return (
        centro[0] + dx * math.cos(r) - dy * math.sin(r),
        centro[1] + dx * math.sin(r) + dy * math.cos(r),
    )


class MaoSintetica:
    """Gera marcos plausiveis sem camera, para exercitar o sistema inteiro."""

    def __init__(self):
        self.deslocamento = (0.0, 0.0)   # da palma, em unidades normalizadas
        self.curvas = {nome: 0.0 for nome in _CADEIAS}   # 0 = esticado, 1 = fechado

    def marcos(self):
        pts = [list(p) for p in NEUTRA]

        for nome, cadeia in _CADEIAS.items():
            c = max(0.0, min(1.0, self.curvas[nome]))
            if c <= 0.0:
                continue

            if nome == "polegar":
                # O polegar nao dobra como os outros: ele se OPOE, levando a
                # ponta em direcao a base do indicador. Girar as falanges no
                # plano mantinha a ponta longe dali, entao flexao_polegar —
                # que mede exatamente essa distancia — lia 0.00 sempre e o
                # clique esquerdo ficava sem cobertura de teste.
                alvo = NEUTRA[5]
                for j, k in enumerate(cadeia[1:], start=1):
                    peso = c * (0.35 + 0.25 * j)       # a ponta se move mais
                    pts[k] = [
                        pts[k][0] + (alvo[0] - pts[k][0]) * peso,
                        pts[k][1] + (alvo[1] - pts[k][1]) * peso,
                    ]
                continue

            for j in range(1, 4):
                pivo = pts[cadeia[j - 1]]
                for k in range(j, 4):
                    pts[cadeia[k]] = list(
                        _girar(pts[cadeia[k]], pivo, GRAUS_POR_JUNTA * c)
                    )

        dx, dy = self.deslocamento
        return [(p[0] + dx, p[1] + dy) for p in pts]
