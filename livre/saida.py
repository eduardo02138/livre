"""Saida para o jogo via uinput.

Nao simula teclas: cria dispositivos. O kernel registra um mouse e um
teclado virtuais que entram na mesma fila de eventos que hardware real,
por isso o jogo nao precisa saber de nada e o Wayland deixa de ser
obstaculo — uinput opera abaixo do compositor.
"""

import time

from evdev import UInput, ecodes as e

TECLAS = {
    "W": e.KEY_W, "A": e.KEY_A, "S": e.KEY_S, "D": e.KEY_D,
    "E": e.KEY_E, "F": e.KEY_F, "ESPACO": e.KEY_SPACE, "SHIFT": e.KEY_LEFTSHIFT,
}
BOTOES = {"ESQUERDO": e.BTN_LEFT, "DIREITO": e.BTN_RIGHT}

CAPS = {
    e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL],
    e.EV_KEY: list(TECLAS.values()) + list(BOTOES.values()),
}


class SemPermissao(Exception):
    pass


class Saida:
    def __init__(self, nome="mao-virtual", espera=1.2):
        try:
            self._ui = UInput(CAPS, name=nome, version=1)
        except PermissionError as ex:
            raise SemPermissao(
                "sem escrita em /dev/uinput. confira com: getfacl /dev/uinput\n"
                "deve haver uma linha 'user:<voce>:rw-'"
            ) from ex

        # O compositor leva um instante para reconhecer o dispositivo novo;
        # sem esta pausa os primeiros eventos somem sem erro nenhum.
        time.sleep(espera)

        self._ligados = set()
        self._resto_x = 0.0
        self._resto_y = 0.0

    def aplicar(self, estado, dt):
        """Integra a velocidade em pixels e emite so o que mudou."""
        self._resto_x += estado.vel_x * dt
        self._resto_y += estado.vel_y * dt
        px = int(self._resto_x)
        py = int(self._resto_y)
        self._resto_x -= px
        self._resto_y -= py

        moveu = False
        if px:
            self._ui.write(e.EV_REL, e.REL_X, px)
            moveu = True
        if py:
            self._ui.write(e.EV_REL, e.REL_Y, py)
            moveu = True

        alvo = set()
        for t in estado.teclas:
            if t in TECLAS:
                alvo.add(TECLAS[t])
        for b in estado.botoes:
            if b in BOTOES:
                alvo.add(BOTOES[b])

        for cod in alvo - self._ligados:
            self._ui.write(e.EV_KEY, cod, 1)
        for cod in self._ligados - alvo:
            self._ui.write(e.EV_KEY, cod, 0)

        if moveu or alvo != self._ligados:
            self._ui.syn()
        self._ligados = alvo

    def soltar_tudo(self):
        for cod in self._ligados:
            self._ui.write(e.EV_KEY, cod, 0)
        if self._ligados:
            self._ui.syn()
        self._ligados = set()

    def fechar(self):
        self.soltar_tudo()
        self._ui.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.fechar()
