"""Teste da fila de pendentes: broker inexistente -> publicar() devolve False e guarda; depois o broker volta.

Usa o topico de teste (motiva/rm563567/teste_visao), nunca o topico real de eventos.
Pode levar ate ~40 s, pois a reconexao do paho tem intervalo crescente.
"""

import json
import threading
import time

import paho.mqtt.client as mqtt

from util import carregar_config
from publicador_mqtt import PublicadorMqtt

BROKER_INEXISTENTE = "broker-inexistente.motiva.invalid"
TOPICO = "motiva/rm563567/teste_visao"


def main():
    config = carregar_config()
    with open("../docs/exemplos/evento.json", encoding="utf-8") as f:
        evento = json.load(f)

    recebido = threading.Event()
    assinante = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="motiva-rm563567-visao-teste")
    assinante.on_connect = lambda c, u, f, rc, p: c.subscribe(TOPICO, qos=1)
    assinante.on_message = lambda c, u, m: recebido.set() if json.loads(m.payload) == evento else None
    assinante.connect(config["mqtt"]["broker"], config["mqtt"]["porta"], keepalive=30)
    assinante.loop_start()

    pub = PublicadorMqtt(config, broker=BROKER_INEXISTENTE, topico=TOPICO)
    print("conectou ao broker inexistente?", pub.conectar(timeout=3))
    resultado = pub.publicar(evento)
    print("publicar() devolveu:", resultado, "| pendentes:", len(pub.pendentes))
    assert resultado is False and len(pub.pendentes) == 1

    print("trocando para o broker real; aguardando a reconexao automatica...")
    pub.cliente.connect_async(config["mqtt"]["broker"], config["mqtt"]["porta"], keepalive=30)
    pub.broker = config["mqtt"]["broker"]
    limite = time.time() + 60
    while len(pub.pendentes) and time.time() < limite:
        time.sleep(0.5)
    print("pendentes apos reconectar:", len(pub.pendentes), "| recebido pelo assinante:", recebido.wait(timeout=10))
    assert len(pub.pendentes) == 0 and recebido.is_set()
    pub.desconectar()
    assinante.loop_stop()
    assinante.disconnect()
    print("Teste passou.")


if __name__ == "__main__":
    main()
