"""Conexao e publicacao MQTT (QoS 1, reconexao automatica)."""

import json
import time

import paho.mqtt.client as mqtt


class PublicadorMqtt:
    """Publica eventos no broker configurado em config.yaml (contrato MQTT v0.2)."""

    def __init__(self, config, broker=None, topico=None):
        cfg = config["mqtt"]
        self.broker = broker or cfg["broker"]
        self.porta = cfg["porta"]
        self.topico = topico or cfg["topico_eventos"]
        self.qos = cfg["qos"]
        self.conectado = False
        self.cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cfg["client_id"])
        self.cliente.reconnect_delay_set(min_delay=1, max_delay=30)
        self.cliente.on_connect = self._ao_conectar
        self.cliente.on_disconnect = self._ao_desconectar

    def _ao_conectar(self, cliente, userdata, flags, reason_code, properties):
        self.conectado = not reason_code.is_failure
        if self.conectado:
            print(f"[mqtt] conectado a {self.broker}:{self.porta}")
        else:
            print(f"[mqtt] falha ao conectar: {reason_code}")

    def _ao_desconectar(self, cliente, userdata, flags, reason_code, properties):
        self.conectado = False
        print(f"[mqtt] desconectado ({reason_code}); reconectando automaticamente")

    def conectar(self, timeout=10):
        """Conecta e inicia o loop em segundo plano. Devolve True se conectou a tempo."""
        self.cliente.connect_async(self.broker, self.porta, keepalive=30)
        self.cliente.loop_start()
        limite = time.time() + timeout
        while not self.conectado and time.time() < limite:
            time.sleep(0.1)
        return self.conectado

    def publicar(self, evento, timeout=10):
        """Publica o evento (dict) como JSON UTF-8, QoS 1, sem retained. Devolve True se confirmado."""
        info = self.cliente.publish(self.topico, json.dumps(evento, ensure_ascii=False),
                                    qos=self.qos, retain=False)
        info.wait_for_publish(timeout=timeout)
        return info.is_published()

    def desconectar(self):
        """Encerra a conexao e o loop."""
        self.cliente.loop_stop()
        self.cliente.disconnect()
