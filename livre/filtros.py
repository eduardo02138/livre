"""Filtros e curvas de resposta.

Matematica pura, sem camera e sem uinput — e por isso a unica parte do
sistema que da para testar e calibrar isoladamente.
"""

import math


def _alfa(corte_hz, freq_hz):
    tau = 1.0 / (2.0 * math.pi * corte_hz)
    te = 1.0 / freq_hz
    return 1.0 / (1.0 + tau / te)


class _PassaBaixa:
    def __init__(self):
        self.y = None

    def aplicar(self, x, alfa):
        self.y = x if self.y is None else alfa * x + (1.0 - alfa) * self.y
        return self.y


class OneEuro:
    """Suaviza forte com a mao quase parada, afrouxa quando ela acelera.

    E adaptativo justamente para nao trocar tremor por atraso: uma media
    movel comum mataria o tremor e destruiria a resposta junto.

    beta maior  -> segue mais rapido, treme mais
    corte_min maior -> menos suavizacao em repouso
    """

    def __init__(self, freq_hz=30.0, corte_min=1.0, beta=0.007, corte_derivada=1.0):
        self.freq = freq_hz
        self.corte_min = corte_min
        self.beta = beta
        self.corte_derivada = corte_derivada
        self._x = _PassaBaixa()
        self._dx = _PassaBaixa()
        self._x_ant = None
        self._t_ant = None

    def __call__(self, x, t):
        if self._t_ant is not None:
            dt = t - self._t_ant
            if dt > 1e-6:
                self.freq = 1.0 / dt
        self._t_ant = t

        dx = 0.0 if self._x_ant is None else (x - self._x_ant) * self.freq
        self._x_ant = x

        dx_suave = self._dx.aplicar(dx, _alfa(self.corte_derivada, self.freq))
        corte = self.corte_min + self.beta * abs(dx_suave)
        return self._x.aplicar(x, _alfa(corte, self.freq))


class Histerese:
    """Dois limiares com confirmação temporal simétrica: elimina ruídos de 1 quadro.

    Exige persistência de 'quadros_confirmacao' (padrão: 2) para ativar e
    'quadros_libera' (padrão: 2, ~66ms) para soltar. Isso impede que quedas
    transitórias de 1 único quadro (30ms) causadas por oclusão de sensor quebrem
    a caminhada contínua (ex: sprint no W) ou soltem cliques sustentados.
    """

    def __init__(self, aciona, libera, quadros_confirmacao=2, quadros_libera=2):
        if libera >= aciona:
            raise ValueError("'libera' precisa ser menor que 'aciona'")
        self.aciona = aciona
        self.libera = libera
        self.quadros_confirmacao = quadros_confirmacao
        self.quadros_libera = quadros_libera
        self.ativo = False
        self._contador_aciona = 0
        self._contador_libera = 0

    def __call__(self, valor):
        if self.ativo:
            if valor < self.libera:
                self._contador_libera += 1
                if self._contador_libera >= self.quadros_libera:
                    self.ativo = False
                    self._contador_libera = 0
                    self._contador_aciona = 0
            else:
                self._contador_libera = 0
        else:
            if valor >= self.aciona:
                self._contador_aciona += 1
                if self._contador_aciona >= self.quadros_confirmacao:
                    self.ativo = True
                    self._contador_aciona = 0
                    self._contador_libera = 0
            else:
                self._contador_aciona = 0
        return self.ativo


def velocidade(desloc, zona_morta=0.15, expo=2.0, vel_max=900.0):
    """Deslocamento normalizado (-1..1) -> velocidade do cursor em px/s.

    Controle de velocidade, nao de posicao: a mao nunca "acaba o curso" e
    um tremor de poucos milimetros vira velocidade quase nula em vez de
    um salto de cursor. A curva exponencial concentra o controle fino
    perto do centro, que e onde acontece a correcao de mira.
    """
    m = abs(desloc)
    if m <= zona_morta:
        return 0.0
    norm = min((m - zona_morta) / (1.0 - zona_morta), 1.0)
    return math.copysign(norm ** expo * vel_max, desloc)
