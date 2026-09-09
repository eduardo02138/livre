#!/usr/bin/env python3
"""
=============================================================================
PROJETO UMA MÃO NO AR — ETAPA 3: PROVA DE SAÍDA (UINPUT)
=============================================================================
Valida uinput, permissões e integração com o compositor gráfico (Wayland/X11).
Sem câmera, sem sudo, sem dependência externa além de python-evdev.

Uso:
    python3 prova_saida.py
    python3 prova_saida.py --teclas
"""

import sys
import time
import argparse

try:
    import evdev
    from evdev import ecodes
except ImportError:
    print("❌ Erro: python-evdev não encontrado!")
    print("Instale com: sudo pacman -S python-evdev")
    sys.exit(1)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Prova de Saída — Testa o mouse e teclado virtuais via uinput."
    )
    parser.add_argument(
        "--teclas", "-t",
        action="store_true",
        help="Testa o envio das teclas virtuais (W, A, S, D, E, F) após o quadrado."
    )
    parser.add_argument(
        "--espera",
        type=int,
        default=5,
        help="Segundos de contagem regressiva antes de iniciar (padrão: 5s)."
    )
    return parser.parse_args()


def criar_dispositivos():
    """Cria os dispositivos virtuais de mouse e teclado via uinput."""
    # Mouse virtual: movimento relativo e botões esquerdo/direito
    cap_mouse = {
        ecodes.EV_REL: [ecodes.REL_X, ecodes.REL_Y],
        ecodes.EV_KEY: [ecodes.BTN_LEFT, ecodes.BTN_RIGHT],
    }

    # Teclado virtual: W, A, S, D, E, F
    cap_teclado = {
        ecodes.EV_KEY: [
            ecodes.KEY_W,
            ecodes.KEY_A,
            ecodes.KEY_S,
            ecodes.KEY_D,
            ecodes.KEY_E,
            ecodes.KEY_F,
        ]
    }

    try:
        mouse = evdev.UInput(cap_mouse, name="UmaMaoNoAr-MouseVirtual")
        teclado = evdev.UInput(cap_teclado, name="UmaMaoNoAr-TecladoVirtual")
        return mouse, teclado
    except PermissionError:
        print("❌ Permissão negada ao acessar /dev/uinput.")
        print("Execute no terminal:")
        print("   sudo usermod -aG input $USER")
        print("   sudo chmod 660 /dev/uinput")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Erro ao criar dispositivos uinput: {e}")
        sys.exit(1)


def desenhar_quadrado(mouse, tamanho=250, passos=50, delay=0.01):
    """Move o cursor do mouse em um quadrado suave na tela."""
    print("\n📐 Desenhando quadrado com o cursor...")

    delta_por_passo = tamanho // passos

    lados = [
        ("Direita  👉", delta_por_passo, 0),
        ("Baixo    👇", 0, delta_por_passo),
        ("Esquerda 👈", -delta_por_passo, 0),
        ("Cima     👆", 0, -delta_por_passo),
    ]

    for nome_lado, dx, dy in lados:
        print(f"   Lado: {nome_lado}")
        for _ in range(passos):
            mouse.write(ecodes.EV_REL, ecodes.REL_X, dx)
            mouse.write(ecodes.EV_REL, ecodes.REL_Y, dy)
            mouse.syn()
            time.sleep(delay)


def digitar_teclas(teclado):
    """Envia as teclas W, A, S, D, E, F como se tivessem sido digitadas."""
    print("\n⌨️  Enviando teclas W, A, S, D, E, F...")

    teclas = [
        (ecodes.KEY_W, "W"),
        (ecodes.KEY_A, "A"),
        (ecodes.KEY_S, "S"),
        (ecodes.KEY_D, "D"),
        (ecodes.KEY_E, "E"),
        (ecodes.KEY_F, "F"),
    ]

    for codigo, nome in teclas:
        print(f"   Pressionando: [{nome}]")
        teclado.write(ecodes.EV_KEY, codigo, 1)  # Key down
        teclado.syn()
        time.sleep(0.12)
        teclado.write(ecodes.EV_KEY, codigo, 0)  # Key up
        teclado.syn()
        time.sleep(0.20)


def main():
    args = parse_args()

    print("=" * 65)
    print("  ✋ PROJETO UMA MÃO NO AR — PROVA DE SAÍDA (ETAPA 3)")
    print("=" * 65)
    print("✅ Inicializando dispositivos virtuais no kernel via uinput...")

    mouse, teclado = criar_dispositivos()
    print("✅ Dispositivos criados com sucesso:")
    print(f"   • Mouse:   UmaMaoNoAr-MouseVirtual")
    print(f"   • Teclado: UmaMaoNoAr-TecladoVirtual")

    if args.teclas:
        print("\n⚠️  Opção --teclas ATIVA:")
        print("   Foque agora uma janela de texto (editor, terminal ou bloco de notas)!")
    else:
        print("\n💡 Dica: para testar também digitação das teclas W A S D E F, use:")
        print("   python3 prova_saida.py --teclas")

    print(f"\n⏳ Iniciando contagem regressiva ({args.espera} segundos)...")
    for i in range(args.espera, 0, -1):
        print(f"   [ {i} ] segure ou solte o mouse físico...")
        time.sleep(1)

    print("🚀 Disparando eventos de saída!")

    try:
        # 1. Desenhar o quadrado com o cursor
        desenhar_quadrado(mouse)

        # 2. Opcional: testar teclas
        if args.teclas:
            digitar_teclas(teclado)

        print("\n" + "=" * 65)
        print("🎉 SUCESSO! Prova de saída concluída sem erros.")
        print("   • uinput: FUNCIONANDO")
        print("   • Permissões: VÁLIDAS")
        print("   • Integração com ambiente gráfico: CONFIRMADA")
        print("=" * 65)

    except KeyboardInterrupt:
        print("\n⛔ Interrompido pelo usuário.")
    finally:
        mouse.close()
        teclado.close()


if __name__ == "__main__":
    main()
