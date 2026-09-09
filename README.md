# livre

Controle de mouse e teclado com **uma mão só**, rastreada por webcam.

A mão no ar é o dispositivo inteiro: a palma mira, os dedos agem. Nasceu de
uma restrição concreta — mão direita imobilizada e pescoço imobilizado —, e o
desenho todo decorre disso.

A saída não simula teclas: cria dispositivos via `uinput`. O jogo recebe um
mouse e um teclado comuns, na mesma fila de eventos do hardware real. Por isso
funciona no Wayland, onde injeção de entrada por cliente comum é bloqueada.

## Como funciona

```
webcam → palm_detection.onnx → recorte rotacionado → hand_landmark.onnx
       → 21 marcos → flexão por dedo + posição da palma
       → uinput (mouse + teclado) → jogo
```

Dois estágios porque o modelo de marcos foi treinado em recortes justos com a
mão preenchendo o quadro; alimentá-lo com a cena inteira devolve ruído com
aparência de esqueleto. Depois do primeiro acerto a região de interesse é
reaproveitada e o detector só volta a rodar quando a mão se perde.

## Perfis

| Dedo | DIRETO | HÍBRIDO |
|---|---|---|
| Polegar | clique esquerdo | clique esquerdo |
| Indicador | `W` | `E` |
| Médio | `S` | modo movimento |
| Anelar | `E` | `F` |
| Mindinho | clique direito | clique direito |
| Palma | mira | mira, ou `W A S D` no modo movimento |

O **HÍBRIDO** existe por um motivo específico: três dedos dão sete acordes,
mas dois dedos juntos viram *um terceiro símbolo*, não duas teclas — o que
elimina a diagonal. No modo movimento, `W` e `A` voltam a ser bits
independentes do mesmo sinal de dois eixos, e andar na diagonal funciona.

O **DIRETO** troca isso por mirar e andar ao mesmo tempo.

## Instalação

```bash
sudo pacman -S --needed python-opencv python-onnxruntime-cpu python-evdev
./baixar_modelos.sh
```

Escrever em `/dev/uinput` costuma já funcionar: o `systemd-logind` concede uma
ACL ao usuário da sessão ativa. Confira com `getfacl /dev/uinput` — deve haver
uma linha `user:<você>:rw-`. Se não houver, entre no grupo `input` e refaça o
login.

## Uso

```bash
python3 painel.py        # painel visual; [J] liga a saída para o jogo
```

Atalhos: `[C]` recentra o repouso · `[M]` troca de perfil · `[+]`/`[-]`
ajustam sensibilidade · `[J]` liga/desliga a saída · `[Q]` sai.

O painel abre em modo visual, sem tocar no cursor. Calibre olhando as barras
de flexão antes de ligar o `[J]`.

## Verificação

```bash
python3 testar_nucleo.py   # núcleo com mão sintética, sem câmera. Sai 1 se falhar.
python3 prova_saida.py     # cria os dispositivos virtuais e desenha um quadrado
python3 perfilar.py        # onde vai o tempo de cada quadro
```

`testar_nucleo.py` compara os perfis contra uma tabela fixada à mão. Mudou um
mapeamento? Ele reprova até a tabela ser atualizada de propósito — a versão
anterior derivava a expectativa do próprio perfil e passava com qualquer
alteração.

## Diagnóstico

- `logs/rastreador.log` — uma linha por segundo: fps, ms por quadro, taxa de
  perda, redetecções, score, tamanho da região de interesse.
- `telemetria/` — CSV a 10 Hz, log de eventos e um resumo por sessão com picos
  de flexão e recomendação de calibração.

Os dois são complementares: o primeiro diz se o **rastreador** está saudável,
o segundo diz como a **sua mão** está se comportando.

## Limites conhecidos

- A câmera entrega 30 fps, então o piso de latência de captura é 33 ms. É por
  isso que a mira usa controle de velocidade, e não de posição.
- No GNOME sobre Wayland o painel é uma janela comum: fica coberto por jogos em
  fullscreen exclusivo. Janela sem borda ou segundo monitor resolvem.
- Apoie cotovelo e antebraço na mesa. Braço suspenso cansa rápido, e um quadro
  menor deixa a mão maior na imagem, o que estabiliza o rastreamento.
