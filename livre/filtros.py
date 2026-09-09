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
    """Dois limiares: um para acionar, outro menor para soltar.

    Sem isso, qualquer valor parado em cima do limiar dispara e solta
    dezenas de vezes por segundo.
    """

    def __init__(self, aciona, libera):
        if libera >= aciona:
            raise ValueError("'libera' precisa ser menor que 'aciona'")
        self.aciona = aciona
        self.libera = libera
        self.ativo = False

    def __call__(self, valor):
        if self.ativo:
            if valor < self.libera:
                self.ativo = False
        elif valor > self.aciona:
            self.ativo = True
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
