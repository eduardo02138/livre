#!/usr/bin/env python3
"""Verifica o nucleo com uma mao sintetica — sem camera, sem uinput.

    python3 testar_nucleo.py            # verifica os dois perfis e falha se divergir
    python3 testar_nucleo.py --real     # alem disso, envia de verdade (move o cursor)

As expectativas sao DERIVADAS do proprio perfil ativo, nunca escritas a mao.
Uma versao anterior deste arquivo tinha rotulos fixos descrevendo o perfil
hibrido enquanto instanciava o perfil direto: passava sem excecao e validava
comportamento diferente do que anunciava. Derivar do perfil torna esse tipo
de divergencia impossivel.
"""

import sys
import time

from livre.fonte import MaoSintetica
from livre.mapeamento import DEDOS, PERFIL_DIRETO, PERFIL_HIBRIDO, Mapeador

HZ = 30.0
CURVA_FORTE = 0.95

# Mapeamento FIXADO, escrito a mao de proposito.
#
# Derivar a expectativa do proprio dicionario do perfil — que foi a primeira
# versao disto — torna o teste tautologico: trocar "anelar -> E" por
# "anelar -> X" mudava expectativa e comportamento juntos e o teste passava.
# Com a tabela abaixo, qualquer alteracao de perfil reprova ate ser refletida
# aqui, que e o ponto: a mudanca tem de ser deliberada.
FIXADO = {
    "DIRETO": {
        "polegar":   ("botao", "ESQUERDO"),
        "indicador": ("tecla", "W"),
        "medio":     ("tecla", "S"),
        "anelar":    ("tecla", "E"),
        "mindinho":  ("botao", "DIREITO"),
    },
    "HIBRIDO": {
        "polegar":   ("botao", "ESQUERDO"),
        "indicador": ("tecla", "E"),
        "medio":     ("modo", "MOVIMENTO"),
        "anelar":    ("tecla", "F"),
        "mindinho":  ("botao", "DIREITO"),
    },
}


def _esperado(nome_perfil, dedo):
    """O que o mapeamento fixado promete para este dedo."""
    tipo, alvo = FIXADO[nome_perfil][dedo]
    return {
        "tecla": ({alvo}, set(), False),
        "botao": (set(), {alvo}, False),
        "modo": (set(), set(), True),
    }[tipo]


def verificar_perfis_fixados():
    """Os perfis em mapeamento.py ainda batem com a tabela fixada?"""
    print("\n  perfis conferem com a tabela fixada")
    problemas = []
    for nome, vivo in (("DIRETO", PERFIL_DIRETO), ("HIBRIDO", PERFIL_HIBRIDO)):
        divergencias = {
            d: (FIXADO[nome].get(d), vivo.get(d))
            for d in set(FIXADO[nome]) | set(vivo)
            if FIXADO[nome].get(d) != vivo.get(d)
        }
        print(f"    {'ok  ' if not divergencias else 'FALHA'} {nome}")
        for dedo, (esp, veio) in sorted(divergencias.items()):
            problemas.append(f"perfil {nome}/{dedo}: fixado {esp}, encontrado {veio}")
    return problemas


def verificar_dedos(nome_perfil, perfil):
    """Cada dedo, isolado, deve produzir exatamente o que o perfil promete."""
    mao = MaoSintetica()
    mapeador = Mapeador(perfil_padrao=nome_perfil)
    mapeador.recentrar(mao.marcos())
    problemas = []

    # A histerese exige que o valor persista alguns quadros antes de acionar,
    # o que elimina pulso de ruido de 1 quadro. O teste tem de alimentar essa
    # janela — chamar o mapeador uma unica vez nunca aciona nada. Derivado do
    # proprio valor para nao quebrar de novo se a janela mudar.
    janela = max((getattr(h, "quadros_confirmacao", 1)
                  for h in mapeador._hist.values()), default=1)

    print(f"\n  perfil {nome_perfil}")
    for dedo in DEDOS:
        mao.deslocamento = (0.0, 0.0)
        mao.curvas = {n: (CURVA_FORTE if n == dedo else 0.0) for n in mao.curvas}
        est = None
        for i in range(janela + 1):
            est = mapeador(mao.marcos(), i / HZ)

        teclas_e, botoes_e, modo_e = _esperado(nome_perfil, dedo)
        flex = est.flexoes[dedo]
        ok = (est.teclas == teclas_e and est.botoes == botoes_e
              and est.modo_movimento == modo_e)

        marca = "ok  " if ok else "FALHA"
        print(f"    {marca} {dedo:<10} flexao={flex:.2f}  "
              f"teclas={sorted(est.teclas) or '-'}  "
              f"botoes={sorted(est.botoes) or '-'}  modo={est.modo_movimento}")

        if flex < 0.5:
            problemas.append(
                f"{nome_perfil}/{dedo}: flexao {flex:.2f} nao chega ao limiar "
                f"nem com a mao fechada — o mock nao exercita este dedo")
        elif not ok:
            problemas.append(
                f"{nome_perfil}/{dedo}: esperava teclas={sorted(teclas_e) or '-'} "
                f"botoes={sorted(botoes_e) or '-'} modo={modo_e}, "
                f"veio teclas={sorted(est.teclas) or '-'} "
                f"botoes={sorted(est.botoes) or '-'} modo={est.modo_movimento}")
    return problemas


