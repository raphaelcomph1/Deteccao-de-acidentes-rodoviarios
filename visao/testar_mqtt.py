"""Teste de conexao: publica docs/exemplos/evento.json e confere o recebimento por um segundo cliente.

Por padrao usa um topico de teste, para nao criar evento falso na Central.
Use --real para publicar em motiva/rm563567/eventos (avise o grupo antes).
"""

import argparse
import json
import threading
import time

import paho.mqtt.client as mqtt

from detector_tracker import carregar_config
from eventos import validar_evento
from publicador_mqtt import PublicadorMqtt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--exemplo", default="../docs/exemplos/evento.json")
    ap.add_argument("--broker", help="sobrescreve o broker do config (ex.: test.mosquitto.org)")
    ap.add_argument("--real", action="store_true", help="publica no topico real de eventos")
    args = ap.parse_args()

    config = carregar_config(args.config)
    with open(args.exemplo, encoding="utf-8") as f:
        evento = json.load(f)
    problemas = validar_evento(evento)
    print("[teste] contrato:", "ok" if not problemas else problemas)

    topico = config["mqtt"]["topico_eventos"] if args.real else "motiva/rm563567/teste_visao"
    pub = PublicadorMqtt(config, broker=args.broker, topico=topico)

    recebido = threading.Event()

    def ao_conectar(c, u, f, rc, p):
        c.subscribe(topico, qos=1)

    def ao_receber(c, u, m):
        if json.loads(m.payload) == evento:
            recebido.set()

    assinante = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="motiva-rm563567-visao-teste")
    assinante.on_connect = ao_conectar
    assinante.on_message = ao_receber
    assinante.connect(pub.broker, pub.porta, keepalive=30)
    assinante.loop_start()
    time.sleep(1.5)

    if not pub.conectar():
        raise SystemExit("[teste] nao conectou ao broker")
    print("[teste] publicado:", pub.publicar(evento), "em", topico)
    print("[teste] recebido de volta:", recebido.wait(timeout=8))
    pub.desconectar()
    assinante.loop_stop()
    assinante.disconnect()


if __name__ == "__main__":
    main()
