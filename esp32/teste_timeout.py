import json, time, paho.mqtt.client as mqtt

BROKER, PORT, RM = "broker.hivemq.com", 1883, "rm563567"
T_ST, T_CEN = f"motiva/{RM}/status", f"motiva/{RM}/central"

def on_msg(c, u, m):
    d = json.loads(m.payload)
    print(f"  <- {d.get('tipo_msg')} estado={d.get('estado')} {d.get('motivo', '')}")

c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="motiva-rm563567-teste-timeout")
c.on_message = on_msg
c.connect(BROKER, PORT); c.subscribe(T_ST, qos=1); c.loop_start()

def central(online):
    c.publish(T_CEN, json.dumps({"v": 1, "tipo_msg": "HEARTBEAT" if online else "LWT",
                                 "dispositivo": "central", "online": online}))

print("Fase 1 (20 s): Central enviando heartbeat. O ESP32 deve ficar verde (NORMAL).")
for _ in range(10):
    central(True); time.sleep(2)

print("\nFase 2 (80 s): heartbeat da Central PARADO.")
print("O ESP32 deve entrar em FALHA (LED magenta, [FALHA] ATIVA) cerca de 30 s depois,")
print("contados no relogio do Wokwi, sem aparecer '[MQTT] conectando' antes.")
inicio = time.time()
while time.time() - inicio < 80:
    time.sleep(10)
    print(f"  ... {int(time.time() - inicio)} s sem heartbeat")

print("\nFase 3: heartbeat retomado. O ESP32 deve sair de FALHA e publicar SYNC.")
for _ in range(5):
    central(True); time.sleep(2)
c.loop_stop()
