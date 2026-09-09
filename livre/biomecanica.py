"""Biomecanica da mao: converte 21 marcos em grandezas anatomicas.

Matematica pura, sem estado e sem dependencia de camera, uinput ou perfil —
por isso e a parte do sistema que da para testar isoladamente, e a que menos
deveria mudar.

Toda medida de dedo e feita por ANGULO entre falanges, nao por posicao na
imagem, o que torna a leitura invariante a translacao e a escala: a mao pode
atravessar a cena inteira mirando que "indicador dobrado" continua lendo
igual.

As funcoes que recebem 'marcos_geo' esperam coordenadas ja convertidas para
espaco isometrico (ver ASPECTO_PADRAO). Os marcos chegam da camera
normalizados por LARGURA e ALTURA separadamente, e num quadro 4:3 isso
distorce angulos — como flexao E angulo, medir sem desfazer a distorcao
produz leitura diferente para o mesmo dedo conforme a orientacao da mao.
"""

import math

PULSO = 0
DEDOS = {
    "polegar": (1, 2, 3, 4),
    "indicador": (5, 6, 7, 8),
    "medio": (9, 10, 11, 12),
    "anelar": (13, 14, 15, 16),
    "mindinho": (17, 18, 19, 20),
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


def flexao_dedo(marcos_geo, dedo):
    """Calcula flexao combinando angulo articular e encurtamento em espaço isométrico."""
    mcp, pip, dip, tip = (marcos_geo[i] for i in dedo)

    # 1. Angulo articular real das articulacoes interfalangicas (PIP e DIP).
    # Dedos em repouso estao naturalmente entre 150 e 175 graus.
    # Exige curvatura articular genuina (< 155 graus) para iniciar a contagem.
    ang = (_angulo(mcp, pip, dip) + _angulo(pip, dip, tip)) / 2.0
    f_angulo = max(0.0, min(1.0, (155.0 - ang) / 65.0))

    # 2. Encurtamento: razao entre distancia ponta-base e o comprimento somado dos ossos.
    # Em repouso, a razao fica acima de 0.85. Ao dobrar o dedo, cai abaixo de 0.50.
    comp_ossos = (math.hypot(pip[0]-mcp[0], pip[1]-mcp[1]) +
                  math.hypot(dip[0]-pip[0], dip[1]-pip[1]) +
                  math.hypot(tip[0]-dip[0], tip[1]-dip[1]))
    if comp_ossos < 1e-6:
        return 0.0

    dist_direta = math.hypot(tip[0]-mcp[0], tip[1]-mcp[1])
    f_encurtamento = max(0.0, min(1.0, (0.85 - (dist_direta / comp_ossos)) / 0.45))

    # Fusao ponderada: 75% angulo articular (imune a corte de borda e perspectiva) + 25% encurtamento
    return max(0.0, min(1.0, 0.75 * f_angulo + 0.25 * f_encurtamento))


def flexao_polegar(marcos_geo):
    """Mede a flexao do polegar unindo articulacao propria (IP/MCP) e oposicao."""
    pulso = marcos_geo[0]
    p1, p2, p3, p4 = (marcos_geo[i] for i in (1, 2, 3, 4))
    base_indicador = marcos_geo[5]

    # 1. Curvatura da articulacao interfalangiana do polegar (nós 2-3-4)
    ang_ip = _angulo(p2, p3, p4)
    f_angulo = max(0.0, min(1.0, (170.0 - ang_ip) / 75.0))

    # 2. Encurtamento próprio da falange do polegar (distância ponta-MCP vs soma dos ossos)
    comp_ossos = math.hypot(p3[0] - p2[0], p3[1] - p2[1]) + math.hypot(p4[0] - p3[0], p4[1] - p3[1])
    if comp_ossos > 1e-6:
        dist_direta = math.hypot(p4[0] - p2[0], p4[1] - p2[1])
        f_encurtamento = max(0.0, min(1.0, (1.0 - (dist_direta / comp_ossos)) / 0.35))
    else:
        f_encurtamento = 0.0

    # 3. Oposicao (distancia ponta do polegar ate a base do indicador)
    dist_oposta = math.hypot(p4[0] - base_indicador[0], p4[1] - base_indicador[1])
    comp_palma = math.hypot(base_indicador[0] - pulso[0], base_indicador[1] - pulso[1])
    if comp_palma > 1e-6:
        razao_op = dist_oposta / comp_palma
        f_oposicao = max(0.0, min(1.0, (0.75 - razao_op) / 0.45))
    else:
        f_oposicao = 0.0

    # Fusao: 45% curvatura articular + 35% encurtamento + 20% oposicao
    return max(0.0, min(1.0, 0.45 * f_angulo + 0.35 * f_encurtamento + 0.20 * f_oposicao))


def centro_palma(marcos):
    """Centroide do pulso com as quatro bases dos dedos."""
    idx = (PULSO,) + tuple(d[0] for d in DEDOS.values() if d[0] != 1)
    return (
        sum(marcos[i][0] for i in idx) / len(idx),
        sum(marcos[i][1] for i in idx) / len(idx),
    )

