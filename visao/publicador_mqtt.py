"""Conexao e publicacao MQTT (QoS 1, reconexao automatica, fila local de pendentes)."""

import json
import threading
import time
from collections import deque

import paho.mqtt.client as mqtt


class PublicadorMqtt:
    """Publica eventos no broker configurado em config.yaml (contrato MQTT v0.2).

    Se o broker estiver fora, publicar() nao derruba o programa: devolve False e guarda o evento
    numa fila em memoria, que e reenviada quando a conexao voltar. Em raros casos (confirmacao que
    demora e a conexao cai) um evento pode chegar duas vezes; a Central deduplica por event_id e revisao.
    A fila vive so na memoria: se o programa for encerrado, os pendentes se perdem.
    """

    def __init__(self, config, broker=None, topico=None):
        cfg = config["mqtt"]
        self.broker = broker or cfg["broker"]
        self.porta = cfg["porta"]
        self.topico = topico or cfg["topico_eventos"]
        self.qos = cfg["qos"]
        self.conectado = False
        self.pendentes = deque()
        self._trava = threading.Lock()
        self.cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cfg["client_id"])
        self.cliente.reconnect_delay_set(min_delay=1, max_delay=30)
        self.cliente.on_connect = self._ao_conectar
        self.cliente.on_disconnect = self._ao_desconectar

    def _ao_conectar(self, cliente, userdata, flags, reason_code, properties):
        self.conectado = not reason_code.is_failure
        if self.conectado:
            print(f"[mqtt] conectado a {self.broker}:{self.porta}")
            self._reenviar_pendentes()
        else:
            print(f"[mqtt] falha ao conectar: {reason_code}")

    def _ao_desconectar(self, cliente, userdata, flags, reason_code, properties):
        self.conectado = False
        print(f"[mqtt] desconectado ({reason_code}); reconectando automaticamente")

    def _guardar_pendente(self, evento):
        """Coloca o evento na fila local e avisa no terminal."""
        with self._trava:
            self.pendentes.append(evento)
            total = len(self.pendentes)
        print(f"[mqtt] evento {evento.get('event_id')} ficou PENDENTE ({total} na fila)")

    def _reenviar_pendentes(self):
        """Reenvia a fila quando a conexao volta. Roda no callback de conexao, por isso nao espera confirmacao."""
        with self._trava:
            fila, self.pendentes = list(self.pendentes), deque()
        for i, evento in enumerate(fila):
            try:
                info = self.cliente.publish(self.topico, json.dumps(evento, ensure_ascii=False),
                                            qos=self.qos, retain=False)
                enviado = info.rc == mqtt.MQTT_ERR_SUCCESS
            except (ValueError, RuntimeError):
                enviado = False
            if enviado:
                print(f"[mqtt] evento {evento.get('event_id')} REENVIADO")
            else:
                with self._trava:  # devolve este e os restantes, na ordem original
                    self.pendentes.extendleft(reversed(fila[i:]))
                print(f"[mqtt] reenvio interrompido; {len(fila) - i} evento(s) continuam pendentes")
                break

    def conectar(self, timeout=10):
        """Inicia a conexao em segundo plano. Devolve True se conectou a tempo (senao continua tentando)."""
        self.cliente.connect_async(self.broker, self.porta, keepalive=30)
        self.cliente.loop_start()
        limite = time.time() + timeout
        while not self.conectado and time.time() < limite:
            time.sleep(0.1)
        return self.conectado

    def publicar(self, evento, timeout=10):
        """Publica o evento (dict) como JSON UTF-8, QoS 1, sem retained.

        Devolve True se o broker confirmou. Se nao conseguir, guarda o evento na fila local e devolve False.
        """
        if not self.conectado:  # evita a fila interna do paho, que duplicaria o reenvio
            self._guardar_pendente(evento)
            return False
        try:
            info = self.cliente.publish(self.topico, json.dumps(evento, ensure_ascii=False),
                                        qos=self.qos, retain=False)
            info.wait_for_publish(timeout=timeout)
            publicado = info.is_published()
        except (RuntimeError, ValueError):
            publicado = False
        if not publicado:
            self._guardar_pendente(evento)
        return publicado

    def desconectar(self):
        """Encerra a conexao e o loop."""
        self.cliente.loop_stop()
        self.cliente.disconnect()
