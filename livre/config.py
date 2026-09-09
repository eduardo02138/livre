"""Gerenciamento de configuração persistente para sensibilidade e mapeamento.

Salva e carrega de 'configuracao.json' no diretório do projeto:
  • Limiares individuais por dedo (aciona / libera)
  • Ação atribuída a cada dedo (tecla, botão de mouse ou modo)
  • Parâmetros de mira do mouse (velocidade máxima, zona morta, expoente)
"""

import os
import json

ACOES_DISPONIVEIS = [
    ("tecla", "W"),
    ("tecla", "S"),
    ("tecla", "A"),
    ("tecla", "D"),
    ("tecla", "E"),
    ("tecla", "F"),
    ("tecla", "ESPACO"),
    ("tecla", "SHIFT"),
    ("botao", "ESQUERDO"),
    ("botao", "DIREITO"),
    ("modo",  "MOVIMENTO"),
    ("nenhum", "-"),
]

CONFIG_PADRAO = {
    "dedos": {
        "polegar":   {"tipo": "botao", "alvo": "ESQUERDO", "aciona": 0.55, "libera": 0.35},
        "indicador": {"tipo": "tecla", "alvo": "W",        "aciona": 0.48, "libera": 0.28},
        "medio":     {"tipo": "tecla", "alvo": "S",        "aciona": 0.50, "libera": 0.30},
        "anelar":    {"tipo": "tecla", "alvo": "E",        "aciona": 0.62, "libera": 0.42},
        "mindinho":  {"tipo": "botao", "alvo": "DIREITO",  "aciona": 0.55, "libera": 0.35},
    },
    "mouse": {
        "vel_max": 900.0,
        "zona_morta": 0.20,
        "expo": 2.0,
    }
}


class Configuracao:
    def __init__(self, caminho_arquivo=None):
        if caminho_arquivo is None:
            raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            caminho_arquivo = os.path.join(raiz, "configuracao.json")

        self.caminho = caminho_arquivo
        self.dados = self.carregar()

    def carregar(self):
        """Carrega do disco ou inicializa com valores padrão."""
        if os.path.exists(self.caminho):
            try:
                with open(self.caminho, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                # Garante chaves essenciais
                for d in CONFIG_PADRAO["dedos"]:
                    if d not in cfg.get("dedos", {}):
                        cfg.setdefault("dedos", {})[d] = CONFIG_PADRAO["dedos"][d].copy()
                if "mouse" not in cfg:
                    cfg["mouse"] = CONFIG_PADRAO["mouse"].copy()
                return cfg
            except Exception as e:
                print(f"⚠️ Erro ao ler {self.caminho}: {e}. Usando configuração padrão.")

        return json.loads(json.dumps(CONFIG_PADRAO))

    def salvar(self):
        """Persiste a configuração atual em disco."""
        try:
            with open(self.caminho, "w", encoding="utf-8") as f:
                json.dump(self.dados, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"❌ Erro ao salvar {self.caminho}: {e}")
            return False

    def obter_dedo(self, nome):
        return self.dados["dedos"].get(nome, CONFIG_PADRAO["dedos"].get(nome, {}))

    def ajustar_limiar(self, nome, delta):
        """Aumenta ou diminui a sensibilidade do dedo específico."""
        d = self.dados["dedos"][nome]
        novo_aciona = round(max(0.18, min(0.90, d["aciona"] + delta)), 2)
        novo_libera = round(max(0.10, min(0.80, novo_aciona - 0.20)), 2)
        d["aciona"] = novo_aciona
        d["libera"] = novo_libera
        self.salvar()
        return novo_aciona, novo_libera

    def ciclo_acao(self, nome):
        """Alterna a ação associada ao dedo para a próxima da lista."""
        d = self.dados["dedos"][nome]
        atual = (d["tipo"], d["alvo"])
        idx = 0
        for i, item in enumerate(ACOES_DISPONIVEIS):
            if item == atual:
                idx = (i + 1) % len(ACOES_DISPONIVEIS)
                break

        novo_tipo, novo_alvo = ACOES_DISPONIVEIS[idx]
        d["tipo"] = novo_tipo
        d["alvo"] = novo_alvo
        self.salvar()
        return novo_tipo, novo_alvo

    def ajustar_mouse(self, delta_vel=0, delta_zm=0):
        """Ajusta parâmetros de sensibilidade da mira do mouse."""
        m = self.dados["mouse"]
        if delta_vel != 0:
            m["vel_max"] = max(200.0, min(2500.0, m["vel_max"] + delta_vel))
        if delta_zm != 0:
            m["zona_morta"] = max(0.05, min(0.40, round(m["zona_morta"] + delta_zm, 2)))
        self.salvar()
        return m["vel_max"], m["zona_morta"]
