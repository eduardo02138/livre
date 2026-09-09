#!/usr/bin/env bash
# Baixa os dois modelos ONNX do rastreador de mao.
#
# Sao conversoes ONNX dos modelos MediaPipe (BlazePalm + Hand Landmark)
# publicadas por keijiro. Ficam fora do repositorio para nao redistribuir
# binarios de terceiros junto com este codigo.
set -euo pipefail

destino="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/modelos"
mkdir -p "$destino"

base_palma="https://raw.githubusercontent.com/keijiro/BlazePalmBarracuda/main/Packages/jp.keijiro.mediapipe.blazepalm/ONNX"
base_marcos="https://raw.githubusercontent.com/keijiro/HandLandmarkBarracuda/main/Packages/jp.keijiro.mediapipe.handlandmark/ONNX"

baixar() {
    local url="$1" saida="$2"
    if [ -s "$saida" ]; then
        echo "  ja existe: $(basename "$saida")"
        return
    fi
    echo "  baixando $(basename "$saida")..."
    curl -fsSL -o "$saida" "$url"
}

baixar "$base_palma/palm_detection_barracuda.onnx" "$destino/palm_detection.onnx"
baixar "$base_marcos/hand_landmark.onnx"           "$destino/hand_landmark.onnx"

# Um HTML de erro salvo com extensao .onnx e a falha mais comum aqui, e ela
# so aparece muito depois, como "modelo carregou mas devolve lixo".
for f in "$destino"/*.onnx; do
    if ! head -c 16 "$f" | grep -q "onnx"; then
        echo "ERRO: $f nao parece um ONNX valido (URL mudou?)" >&2
        exit 1
    fi
done

echo
ls -lh "$destino"/*.onnx
echo "modelos prontos."
