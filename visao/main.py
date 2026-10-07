"""Ponto de entrada: python main.py --video caminho.mp4 [--mostrar] [--salvar-video]"""

import argparse
import csv
from pathlib import Path

import cv2

from detector_tracker import DetectorTracker, carregar_config


def desenhar(quadro, tracks):
    """Desenha caixa, classe e ID de cada track no quadro."""
    for t in tracks:
        x1, y1, x2, y2 = map(int, t["caixa"])
        cv2.rectangle(quadro, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.putText(quadro, f'{t["classe"]} #{t["id"]}', (x1, max(y1 - 6, 12)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
    return quadro


def main():
    ap = argparse.ArgumentParser(description="Motiva Vision - deteccao e tracking")
    ap.add_argument("--video", required=True, help="caminho do video")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--mostrar", action="store_true", help="abre janela com o resultado (q sai)")
    ap.add_argument("--salvar-video", action="store_true", help="grava video anotado em saida/")
    args = ap.parse_args()

    config = carregar_config(args.config)
    detector = DetectorTracker(config)

    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        raise SystemExit(f"nao consegui abrir o video: {args.video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    largura = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    altura = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    nome = Path(args.video).stem
    pasta_logs = Path(config["saida"]["logs"])
    pasta_logs.mkdir(parents=True, exist_ok=True)
    arq_csv = open(pasta_logs / f"{nome}_trajetorias.csv", "w", newline="", encoding="utf-8")
    escritor = csv.writer(arq_csv)
    escritor.writerow(["frame", "tempo_s", "track_id", "classe", "conf", "x1", "y1", "x2", "y2"])

    gravador = None
    if args.salvar_video:
        destino = Path(config["saida"]["pasta"]) / f"{nome}_anotado.mp4"
        gravador = cv2.VideoWriter(str(destino), cv2.VideoWriter_fourcc(*"mp4v"), fps, (largura, altura))

    n = 0
    while True:
        ok, quadro = cap.read()
        if not ok:
            break
        tracks = detector.rastrear(quadro)
        for t in tracks:
            escritor.writerow([n, round(n / fps, 3), t["id"], t["classe"], t["conf"], *t["caixa"]])
        if args.mostrar or gravador:
            desenhar(quadro, tracks)
        if gravador:
            gravador.write(quadro)
        if args.mostrar:
            cv2.imshow("Motiva Vision", quadro)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
        n += 1

    cap.release()
    arq_csv.close()
    if gravador:
        gravador.release()
    cv2.destroyAllWindows()
    print(f"{n} quadros processados ({fps:.1f} fps). Log em {pasta_logs / (nome + '_trajetorias.csv')}")


if __name__ == "__main__":
    main()
