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


def perfil_por_nome(nome):
    """Devolve uma COPIA do perfil, para o chamador poder alterar a vontade."""
    return dict(PERFIL_HIBRIDO if nome == "HIBRIDO" else PERFIL_DIRETO)
