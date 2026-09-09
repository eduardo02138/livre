"""Saida para o jogo via uinput.

Nao simula teclas: cria dispositivos. O kernel registra um mouse e um
teclado virtuais que entram na mesma fila de eventos que hardware real,
por isso o jogo nao precisa saber de nada e o Wayland deixa de ser
obstaculo — uinput opera abaixo do compositor.
"""

import atexit
import signal
import threading
import time

from evdev import UInput, ecodes as e

# Se 'aplicar' parar de ser chamado por mais tempo que isto, o cao de guarda
# solta tudo. Uma tecla presa no kernel continua presa mesmo com o processo
# travado — e quem perde o controle do teclado e o usuario, nao o programa.
# Folgado o bastante para nao disparar num engasgo de meio segundo do laco.
TEMPO_LIMITE_S = 1.5

TECLAS = {
    "W": e.KEY_W, "A": e.KEY_A, "S": e.KEY_S, "D": e.KEY_D,
    "E": e.KEY_E, "F": e.KEY_F, "ESPACO": e.KEY_SPACE, "SHIFT": e.KEY_LEFTSHIFT,
    "Q": e.KEY_Q, "V": e.KEY_V, "R": e.KEY_R,
}
BOTOES = {"ESQUERDO": e.BTN_LEFT, "DIREITO": e.BTN_RIGHT}

# DOIS dispositivos, nao um.
#
# Um unico no evdev anunciando EV_REL junto com KEY_W..KEY_R recebe do kernel
# 'Handlers=kbd event mouse' e do udev ID_INPUT_MOUSE=1 E ID_INPUT_KEY=1 ao
# mesmo tempo. Nenhum hardware real faz isso: mesmo um combo teclado+mouse com
# um so receptor USB se apresenta como dois dispositivos separados. SDL e as
# camadas de input dos jogos classificam cada dispositivo em UM papel, e o no
# hibrido fica ambiguo — funciona na area de trabalho, onde o compositor le os
# eventos crus, e some dentro do jogo.
#
# Separar reproduz a convencao que o hardware real segue. Os nomes continuam
# dizendo que sao virtuais; a mudanca e de forma, nao de disfarce.
CAPS_MOUSE = {
    e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL],
    e.EV_KEY: list(BOTOES.values()),
}
CAPS_TECLADO = {
    e.EV_KEY: list(TECLAS.values()),
}

# Mantido para compatibilidade com quem importava CAPS.
CAPS = {
    e.EV_REL: CAPS_MOUSE[e.EV_REL],
    e.EV_KEY: list(TECLAS.values()) + list(BOTOES.values()),
}


class SemPermissao(Exception):
    pass


