# livre

Controle de mouse e teclado com **uma mão só**, rastreada por webcam.

A mão no ar é o dispositivo inteiro: a palma mira, os dedos agem. Nasceu de
uma restrição concreta — mão direita imobilizada e pescoço imobilizado —, e o
desenho todo decorre disso.

A saída não simula teclas: cria **dois dispositivos** via `uinput` (um mouse
virtual + um teclado virtual). O jogo recebe um mouse e um teclado comuns, na
mesma fila de eventos do hardware real. Por isso funciona no Wayland, onde
injeção de entrada por cliente comum é bloqueada.

## Como funciona

```
webcam → palm_detection.onnx  → recorte rotacionado da palma
       → hand_landmark.onnx   → 21 marcos 3D da mão
       → biomecânica          → flexão por dedo + posição da palma
       → uinput (2 dispositivos) → jogo
       └─ face_detection.onnx → face_landmark.onnx (468 marcos)
            → EAR/MAR         → ações faciais (Overwatch: Shift, E, Q)
```

**Dois estágios para a mão** porque o modelo de marcos foi treinado em
recortes justos com a mão preenchendo o quadro; alimentá-lo com a cena
inteira devolve ruído com aparência de esqueleto. Depois do primeiro acerto
a região de interesse (ROI) é reaproveitada e o detector só volta a rodar
quando a mão se perde.

**Rosto em paralelo**: detecção 128×128 + marcos 192×192. Calcula EAR
(olhos) e MAR (boca) com diferenciação rigorosa entre piscadela unilateral
e piscada bilateral natural. Não dispara especial ao piscar os dois olhos
ao mesmo tempo.

## Perfis

| Dedo    | DIRETO          | HÍBRIDO            |
|---------|-----------------|--------------------|
| Polegar | Clique esquerdo | Clique esquerdo    |
| Indicador | `W`           | `E`                |
| Médio   | `S`             | **Modo Movimento** |
| Anelar  | `E`             | `F`                |
| Mindinho| Clique direito  | Clique direito     |
| Palma   | Mira (joystick) | Mira, ou `W A S D` no modo movimento |

**DIRETO** — Mirar e mover ao mesmo tempo. Palma controla velocidade do mouse
(joystick); dedos acionam teclas/botões independentes. `W` e `S` têm
exclusividade mútua biomecânica: se ambos atingirem o limiar, o mais dobrado
vence com histerese de transição.

**HÍBRIDO** — Três dedos dão sete acordes, mas dois dedos juntos viram *um
terceiro símbolo*, não duas teclas — o que elimina a diagonal. O **Médio
ativa o Modo Movimento**: enquanto dobrado, a palma emite `W A S D` como bits
independentes do mesmo sinal de dois eixos, e andar na diagonal funciona.
Solte o médio para voltar a mirar.

**Ações faciais** (configuráveis em `configuracao.json`):
- Piscadela direita → `SHIFT` (Habilidade 1 / Mobilidade)
- Piscadela esquerda → `E` (Habilidade 2 / Tática)
- Abertura da boca → `Q` (Suprema / Ultimate)

## Instalação

### Arch / Manjaro / EndeavourOS
```bash
sudo pacman -S --needed python-opencv python-onnxruntime-cpu python-evdev
./baixar_modelos.sh
```

### Debian / Ubuntu / Mint / Pop!_OS
```bash
sudo apt update && sudo apt install -y python3-opencv python3-onnxruntime python3-evdev
./baixar_modelos.sh
```

### Fedora
```bash
sudo dnf install -y python3-opencv python3-onnxruntime python3-evdev
./baixar_modelos.sh
```

### Via pip (qualquer distro, se os pacotes do sistema não estiverem disponíveis)
```bash
pip install opencv-python onnxruntime evdev
./baixar_modelos.sh
```

**Permissão uinput**: Escrever em `/dev/uinput` costuma já funcionar — o
`systemd-logind` concede uma ACL ao usuário da sessão ativa. Confira com
`getfacl /dev/uinput` — deve haver uma linha `user:<você>:rw-`. Se não houver:
```bash
sudo usermod -aG input $USER
# Refaça o login
```

## Uso

```bash
python3 painel.py        # painel visual; [J] liga a saída para o jogo
```