def verificar_mira():
    """Valida controle de mira: modo joystick (deflexão) e modo relativo (delta / deriva zero)."""
    mao = MaoSintetica()
    problemas = []

    # 1. Modo Joystick (deflexão do centro)
    print("\n  mira (controle joystick por deflexao)")
    map_joy = Mapeador(perfil_padrao="DIRETO", modo_mouse="joystick")
    map_joy.recentrar(mao.marcos())
    casos = [
        ("centro",          (0.00, 0.00), lambda e: abs(e.vel_x) < 1 and abs(e.vel_y) < 1),
        ("palma a direita", (0.30, 0.00), lambda e: e.vel_x > 50),
        ("palma acima",     (0.00, -0.30), lambda e: e.vel_y < -50),
    ]
    for descricao, desloc, condicao in casos:
        mao.deslocamento = desloc
        mao.curvas = {n: 0.0 for n in mao.curvas}
        est = None
        for i in range(45):                    # deixa o One Euro assentar
            est = map_joy(mao.marcos(), i / HZ)
        ok = condicao(est)
        print(f"    {'ok  ' if ok else 'FALHA'} {descricao:<16} "
              f"vel=({est.vel_x:+7.1f}, {est.vel_y:+7.1f}) px/s")
        if not ok:
            problemas.append(f"mira/joystick/{descricao}: vel=({est.vel_x:.1f}, {est.vel_y:.1f})")

    # 2. Modo Relativo (deslocamento diferencial: deriva zero quando parada)
    print("\n  mira (modo relativo delta)")
    map_rel = Mapeador(perfil_padrao="DIRETO", modo_mouse="relativo")
    map_rel.recentrar(mao.marcos())

    # Mão mantida estática fora do centro: DEVE parar rigorosamente em 0 px/s
    mao.deslocamento = (0.25, 0.25)
    for i in range(15):
        est = map_rel(mao.marcos(), i / HZ)
    ok_estacionario = (abs(est.vel_x) == 0.0 and abs(est.vel_y) == 0.0)
    print(f"    {'ok  ' if ok_estacionario else 'FALHA'} mao estacionaria vel=({est.vel_x:+7.1f}, {est.vel_y:+7.1f}) px/s (deriva zero)")
    if not ok_estacionario:
        problemas.append(f"mira/relativo/estacionario: vel=({est.vel_x:.1f}, {est.vel_y:.1f})")

    # Mão com deslocamento dinâmico: DEVE gerar velocidade proporcional ao delta
    mao.deslocamento = (0.25 + 0.06, 0.25)
    est_mov = map_rel(mao.marcos(), 16 / HZ)
    ok_mov = est_mov.vel_x > 50
    print(f"    {'ok  ' if ok_mov else 'FALHA'} movimento dinamico vel=({est_mov.vel_x:+7.1f}, {est_mov.vel_y:+7.1f}) px/s")
    if not ok_mov:
        problemas.append(f"mira/relativo/movimento: vel=({est_mov.vel_x:.1f}, {est_mov.vel_y:.1f})")

    return problemas


def verificar_diagonal():
    """No perfil hibrido, o modo movimento deve permitir W e A simultaneos."""
    mao = MaoSintetica()
    mapeador = Mapeador(perfil_padrao="HIBRIDO")
    mapeador.recentrar(mao.marcos())
    print("\n  diagonal (o motivo de o movimento nao sair dos dedos)")

    mao.deslocamento = (-0.30, -0.30)
    mao.curvas = {n: (CURVA_FORTE if n == "medio" else 0.0) for n in mao.curvas}
    est = None
    for i in range(45):
        est = mapeador(mao.marcos(), i / HZ)

    ok = {"W", "A"} <= est.teclas
    print(f"    {'ok  ' if ok else 'FALHA'} medio + palma cima/esquerda "
          f"-> teclas={sorted(est.teclas)}")
    return [] if ok else [f"diagonal: esperava W e A juntos, veio {sorted(est.teclas)}"]


