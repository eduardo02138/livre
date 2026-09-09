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
    """Palma deslocada deve gerar velocidade no sentido certo, e zero no centro."""
    mao = MaoSintetica()
    mapeador = Mapeador(perfil_padrao="DIRETO")
    mapeador.recentrar(mao.marcos())
    problemas = []
    print("\n  mira (controle de velocidade)")

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
            est = mapeador(mao.marcos(), i / HZ)
        ok = condicao(est)
        print(f"    {'ok  ' if ok else 'FALHA'} {descricao:<16} "
              f"vel=({est.vel_x:+7.1f}, {est.vel_y:+7.1f}) px/s")
        if not ok:
            problemas.append(f"mira/{descricao}: vel=({est.vel_x:.1f}, {est.vel_y:.1f})")
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
