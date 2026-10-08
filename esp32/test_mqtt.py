import json, random, time, datetime as dt, paho.mqtt.client as mqtt

BROKER, PORT, RM = "broker.hivemq.com", 1883, "rm563567"
T_CMD, T_ST, T_CEN = f"motiva/{RM}/comandos", f"motiva/{RM}/status", f"motiva/{RM}/central"

def on_msg(c, u, m):
    d = json.loads(m.payload); t = d.get("tipo_msg")
    if t == "ACK":
        print(f"  <- ACK fase={d.get('fase'):<9} duplicado={d.get('duplicado')} estado={d.get('estado')} {d.get('motivo','')}")
    elif t != "HEARTBEAT":
        print(f"  <- {t} estado={d.get('estado')} {d.get('motivo','')}")

c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="motiva-rm563567-teste-fernando")
c.on_message = on_msg
c.connect(BROKER, PORT); c.subscribe(T_ST, qos=1); c.loop_start()

hb_on = True; ult_hb = 0.0; ult_seq = [int(time.time() * 1000)]
def central(online):
    c.publish(T_CEN, json.dumps({"v": 1, "tipo_msg": "HEARTBEAT" if online else "LWT",
                                 "dispositivo": "central", "online": online}))
def esperar(s):
    global ult_hb
    for _ in range(int(s)):
        if hb_on and time.time() - ult_hb > 8: central(True); ult_hb = time.time()
        time.sleep(1)
def novo_id(prefixo):
    return f"{prefixo}-{dt.datetime.now():%Y%m%dT%H%M%S}-{random.randrange(65536):04x}"
def cmd(acao, alvo, evt, nota, seq=None, cid=None):
    cid = cid or novo_id("cmd")
    if seq is None: ult_seq[0] += 1; seq = ult_seq[0]
    print(f"\n-> {acao} alvo={alvo} evt={evt} seq={seq}   [{nota}]")
    c.publish(T_CMD, json.dumps({"v": 1, "command_id": cid, "seq": seq, "event_id": evt,
                                 "acao": acao, "estado_alvo": alvo}), qos=1)
    esperar(2)
    return cid, seq

evt = novo_id("evt-CAM01")
esperar(3)
cmd("SUSPEITA", "SUSPEITA", evt, "amarelo piscando")
cid, seq = cmd("CONFIRMAR", "CONFIRMADO", evt, "vermelho + buzzer")
cmd("CONFIRMAR", "CONFIRMADO", evt, "DUPLICADO: mesmo seq, nada muda", seq=seq, cid=cid)
cmd("ENCERRAR", "NORMAL", evt, "volta NORMAL (verde)")
cmd("SUSPEITA", "SUSPEITA", evt, "seq ANTIGO: nao reabre evento encerrado", seq=seq - 1)
evt2 = novo_id("evt-CAM01")
cmd("SUSPEITA", "SUSPEITA", evt2, "novo evento")
cmd("REJEITAR", "NORMAL", evt2, "operador rejeita -> NORMAL")
cmd("SINCRONIZAR", "CONFIRMADO", None, "sincronizacao com event_id null")
cmd("ENCERRAR", "NORMAL", None, "volta NORMAL")
cmd("SUSPEITA", "XYZ", evt2, "RECUSADO: estado_alvo invalido")

print("\nCentral offline (LWT): ESP32 deve ir para FALHA (magenta)...")
hb_on = False; central(False); esperar(8)
print("Central online: ESP32 deve sair de FALHA e publicar SYNC.")
central(True); esperar(5)
print("\nParando heartbeat da Central por 35 s: ESP32 deve entrar em FALHA por timeout...")
hb_on = False; esperar(35)
print("Heartbeat retomado: deve recuperar e publicar SYNC.")
hb_on = True; central(True); esperar(5)
c.loop_stop()