def verificar_punho_fechado():
    """Valida detecção de punho fechado (supressão total de comandos e embreagem de mouse)."""
    mao = MaoSintetica()
    mapeador = Mapeador(perfil_padrao="DIRETO", modo_mouse="relativo")
    mapeador.recentrar(mao.marcos())
    problemas = []
    print("\n  punho fechado (supressao total e embreagem de mouse)")

    # 1. Mao em punho: todos os dedos curvados fortemente
    mao.curvas = {n: CURVA_FORTE for n in mao.curvas}
    mao.deslocamento = (0.20, 0.20)
    est = None
    for i in range(5):
        est = mapeador(mao.marcos(), i / HZ)

    ok_punho = est.punho_fechado
    ok_sem_teclas = (len(est.teclas) == 0)
    ok_sem_botoes = (len(est.botoes) == 0)
    ok_clutch = (est.vel_x == 0.0 and est.vel_y == 0.0)

    print(f"    {'ok  ' if ok_punho else 'FALHA'} punho detectado (punho_fechado={est.punho_fechado})")
    print(f"    {'ok  ' if ok_sem_teclas else 'FALHA'} teclas suprimidas: {sorted(est.teclas) or '-'}")
    print(f"    {'ok  ' if ok_sem_botoes else 'FALHA'} botoes suprimidos: {sorted(est.botoes) or '-'}")
    print(f"    {'ok  ' if ok_clutch else 'FALHA'} embreagem de mouse (clutch vel=0): ({est.vel_x:.1f}, {est.vel_y:.1f})")

    if not (ok_punho and ok_sem_teclas and ok_sem_botoes and ok_clutch):
        problemas.append(f"punho_fechado: esperado neutro sem acoes, veio teclas={est.teclas} botoes={est.botoes}")

    # 2. Reabertura da mao: volta a responder normalmente
    mao.curvas = {n: 0.0 for n in mao.curvas}
    for i in range(5, 12):
        est = mapeador(mao.marcos(), i / HZ)
    ok_aberto = (not est.punho_fechado)
    print(f"    {'ok  ' if ok_aberto else 'FALHA'} reabertura detectada (punho_fechado={est.punho_fechado})")
    if not ok_aberto:
        problemas.append("punho_fechado: mao reaberta permaneceu travada em punho")

    # 3. Combo deliberado: Andar + Mirar (Indicador + Mindinho) NÃO deve ser considerado punho
    mao.curvas = {n: (CURVA_FORTE if n in ("indicador", "mindinho") else 0.0) for n in mao.curvas}
    for i in range(12, 18):
        est = mapeador(mao.marcos(), i / HZ)
    ok_combo = (not est.punho_fechado and est.teclas == {"W"} and est.botoes == {"DIREITO"})
    print(f"    {'ok  ' if ok_combo else 'FALHA'} combo W + Mindinho liberado (teclas={sorted(est.teclas)} botoes={sorted(est.botoes)})")
    if not ok_combo:
        problemas.append(f"punho_fechado: combo legitimo W+Mindinho bloqueado indevidamente (teclas={est.teclas} botoes={est.botoes})")

    return problemas


def enviar_de_verdade():
    """Repete a sequencia mandando eventos reais para o sistema."""
    from livre.saida import Saida, SemPermissao
    try:
        saida = Saida()
    except SemPermissao as ex:
        sys.exit(str(ex))

    print("\n  dispositivo virtual criado — o cursor vai se mexer")
    mao = MaoSintetica()
    mapeador = Mapeador(perfil_padrao="HIBRIDO")
    mapeador.recentrar(mao.marcos())
    dt = 1.0 / HZ
    t = 0.0
    try:
        for desloc, curvas in ((( 0.30, 0.00), {}),
                               ((0.00, -0.28), {}),
                               ((0.00, 0.00), {"indicador": CURVA_FORTE}),
                               ((-0.30, -0.30), {"medio": CURVA_FORTE}),
                               ((0.00, 0.00), {})):
            mao.deslocamento = desloc
            mao.curvas = {n: curvas.get(n, 0.0) for n in mao.curvas}
            for _ in range(int(1.2 * HZ)):
                saida.aplicar(mapeador(mao.marcos(), t), dt)
                time.sleep(dt)
                t += dt
    except KeyboardInterrupt:
        print("  interrompido")
    finally:
        saida.fechar()
        print("  dispositivo removido")


def main():
    print("verificacao do nucleo — mao sintetica, sem camera")

    problemas = []
    problemas += verificar_perfis_fixados()
    problemas += verificar_dedos("DIRETO", PERFIL_DIRETO)
    problemas += verificar_dedos("HIBRIDO", PERFIL_HIBRIDO)
    problemas += verificar_mira()
    problemas += verificar_diagonal()
    problemas += verificar_punho_fechado()

    print()
    if problemas:
        print(f"{len(problemas)} problema(s):")
        for p in problemas:
            print(f"  - {p}")
        sys.exit(1)

    print("nucleo ok: os dois perfis entregam o que prometem.")
    if "--real" in sys.argv:
        enviar_de_verdade()


if __name__ == "__main__":
    main()
