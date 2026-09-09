"""Gravador e gerenciador de telemetria em disco.

Salva automaticamente na pasta 'telemetria/':
  1. dados_<timestamp>.csv   -> métricas contínuas a 10 Hz (flexões, velocidades, teclas)
  2. eventos_<timestamp>.log -> registro de cada clique, tecla e ajuste de sensibilidade
  3. resumo_<timestamp>.txt  -> relatório ao final da sessão com estatísticas e calibração
"""

import os
import time
import csv
from datetime import datetime


class GravadorTelemetria:
    def __init__(self, diretorio_base=None, taxa_hz=10.0, gravar_video=True, largura=640, altura=480, fps_video=30.0):
        if diretorio_base is None:
            raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            diretorio_base = os.path.join(raiz, "telemetria")

        self.pasta = diretorio_base
        os.makedirs(self.pasta, exist_ok=True)

        self.taxa_hz = taxa_hz
        self.intervalo_gravacao = 1.0 / taxa_hz
        self._ultimo_registro_csv = 0.0

        # Identificador único da sessão
        self.id_sessao = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.caminho_csv = os.path.join(self.pasta, f"dados_{self.id_sessao}.csv")
        self.caminho_log = os.path.join(self.pasta, f"eventos_{self.id_sessao}.log")
        self.caminho_resumo = os.path.join(self.pasta, f"resumo_{self.id_sessao}.txt")
        self.caminho_video_limpo = os.path.join(self.pasta, f"video_limpo_{self.id_sessao}.mp4")
        self.caminho_video_anotado = os.path.join(self.pasta, f"video_anotado_{self.id_sessao}.mp4")

        # Abre arquivos com flush imediato
        self._f_log = open(self.caminho_log, "w", encoding="utf-8", buffering=1)
        self._f_csv = open(self.caminho_csv, "w", encoding="utf-8", newline="", buffering=1)
        self._csv_writer = csv.writer(self._f_csv)

        # Inicialização dos gravadores de vídeo duplo (limpo + anotado)
        self.gravar_video = gravar_video
        self._writer_limpo = None
        self._writer_anotado = None

        if self.gravar_video:
            try:
                import cv2
                fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                self._writer_limpo = cv2.VideoWriter(self.caminho_video_limpo, fourcc, fps_video, (largura, altura))
                self._writer_anotado = cv2.VideoWriter(self.caminho_video_anotado, fourcc, fps_video, (largura, altura))
                if not self._writer_limpo.isOpened() or not self._writer_anotado.isOpened():
                    self.gravar_video = False
                else:
                    print(f"🎬 Gravação de telemetria em vídeo duplo ativada:")
                    print(f"   • Câmera limpa:  {os.path.basename(self.caminho_video_limpo)}")
                    print(f"   • Tela anotada:  {os.path.basename(self.caminho_video_anotado)}")
            except Exception as e:
                print(f"⚠️ Aviso ao inicializar vídeo duplo: {e}")
                self.gravar_video = False

        # Cabeçalho CSV
        self._csv_writer.writerow([
            "timestamp", "fps", "pol_pct", "ind_pct", "med_pct", "ane_pct", "min_pct",
            "vel_x", "vel_y", "teclas", "botoes", "perfil", "sensibilidade_aciona"
        ])

        # Estatísticas da sessão
        self.t_inicio = time.time()
        self.total_quadros = 0
        self.quadros_com_mao = 0
        self.picos_flexao = {"polegar": 0.0, "indicador": 0.0, "medio": 0.0, "anelar": 0.0, "mindinho": 0.0}
        self.contagem_acoes = {}

        self.registrar_evento("Sessão de telemetria iniciada", categoria="SISTEMA")
        print(f"📁 Telemetrias sendo gravadas em: {self.pasta}")

    def registrar_evento(self, texto, categoria="ACAO"):
        """Grava uma linha descritiva no arquivo eventos_<timestamp>.log."""
        agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        linha = f"[{agora}] [{categoria:<7}] {texto}\n"
        self._f_log.write(linha)
        self._f_log.flush()

        # Atualiza estatísticas de contagem se for ação
        if "LIGADO" in texto or "ACIONADO" in texto:
            partes = texto.split("->")
            if len(partes) > 1:
                acao = partes[1].replace("LIGADO", "").replace("ACIONADO", "").strip()
                self.contagem_acoes[acao] = self.contagem_acoes.get(acao, 0) + 1

    def registrar_quadro(self, fps, estado, aciona=0.50):
        """Registra métricas no arquivo CSV em taxa controlada (10 Hz)."""
        t_agora = time.time()
        self.total_quadros += 1

        if estado is not None:
            self.quadros_com_mao += 1
            # Atualiza picos de flexão
            for k, v in estado.flexoes.items():
                if v > self.picos_flexao.get(k, 0.0):
                    self.picos_flexao[k] = v

        # Controle de taxa para não inflar o disco desnecessariamente
        if t_agora - self._ultimo_registro_csv < self.intervalo_gravacao:
            return

        self._ultimo_registro_csv = t_agora
        agora_iso = datetime.now().strftime("%H:%M:%S.%f")[:-3]

        if estado:
            pol = round(estado.flexoes.get("polegar", 0.0) * 100, 1)
            ind = round(estado.flexoes.get("indicador", 0.0) * 100, 1)
            med = round(estado.flexoes.get("medio", 0.0) * 100, 1)
            ane = round(estado.flexoes.get("anelar", 0.0) * 100, 1)
            min_ = round(estado.flexoes.get("mindinho", 0.0) * 100, 1)
            teclas = "+".join(sorted(estado.teclas)) if estado.teclas else ""
            botoes = "+".join(sorted(estado.botoes)) if estado.botoes else ""
            self._csv_writer.writerow([
                agora_iso, round(fps, 1), pol, ind, med, ane, min_,
                round(estado.vel_x, 1), round(estado.vel_y, 1),
                teclas, botoes, estado.perfil, round(aciona, 2)
            ])
        else:
            self._csv_writer.writerow([
                agora_iso, round(fps, 1), 0, 0, 0, 0, 0, 0, 0, "", "", "", round(aciona, 2)
            ])

    def fechar(self):
        """Gera o relatório resumo final da sessão e fecha os arquivos."""
        duracao = max(1.0, time.time() - self.t_inicio)
        minutos = int(duracao // 60)
        segundos = int(duracao % 60)
        fps_medio = self.total_quadros / duracao
        taxa_rastreio = (self.quadros_com_mao / max(1, self.total_quadros)) * 100.0

        self.registrar_evento(f"Sessão encerrada. Duração: {minutos}m {segundos}s", categoria="SISTEMA")

        # Escreve o relatório final
        with open(self.caminho_resumo, "w", encoding="utf-8") as f:
            f.write("=" * 65 + "\n")
            f.write("  ✋ RELATÓRIO DE TELEMETRIA — PROJETO UMA MÃO NO AR\n")
            f.write("=" * 65 + "\n\n")
            f.write(f"ID da Sessão:      {self.id_sessao}\n")
            f.write(f"Data e Hora:       {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n")
            f.write(f"Duração Total:     {minutos}m {segundos}s ({int(duracao)} segundos)\n")
            f.write(f"FPS Médio:         {fps_medio:.1f} FPS\n")
            f.write(f"Taxa de Rastreio:  {taxa_rastreio:.1f}% dos quadros com mão detectada\n\n")

            f.write("-" * 65 + "\n")
            f.write("📊 PICOS DE FLEXÃO POR DEDO (Alcance Máximo):\n")
            f.write("-" * 65 + "\n")
            for dedo, pico in self.picos_flexao.items():
                f.write(f"  • {dedo.capitalize():<12}: {pico*100:5.1f}% de flexão máxima\n")

            f.write("\n" + "-" * 65 + "\n")
            f.write("⌨️  TOTAL DE AÇÕES DISPARADAS NA SESSÃO:\n")
            f.write("-" * 65 + "\n")
            if self.contagem_acoes:
                for acao, qtd in sorted(self.contagem_acoes.items(), key=lambda x: -x[1]):
                    f.write(f"  • {acao:<18}: {qtd:4d} vezes\n")
            else:
                f.write("  (Nenhuma ação atingiu o limiar durante a sessão)\n")

            f.write("\n" + "-" * 65 + "\n")
            f.write("💡 RECOMENDAÇÃO DE CALIBRAÇÃO PERSONALIZADA:\n")
            f.write("-" * 65 + "\n")
            ind_pico = self.picos_flexao.get("indicador", 0.0)
            med_pico = self.picos_flexao.get("medio", 0.0)
            if ind_pico > 0.30:
                sugestao_ind = max(0.25, ind_pico * 0.70)
                f.write(f"  • Limiar ideal para W (Indicador): cerca de {int(sugestao_ind*100)}%\n")
            if med_pico > 0.30:
                sugestao_med = max(0.25, med_pico * 0.70)
                f.write(f"  • Limiar ideal para S (Médio):     cerca de {int(sugestao_med*100)}%\n")

            f.write("\nArquivos gravados nesta sessão:\n")
            f.write(f"  • Dados brutos CSV:  {os.path.basename(self.caminho_csv)}\n")
            f.write(f"  • Log de eventos:    {os.path.basename(self.caminho_log)}\n")
            f.write(f"  • Este resumo:       {os.path.basename(self.caminho_resumo)}\n")
            f.write("=" * 65 + "\n")

        self._f_log.close()
        self._f_csv.close()
        print(f"\n📄 Relatório de telemetria gerado em:\n   {self.caminho_resumo}")
