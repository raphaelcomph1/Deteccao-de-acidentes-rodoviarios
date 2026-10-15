"""Ponto de entrada: python main.py --video caminho.mp4 [--sem-mqtt] [--mostrar] [--salvar-video]

Roda o pipeline completo (deteccao, tracking, analise temporal, classificacao) e publica os eventos
no topico de eventos do broker (contrato MQTT v0.2). Com --sem-mqtt so analisa e imprime os eventos.
Saidas em saida/: logs/<video>_trajetorias.csv, eventos/<video>_eventos.jsonl, evidencias/<event_id>.jpg.
"""

import argparse
from pathlib import Path

from eventos import validar_evento
from pipeline import processar_video, salvar_eventos_jsonl
from util import carregar_config


def main():
    ap = argparse.ArgumentParser(description="Motiva Vision - deteccao de acidentes em video")
    ap.add_argument("--video", required=True, help="caminho do video")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--sem-mqtt", action="store_true", help="so analisa: nao conecta nem publica no broker")
    ap.add_argument("--mostrar", action="store_true", help="abre janela com o resultado (q sai)")
    ap.add_argument("--salvar-video", action="store_true", help="grava video anotado em saida/")
    args = ap.parse_args()

    config = carregar_config(args.config)
    publicador = None
    if not args.sem_mqtt:
        from publicador_mqtt import PublicadorMqtt   # so importa o paho se for publicar
        publicador = PublicadorMqtt(config)
        if not publicador.conectar():
            print("[mqtt] nao conectou ao broker; os eventos ficam pendentes e serao reenviados se a conexao voltar")

    try:
        resultado = processar_video(args.video, config, publicador, salvar_logs=True,
                                    mostrar=args.mostrar, salvar_video=args.salvar_video)
    finally:
        if publicador:
            publicador.desconectar()

    nome = Path(args.video).stem
    caminho_eventos = Path(config["saida"]["pasta"]) / "eventos" / f"{nome}_eventos.jsonl"
    salvar_eventos_jsonl(resultado["eventos"], caminho_eventos)
    for evento in resultado["eventos"]:
        problemas = validar_evento(evento)
        if problemas:
            print(f'[contrato] {evento["event_id"]} rev {evento["revisao"]}: {problemas}')
    print(f'{resultado["quadros"]} quadros ({resultado["fps"]:.1f} fps). Resultado: {resultado["tipo"]} '
          f'(confianca {resultado["confianca"]:.2f}), {len(resultado["eventos"])} mensagem(ns) de evento.')
    print(f"Eventos em {caminho_eventos}")
    if publicador and publicador.pendentes:
        print(f"[mqtt] ATENCAO: {len(publicador.pendentes)} evento(s) nao foram publicados")


if __name__ == "__main__":
    main()
