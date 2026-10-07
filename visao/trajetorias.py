"""Trajetorias por track: posicao, tamanho, velocidade e direcao suavizadas por media movel.

Uso: python trajetorias.py --video caminho.mp4 [--csv saida/logs/<video>_trajetorias.csv]
O CSV de entrada e gerado por main.py. Saidas (em saida/logs/):
  <video>_features.csv  e  <video>_trajetorias.mp4 (rastro + vetor de velocidade)

Convencoes:
- Coordenadas em pixels, origem no canto superior esquerdo, y cresce para baixo.
- direcao_graus: 0 = direita, 90 = para baixo, 180 = esquerda, -90 = para cima.
- velocidade_px_s usa o FPS real do video; velocidade_norm divide pela escala da caixa
  (raiz da area, suavizada), em "tamanhos de caixa por segundo", para reduzir o efeito de perspectiva.
- A media movel usa so quadros anteriores (nao olha o futuro), entao funciona tambem em tempo real,
  ao custo de um pequeno atraso.
"""

import argparse
import csv
import math
from collections import defaultdict, deque
from pathlib import Path

import cv2

from detector_tracker import carregar_config

CAMPOS = ["frame", "tempo_s", "track_id", "classe", "cx", "cy", "largura", "altura",
          "vx", "vy", "velocidade_px_s", "velocidade_norm", "direcao_graus"]


def ler_tracks(caminho_csv):
    """Le o CSV de main.py e agrupa as linhas por track_id (em ordem de quadro)."""
    por_track = defaultdict(list)
    with open(caminho_csv, encoding="utf-8") as f:
        for linha in csv.DictReader(f):
            por_track[int(linha["track_id"])].append(linha)
    for linhas in por_track.values():
        linhas.sort(key=lambda l: int(l["frame"]))
    return por_track


def media_movel(valores, janela):
    """Media de cada valor com os (janela - 1) anteriores."""
    recentes = deque(maxlen=janela)
    saida = []
    for v in valores:
        recentes.append(v)
        saida.append(sum(recentes) / len(recentes))
    return saida


def calcular_track(linhas, fps, janela):
    """Calcula as features de um track. Devolve lista de dicts (um por quadro)."""
    quadros = [int(l["frame"]) for l in linhas]
    x1, y1, x2, y2 = (media_movel([float(l[k]) for l in linhas], janela) for k in ("x1", "y1", "x2", "y2"))
    cx = [(a + b) / 2 for a, b in zip(x1, x2)]
    cy = [(a + b) / 2 for a, b in zip(y1, y2)]
    larg = [b - a for a, b in zip(x1, x2)]
    alt = [b - a for a, b in zip(y1, y2)]

    features = []
    for i, l in enumerate(linhas):
        vx = vy = vel = vel_norm = 0.0
        direcao = ""
        if i > 0:
            dt = (quadros[i] - quadros[i - 1]) / fps
            vx = (cx[i] - cx[i - 1]) / dt
            vy = (cy[i] - cy[i - 1]) / dt
            vel = math.hypot(vx, vy)
            escala = math.sqrt(max(larg[i] * alt[i], 1.0))
            vel_norm = vel / escala
            if vel > 0:
                direcao = round(math.degrees(math.atan2(vy, vx)), 1)
        features.append({
            "frame": quadros[i], "tempo_s": round(quadros[i] / fps, 3),
            "track_id": int(l["track_id"]), "classe": l["classe"],
            "cx": round(cx[i], 1), "cy": round(cy[i], 1),
            "largura": round(larg[i], 1), "altura": round(alt[i], 1),
            "vx": round(vx, 1), "vy": round(vy, 1),
            "velocidade_px_s": round(vel, 1), "velocidade_norm": round(vel_norm, 3),
            "direcao_graus": direcao,
        })
    return features


