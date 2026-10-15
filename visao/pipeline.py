"""Pipeline completo em uma passada: video -> tracks -> trajetorias -> analise temporal -> classificador -> eventos.

processar_video() e usado por main.py (publicando no MQTT) e por avaliar.py (sem publicar).
Recebe SOMENTE o caminho do video e a config: nao olha o gabarito nem classifica pelo nome do arquivo
(o nome so escolhe camera_id e local do evento, que sao rotulos).
"""

import csv
import json
from collections import deque
from pathlib import Path

import cv2

from analise_temporal import AnalisadorTemporal
from classificador import classificar
from detector_tracker import DetectorTracker
from eventos import GerenciadorEventos
from trajetorias import HistoricoTrack

COR_TRACK = (0, 255, 0)
COR_PAR = (0, 0, 255)


def desenhar(quadro, tracks, pares_destacados=None):
    """Desenha caixa, classe e ID de cada track. Os pares de eventos ativos saem em vermelho com o rotulo."""
    pares_destacados = pares_destacados or {}
    for t in tracks:
        x1, y1, x2, y2 = map(int, t["caixa"])
        destaque = pares_destacados.get(t["id"])
        cor = COR_PAR if destaque else COR_TRACK
        cv2.rectangle(quadro, (x1, y1), (x2, y2), cor, 3 if destaque else 2)
        texto = f'{t["classe"]} #{t["id"]}' + (f" {destaque}" if destaque else "")
        cv2.putText(quadro, texto, (x1, max(y1 - 6, 12)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, cor, 1)
    return quadro


def salvar_evidencia(evento, par, buffer, config):
    """Grava o quadro do contato (com as caixas) em saida/evidencias/<event_id>.jpg. Sobrescreve nas revisoes."""
    frame = evento["evidencia"]["frame"]
    entrada = next((e for e in buffer if e[0] == frame), None)
    if entrada is None:
        return None
    quadro = cv2.imdecode(entrada[1], cv2.IMREAD_COLOR)
    desenhar(quadro, entrada[2], {t: "par" for t in par})
    rotulo = f'{evento["tipo"]} {evento["confianca"]:.2f} ({evento["nivel_confianca"]}) rev {evento["revisao"]}'
    cv2.rectangle(quadro, (0, 0), (len(rotulo) * 11 + 20, 30), (0, 0, 0), -1)
    cv2.putText(quadro, rotulo, (8, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
    pasta = Path(config["saida"]["evidencias"])
    pasta.mkdir(parents=True, exist_ok=True)
    destino = pasta / f'{evento["event_id"]}.jpg'
    cv2.imwrite(str(destino), quadro)
    return destino


def resolver_camera(config, nome_video):
    """camera_id e local do evento para este video (rotulos; nao influenciam a classificacao)."""
    cam = (config.get("cameras") or {}).get(Path(nome_video).stem, {})
    return (cam.get("camera_id", config["eventos"]["camera_padrao"]),
            cam.get("local", config["eventos"]["local_padrao"]))


def processar_video(caminho_video, config, publicador=None, salvar_logs=False, mostrar=False, salvar_video=False):
    """Processa o video quadro a quadro. Devolve dict com tipo, confianca, event_id, instante_s e a lista de eventos.

    publicador: PublicadorMqtt ou None (None = so analisa). Cada evento (e cada revisao) vai para publicador.publicar().
    """
    caminho_video = Path(caminho_video)
    cap = cv2.VideoCapture(str(caminho_video))
    if not cap.isOpened():
        raise RuntimeError(f"nao consegui abrir o video: {caminho_video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    largura, altura = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    cfg_traj = config["trajetorias"]
    detector = DetectorTracker(config)
    analisador = AnalisadorTemporal(config, fps, cfg_traj["min_quadros"])
    camera_id, local = resolver_camera(config, caminho_video.name)
    gerenciador = GerenciadorEventos(config, camera_id, local, caminho_video.name)
    historicos = {}
    buffer = deque(maxlen=int((config["analise_temporal"]["janela_pos_s"] + 1.0) * fps))   # quadros recentes (jpeg)
    maior_sem_evento = 0.0   # maior confianca vista que nao chegou a virar evento (para o log da avaliacao)
    publicados, pares_ativos = [], {}

    arq_csv = escritor = gravador = None
    nome = caminho_video.stem
    if salvar_logs:
        pasta_logs = Path(config["saida"]["logs"])
        pasta_logs.mkdir(parents=True, exist_ok=True)
        arq_csv = open(pasta_logs / f"{nome}_trajetorias.csv", "w", newline="", encoding="utf-8")
        escritor = csv.writer(arq_csv)
        escritor.writerow(["frame", "tempo_s", "track_id", "classe", "conf", "x1", "y1", "x2", "y2"])
    if salvar_video:
        pasta = Path(config["saida"]["pasta"])
        pasta.mkdir(parents=True, exist_ok=True)
        gravador = cv2.VideoWriter(str(pasta / f"{nome}_anotado.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), fps, (largura, altura))

    n = 0
    while True:
        ok, quadro = cap.read()
        if not ok:
            break
        tracks = detector.rastrear(quadro)
        features = []
        for t in tracks:
            if escritor:
                escritor.writerow([n, round(n / fps, 3), t["id"], t["classe"], t["conf"], *t["caixa"]])
            h = historicos.setdefault(t["id"], HistoricoTrack(t["id"], fps, cfg_traj["janela_suavizacao"]))
            features.append(h.adicionar(n, t["classe"], t["caixa"], t["conf"]))
        ok_jpg, jpg = cv2.imencode(".jpg", quadro, [cv2.IMWRITE_JPEG_QUALITY, 90])
        buffer.append((n, jpg, tracks))

        for cand in analisador.processar_quadro(n, features):
            resultado = classificar(cand, config)
            evento = gerenciador.atualizar(cand, resultado, fps)
            if resultado["tipo"] == "SEM_ACIDENTE":
                maior_sem_evento = max(maior_sem_evento, resultado["confianca"])
            else:
                pares_ativos[cand["track_a"]] = pares_ativos[cand["track_b"]] =                     f'{resultado["tipo"][:4]} {resultado["confianca"]:.2f}'
            if evento:
                salvar_evidencia(evento, (cand["track_a"], cand["track_b"]), buffer, config)
                publicados.append(evento)
                print(f'[evento] {evento["event_id"]} rev {evento["revisao"]} {evento["tipo"]} '
                      f'conf {evento["confianca"]:.2f} ({evento["nivel_confianca"]}) quadro {n}')
                if publicador:
                    publicador.publicar(evento)

        if mostrar or gravador:
            desenhar(quadro, tracks, pares_ativos)
        if gravador:
            gravador.write(quadro)
        if mostrar:
            cv2.imshow("Motiva Vision", quadro)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
        n += 1

    cap.release()
    if arq_csv:
        arq_csv.close()
    if gravador:
        gravador.release()
    if mostrar:
        cv2.destroyAllWindows()
    if gerenciador.eventos:   # o acidente de maior confianca do video
        ev = max(gerenciador.eventos, key=lambda e: e["conf_max"])
        melhor = {"tipo": ev["tipo_max"], "confianca": ev["conf_max"], "event_id": ev["event_id"],
                  "instante_s": ev["tempo_contato_s"]}
    else:
        melhor = {"tipo": "SEM_ACIDENTE", "confianca": maior_sem_evento, "event_id": None, "instante_s": None}
    return {**melhor, "eventos": publicados, "quadros": n, "fps": fps}


def salvar_eventos_jsonl(eventos, caminho):
    """Guarda os eventos (um JSON por linha) para conferencia com o contrato."""
    Path(caminho).parent.mkdir(parents=True, exist_ok=True)
    with open(caminho, "w", encoding="utf-8") as f:
        for e in eventos:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
