# Módulo ESP32 (Fernando)

Sinalização da via: ESP32 simulado no Wokwi com LED RGB e buzzer, controlado por MQTT conforme o contrato v0.2 (`docs/contrato_mqtt.md`).

## Arquivos
| Arquivo | Para que serve |
|---|---|
| `sketch.ino` | Firmware do ESP32 (configuração no bloco `CONFIGURACAO` do topo) |
| `diagram.json` | Esquema de ligação do Wokwi (ESP32 + LED RGB + buzzer) |
| `libraries.txt` | Dependências com versões: PubSubClient 2.8 e ArduinoJson 7.0.4 |
| `test_mqtt.py` | Simula a Central e testa duplicata, seq antigo, SINCRONIZAR, FALHA |

## Como executar
1. Em wokwi.com, crie um projeto **ESP32** e substitua `sketch.ino`, `diagram.json` e `libraries.txt` pelos arquivos desta pasta.
2. Clique em Play. O Serial deve mostrar `[MQTT] conectado`.
3. Com a Central (Node-RED) ligada, ou rodando `pip install "paho-mqtt>=2.0"` e `python test_mqtt.py`, o LED deve ficar verde.

## Estados
NORMAL (verde fixo) · SUSPEITA (amarelo piscando) · CONFIRMADO (vermelho piscando + buzzer) · FALHA (magenta piscando).

## Observações
- Só dados fictícios trafegam no broker público. Não há credenciais no código.
- Link do projeto Wokwi: https://wokwi.com/projects/477268846467602433
- Resultados e evidências dos testes: `RELATORIO_ESP32.md` e `evidencias/` (nesta pasta).