def calcular_todos(por_track, fps, janela, min_quadros):
    """Calcula as features de todos os tracks, descartando os muito curtos."""
    resultado = {}
    for track_id, linhas in por_track.items():
        if len(linhas) >= min_quadros:
            resultado[track_id] = calcular_track(linhas, fps, janela)
    return resultado


def salvar_csv(features_por_track, caminho):
    """Grava todas as features em um CSV ordenado por quadro e track."""
    linhas = [f for fs in features_por_track.values() for f in fs]
    linhas.sort(key=lambda f: (f["frame"], f["track_id"]))
    with open(caminho, "w", newline="", encoding="utf-8") as arq:
        escritor = csv.DictWriter(arq, fieldnames=CAMPOS)
        escritor.writeheader()
        escritor.writerows(linhas)


def gerar_video(caminho_video, features_por_track, destino, rastro_quadros, escala_vetor_s):
    """Desenha, por track, o rastro do centro e o vetor de velocidade (deslocamento previsto em escala_vetor_s)."""
    cap = cv2.VideoCapture(str(caminho_video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    largura = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    altura = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    gravador = cv2.VideoWriter(str(destino), cv2.VideoWriter_fourcc(*"mp4v"), fps, (largura, altura))

    por_quadro = defaultdict(list)
    for fs in features_por_track.values():
        for f in fs:
            por_quadro[f["frame"]].append(f)
    rastros = defaultdict(lambda: deque(maxlen=rastro_quadros))

    n = 0
    while True:
        ok, quadro = cap.read()
        if not ok:
            break
        for f in por_quadro.get(n, []):
            cor = ((f["track_id"] * 67) % 256, (f["track_id"] * 131) % 256, 255 - (f["track_id"] * 41) % 256)
            centro = (int(f["cx"]), int(f["cy"]))
            rastros[f["track_id"]].append(centro)
            pontos = list(rastros[f["track_id"]])
            for a, b in zip(pontos, pontos[1:]):
                cv2.line(quadro, a, b, cor, 2)
            fim = (int(f["cx"] + f["vx"] * escala_vetor_s), int(f["cy"] + f["vy"] * escala_vetor_s))
            cv2.arrowedLine(quadro, centro, fim, (0, 0, 255), 3, tipLength=0.25)
            texto = f'#{f["track_id"]} {f["velocidade_px_s"]:.0f}px/s ({f["velocidade_norm"]:.2f}/s)'
            cv2.putText(quadro, texto, (centro[0] + 6, centro[1] - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, cor, 2)
        gravador.write(quadro)
        n += 1
    cap.release()
    gravador.release()


def main():
    ap = argparse.ArgumentParser(description="Features de trajetoria por track")
    ap.add_argument("--video", required=True)
    ap.add_argument("--csv", help="CSV de main.py (padrao: saida/logs/<video>_trajetorias.csv)")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    config = carregar_config(args.config)
    cfg = config["trajetorias"]
    pasta_logs = Path(config["saida"]["logs"])
    nome = Path(args.video).stem
    csv_entrada = Path(args.csv) if args.csv else pasta_logs / f"{nome}_trajetorias.csv"
    if not csv_entrada.exists():
        raise SystemExit(f"nao achei {csv_entrada}; rode antes: python main.py --video {args.video}")

    cap = cv2.VideoCapture(args.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.release()

    por_track = ler_tracks(csv_entrada)
    features = calcular_todos(por_track, fps, cfg["janela_suavizacao"], cfg["min_quadros"])
    descartados = len(por_track) - len(features)

    saida_csv = pasta_logs / f"{nome}_features.csv"
    saida_video = pasta_logs / f"{nome}_trajetorias.mp4"
    salvar_csv(features, saida_csv)
    gerar_video(args.video, features, saida_video, cfg["rastro_quadros"], cfg["escala_vetor_s"])
    print(f"{len(features)} tracks mantidos, {descartados} descartados (< {cfg['min_quadros']} quadros), FPS {fps:.1f}")
    print(f"CSV: {saida_csv}\nVideo: {saida_video}")


if __name__ == "__main__":
    main()