### Atalhos no painel
| Tecla | Ação |
|-------|------|
| `TAB` | Abre/fecha Central de Configuração |
| `1`–`5` | Seleciona dedo (1=Pol, 2=Ind, 3=Med, 4=Ane, 5=Min) |
| `+` / `-` | Aumenta/diminui sensibilidade do dedo selecionado |
| `A` | Troca tecla/ação do dedo selecionado (com TAB aberto) |
| `<` / `>` | Ajusta velocidade máxima (joystick) ou sensibilidade (relativo) |
| `Z` | Ajusta zona morta do mouse |
| `M` | Alterna perfil (DIRETO ↔ HÍBRIDO) |
| `C` / `R` | Recentraliza repouso e reseta rastreador |
| `J` | Liga/desliga saída uinput (Modo Jogo) |
| `Q` | Sai |

O painel abre em **modo visual**, sem tocar no cursor. Calibre olhando as
barras de flexão antes de ligar o `[J]`.

### Opções de linha de comando
```bash
python3 painel.py -s          # Usa mão sintética (sem câmera, para testes)
python3 painel.py -j          # Ativa uinput imediatamente
python3 painel.py -g          # Grava vídeo duplo (limpo + anotado) em telemetria/
python3 painel.py -c 1        # Usa câmera índice 1
python3 painel.py --sem-rosto # Desativa rastreamento facial
python3 painel.py --largura 1280 --altura 720  # Resolução personalizada
```

## Verificação

```bash
python3 testar_nucleo.py   # núcleo com mão sintética, sem câmera. Sai 1 se falhar.
python3 prova_saida.py     # cria os 2 dispositivos virtuais e desenha um quadrado
python3 prova_saida.py -t  # também testa envio de teclas (W A S D E F)
python3 perfilar.py        # onde vai o tempo de cada quadro (profiling)
```

`testar_nucleo.py` compara os perfis contra uma **tabela fixada à mão**.
Mudou um mapeamento? Ele reprova até a tabela ser atualizada de propósito —
a versão anterior derivava a expectativa do próprio perfil e passava com
qualquer alteração.

## Diagnóstico

- `logs/rastreador.log` — uma linha por segundo: fps, ms por quadro, taxa de
  perda, redetecções, score, tamanho da região de interesse.
- `telemetria/` — CSV a 10 Hz, log de eventos e um resumo por sessão com picos
  de flexão e recomendação de calibração.

Os dois são complementares: o primeiro diz se o **rastreador** está saudável,
o segundo diz como a **sua mão** está se comportando.

## Arquitetura dos módulos (`livre/`)

| Módulo | Responsabilidade |
|--------|------------------|
| `camera.py` | Captura, ROI rotacionada, dois estágios ONNX (palma → marcos) |
| `rosto.py` | Detecção facial + 468 marcos → EAR/MAR → gestos faciais |
| `biomecanica.py` | Ângulos articulares, encurtamento de falange, oposição do polegar |
| `filtros.py` | Histerese, OneEuro, mediana móvel, velocidade |
| `perfis.py` | Templates de mapeamento + limiares (dados puros) |
| `mapeamento.py` | Núcleo: marcos → ações (exclusividade W/S, punho, desacoplamento) |
| `config.py` | Persistência JSON + ciclo de ações por dedo |
| `saida.py` | Dois UInput (mouse + teclado) + 3 redes de segurança |
| `fonte.py` | `MaoSintetica` para testes sem câmera |
| `telemetria.py` | CSV 10 Hz + eventos + resumo de sessão |

## Limites conhecidos

- A câmera entrega 30 fps → piso de latência de captura ~33 ms. Por isso a
  mira usa **controle de velocidade**, não de posição.
- No GNOME sobre Wayland o painel é uma janela comum: fica coberto por jogos
  em fullscreen exclusivo. Janela sem borda ou segundo monitor resolvem.
- Apoie cotovelo e antebraço na mesa. Braço suspenso cansa rápido, e um quadro
  menor deixa a mão maior na imagem, o que estabiliza o rastreamento.
- Iluminação forte e uniforme melhora drasticamente a detecção da palma.
- Modelos ONNX são do repositório keijiro (MediaPipe → ONNX); não redistrobuídos
  no código — `baixar_modelos.sh` baixa na primeira execução.
opencode
opencode