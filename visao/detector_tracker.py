"""YOLO + ByteTrack: detecta veiculos e pessoas e mantem um ID estavel por objeto."""

from pathlib import Path

import yaml
from ultralytics import YOLO


def carregar_config(caminho="config.yaml"):
    """Le o arquivo de configuracao YAML."""
    with open(caminho, encoding="utf-8") as f:
        return yaml.safe_load(f)


class DetectorTracker:
    """Envolve o YOLO com tracking ByteTrack e devolve os tracks de cada quadro."""

    def __init__(self, config):
        cfg = config["detector"]
        self.classes = {int(k): v for k, v in cfg["classes"].items()}
        self.confianca = cfg["confianca_minima"]
        self.tamanho = cfg["tamanho_imagem"]
        self.tracker = cfg["tracker"]
        # se o arquivo nao existir, o ultralytics baixa o modelo pelo nome
        caminho = Path(cfg["modelo"])
        caminho.parent.mkdir(parents=True, exist_ok=True)
        self.modelo = YOLO(str(caminho) if caminho.exists() else caminho.name)
        if not caminho.exists():
            self.modelo.save(str(caminho))

    def rastrear(self, quadro):
        """Processa um quadro e devolve lista de dicts: id, classe, conf, caixa (x1,y1,x2,y2)."""
        resultado = self.modelo.track(
            quadro,
            persist=True,
            tracker=self.tracker,
            classes=list(self.classes),
            conf=self.confianca,
            imgsz=self.tamanho,
            device="cpu",
            verbose=False,
        )[0]
        tracks = []
        if resultado.boxes is None or resultado.boxes.id is None:
            return tracks
        for caixa, track_id, classe, conf in zip(
            resultado.boxes.xyxy.tolist(),
            resultado.boxes.id.int().tolist(),
            resultado.boxes.cls.int().tolist(),
            resultado.boxes.conf.tolist(),
        ):
            tracks.append(
                {
                    "id": track_id,
                    "classe": self.classes[classe],
                    "conf": round(conf, 3),
                    "caixa": [round(v, 1) for v in caixa],
                }
            )
        return tracks
