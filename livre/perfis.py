"""Perfis de mapeamento e limiares de flexao.

Dados puros: que gesto vira que acao, e com que sensibilidade. Ficam fora do
Mapeador de proposito — sao a parte que muda por calibracao ou preferencia,
nao por logica.

Os dicionarios daqui sao TEMPLATES. Quem usa deve trabalhar sobre uma copia:
o Mapeador escreve em self.acoes ao aplicar configuracao persistente, e
enquanto isso era uma referencia direta, carregar uma config reescrevia o
perfil do modulo inteiro no processo — um mapeador novo ja nascia com o
mapeamento alterado, trocar de perfil e voltar nao restaurava, e a tabela
fixada do teste de regressao passava a comparar contra dados corrompidos.
Use perfil_por_nome(), que devolve copia.
"""


LIMIARES_INDIVIDUAIS = {
    "indicador": (0.62, 0.36),  # W (ou ação remapeada)
    "medio":     (0.62, 0.36),  # S
    "polegar":   (0.62, 0.38),  # Clique Esquerdo
    "mindinho":  (0.58, 0.34),  # Clique Direito (sensibilidade calibrada)
    "anelar":    (0.65, 0.38),  # E
}

# Perfil 1: Mapeamento Direto
# Alocado pelo acoplamento de tendao medido na telemetria, nao por conforto.
#
# Co-ativacao em menos de 300ms, sessao de 15 minutos:
#     ANELAR    + MINDINHO   32   (anelar disparou 36 vezes no total)
#     MEDIO     + MINDINHO   30
#     ANELAR    + MEDIO      24
#     INDICADOR + MINDINHO    2
#
# Medio, anelar e mindinho formam um trio acoplado: acionar um arrasta os
# outros, e isso e anatomico, nao questao de limiar. Polegar e indicador sao
# os unicos dedos independentes.
#
# Por isso as duas funcoes de menor tolerancia a erro — os botoes do mouse —
# ficam com polegar e indicador. O trio recebe movimento e habilidade, onde
# um disparo involuntario custa um passo ou um cooldown, nao um tiro nem uma
# mira travada.
#
# W e S caem no trio de proposito: ja existe exclusividade mutua entre eles,
# entao quando o acoplamento arrasta os dois juntos o conflito e resolvido em
# vez de somado.
PERFIL_DIRETO = {
    "polegar":   ("botao", "ESQUERDO"),   # independente -> atirar
    "indicador": ("botao", "DIREITO"),    # independente -> mira/ADS
    "medio":     ("tecla", "W"),          # acoplado, tolerante
    "anelar":    ("tecla", "E"),          # acoplado, tolerante
    "mindinho":  ("tecla", "S"),          # o que mais dispara sozinho -> o mais barato
}

# Perfil 2: Modo Híbrido
PERFIL_HIBRIDO = {
    "polegar":   ("botao", "ESQUERDO"),
    "mindinho":  ("botao", "DIREITO"),
    "indicador": ("tecla", "E"),
    "anelar":    ("tecla", "F"),
    "medio":     ("modo", "MOVIMENTO"),
}





# Perfil 3: do usuario — movimento nos dedos acoplados, sem strafe esquerdo.
# Difere do DIRETO so no anelar: D em vez de E. Nao tem "A", entao andar para
# a esquerda nao existe neste perfil; e uma escolha deliberada de quem usa.
PERFIL_MEU = {
    "polegar":   ("botao", "ESQUERDO"),
    "indicador": ("botao", "DIREITO"),
    "medio":     ("tecla", "W"),
    "anelar":    ("tecla", "D"),
    "mindinho":  ("tecla", "S"),
}

# Perfil 4: Overwatch — movimento completo pela palma, habilidades no rosto.
# O medio chaveia a palma para modo movimento, onde W/A/S/D saem como dois
# eixos independentes e a diagonal existe. Os dedos acoplados restantes ficam
# so com acoes tolerantes a disparo acidental: recarregar e pular.
PERFIL_OVERWATCH = {
    "polegar":   ("botao", "ESQUERDO"),   # atirar
    "indicador": ("botao", "DIREITO"),    # mira
    "medio":     ("modo", "MOVIMENTO"),   # palma vira W A S D
    "anelar":    ("tecla", "R"),          # recarregar
    "mindinho":  ("tecla", "ESPACO"),     # pular
}

# Ordem de ciclagem do [M] no painel.
PERFIS = {
    "DIRETO": PERFIL_DIRETO,
    "MEU": PERFIL_MEU,
    "OVERWATCH": PERFIL_OVERWATCH,
    "HIBRIDO": PERFIL_HIBRIDO,
}


def proximo_perfil(nome):
    """Nome do perfil seguinte no ciclo."""
    nomes = list(PERFIS)
    try:
        i = nomes.index(nome)
    except ValueError:
        return nomes[0]
    return nomes[(i + 1) % len(nomes)]


def perfil_por_nome(nome):
    """Devolve uma COPIA do perfil, para o chamador poder alterar a vontade."""
    return dict(PERFIS.get(nome, PERFIL_DIRETO))


# Gestos faciais por perfil. As habilidades do Overwatch (Shift, E, Q) saem do
# rosto porque a mao nao tem dedos independentes sobrando: medio, anelar e
# mindinho sao acoplados por tendao.
ROSTO_PADRAO = {
    "olho_direito":  ("tecla", "SHIFT"),   # Habilidade 1
    "olho_esquerdo": ("tecla", "E"),       # Habilidade 2
    "boca":          ("tecla", "Q"),       # Suprema
}

# No perfil do usuario, QUALQUER piscadela unilateral e a Habilidade 1, e a
# Habilidade 2 vem de fechar os DOIS olhos e segurar. Piscar os dois e
# involuntario 15-20 vezes por minuto, entao esse gesto so conta como comando
# depois de QUADROS_PISCADA_LONGA quadros (~400 ms) — bem acima dos 100-150 ms
# de uma piscada natural.
ROSTO_MEU = {
    "olho_direito":  ("tecla", "SHIFT"),
    "olho_esquerdo": ("tecla", "SHIFT"),
    "piscada_longa": ("tecla", "E"),
    "boca":          ("tecla", "Q"),
}

ROSTO_POR_PERFIL = {
    "MEU": ROSTO_MEU,
}


def rosto_por_perfil(nome):
    """Copia do mapa de gestos faciais do perfil."""
    return dict(ROSTO_POR_PERFIL.get(nome, ROSTO_PADRAO))