class Saida:
    """Mouse e teclado virtuais, com tres redes de seguranca.

    O risco real deste modulo nao e errar um evento: e deixar uma tecla
    AFUNDADA no kernel. Se o processo morre, trava ou o jogo cai com W
    pressionado, o kernel nao sabe disso — a tecla continua pressionada para
    o sistema inteiro e o usuario perde o controle do teclado.

    Por isso tres camadas independentes soltam tudo:
      1. sinais (SIGINT/SIGTERM/SIGHUP), para encerramento pedido de fora
      2. atexit, que cobre excecao nao tratada e saida normal
      3. cao de guarda, que cobre o pior caso — o laco principal travar sem
         morrer, situacao em que 1 e 2 nunca disparam
    """

    def __init__(self, nome="mao-virtual", espera=1.2, tempo_limite=TEMPO_LIMITE_S):
        try:
            self._ui_mouse = UInput(CAPS_MOUSE, name=f"{nome} mouse", version=1)
            self._ui_teclado = UInput(CAPS_TECLADO, name=f"{nome} teclado", version=1)
        except PermissionError as ex:
            raise SemPermissao(
                "sem escrita em /dev/uinput. confira com: getfacl /dev/uinput\n"
                "deve haver uma linha 'user:<voce>:rw-'"
            ) from ex

        # Roteia cada codigo para o dispositivo certo.
        self._dev_do_codigo = {}
        for cod in CAPS_MOUSE[e.EV_KEY]:
            self._dev_do_codigo[cod] = self._ui_mouse
        for cod in CAPS_TECLADO[e.EV_KEY]:
            self._dev_do_codigo[cod] = self._ui_teclado

        # O compositor leva um instante para reconhecer o dispositivo novo;
        # sem esta pausa os primeiros eventos somem sem erro nenhum.
        time.sleep(espera)

        self._ligados = set()
        self._resto_x = 0.0
        self._resto_y = 0.0

        self._lock = threading.RLock()
        self._fechado = False
        self.tempo_limite = tempo_limite
        self._ultimo_aplicar = time.monotonic()

        atexit.register(self._emergencia)
        self._sinais_anteriores = {}
        self._instalar_sinais()

        self._cao = threading.Thread(target=self._vigiar, name="solta-teclas",
                                     daemon=True)
        self._cao.start()

    # ------------------------------------------------------- redes de seguranca

    def _instalar_sinais(self):
        """Solta as teclas antes de morrer — encadeando ao handler anterior."""
        if threading.current_thread() is not threading.main_thread():
            return                      # so a thread principal pode registrar
        for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            try:
                anterior = signal.getsignal(s)
                self._sinais_anteriores[s] = anterior
                signal.signal(s, self._fazer_manipulador(s, anterior))
            except (ValueError, OSError, AttributeError):
                pass                    # plataforma sem esse sinal: segue

    def _fazer_manipulador(self, sinal, anterior):
        def manipulador(num, quadro):
            self._emergencia()
            # Encadeia: nao engolir o Ctrl+C nem o encerramento do sistema.
            if callable(anterior) and anterior not in (signal.SIG_IGN, signal.SIG_DFL):
                anterior(num, quadro)
            elif anterior == signal.SIG_DFL:
                signal.signal(sinal, signal.SIG_DFL)
                signal.raise_signal(num)
        return manipulador

    def _vigiar(self):
        """Se 'aplicar' parou de chegar e ha tecla presa, solta."""
        while not self._fechado:
            time.sleep(0.25)
            try:
                with self._lock:
                    if (self._ligados
                            and time.monotonic() - self._ultimo_aplicar > self.tempo_limite):
                        self.soltar_tudo()
            except Exception:
                return                  # dispositivo ja fechado: nada a fazer

    def _emergencia(self):
        try:
            with self._lock:
                if not self._fechado:
                    self.soltar_tudo()
        except Exception:
            pass                        # ultimo recurso: nunca propagar daqui

    def aplicar(self, estado, dt):
        """Integra a velocidade em pixels e emite so o que mudou."""
        with self._lock:
            self._ultimo_aplicar = time.monotonic()
            if self._fechado:
                return
            self._aplicar_sem_lock(estado, dt)

    def _aplicar_sem_lock(self, estado, dt):
        if abs(estado.vel_x) < 1e-3:
            self._resto_x = 0.0
        else:
            self._resto_x += estado.vel_x * dt

        if abs(estado.vel_y) < 1e-3:
            self._resto_y = 0.0
        else:
            self._resto_y += estado.vel_y * dt

        px = int(self._resto_x)
        py = int(self._resto_y)
        self._resto_x -= px
        self._resto_y -= py

        moveu = False
        if px:
            self._ui_mouse.write(e.EV_REL, e.REL_X, px)
            moveu = True
        if py:
            self._ui_mouse.write(e.EV_REL, e.REL_Y, py)
            moveu = True

        alvo = set()
        for t in estado.teclas:
            if t in TECLAS:
                alvo.add(TECLAS[t])
        for b in estado.botoes:
            if b in BOTOES:
                alvo.add(BOTOES[b])

        tocados = set()
        for cod in alvo - self._ligados:
            dev = self._dev_do_codigo[cod]
            dev.write(e.EV_KEY, cod, 1)
            tocados.add(dev)
        for cod in self._ligados - alvo:
            dev = self._dev_do_codigo[cod]
            dev.write(e.EV_KEY, cod, 0)
            tocados.add(dev)

        if moveu:
            tocados.add(self._ui_mouse)
        for dev in tocados:
            dev.syn()
        self._ligados = alvo

    def soltar_tudo(self):
        with self._lock:
            if self._fechado:
                return
            tocados = set()
            for cod in self._ligados:
                dev = self._dev_do_codigo[cod]
                dev.write(e.EV_KEY, cod, 0)
                tocados.add(dev)
            for dev in tocados:
                dev.syn()
            self._ligados = set()
            self._resto_x = self._resto_y = 0.0

    def fechar(self):
        with self._lock:
            if self._fechado:
                return
            self.soltar_tudo()
            self._fechado = True
            self._ui_mouse.close()
            self._ui_teclado.close()

        for s, anterior in self._sinais_anteriores.items():
            try:
                signal.signal(s, anterior)
            except (ValueError, OSError):
                pass
        self._sinais_anteriores.clear()
        try:
            atexit.unregister(self._emergencia)
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.fechar()
