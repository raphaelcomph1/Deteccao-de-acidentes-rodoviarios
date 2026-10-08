# Módulo ESP32 e infraestrutura (Fernando)

## 1. Circuito (Wokwi)
ESP32 DevKit + LED RGB cátodo comum (R=GPIO25, G=GPIO26, B=GPIO27, resistores de 220 Ω) + buzzer (GPIO14). Link Wokwi: https://wokwi.com/projects/477268846467602433. Configuração (broker, tópicos, pinos, tempos) concentrada nos `#define` no topo do `sketch.ino`.

## 2. Estados e sinalização
| Estado | Significado | LED RGB | Buzzer |
|---|---|---|---|
| NORMAL | Monitoramento ativo, sem ocorrência | Verde fixo | Off |
| SUSPEITA | Possível acidente, aguardando humano | Amarelo piscando lento (1 s) | Off |
| CONFIRMADO | Operador confirmou | Vermelho piscando rápido (250 ms) | Intermitente |
| FALHA | Sem broker ou Central sem contato | Magenta piscando (500 ms) | Off |

FALHA é uma condição sobreposta: o último estado aplicado é preservado e volta a ser exibido quando a comunicação é restabelecida.

## 3. Como o ESP32 muda de estado
A Central é a única fonte de verdade (contrato v0.2, seção 5.2). O ESP32 não decide: aplica o `estado_alvo` do comando (`NORMAL`, `SUSPEITA` ou `CONFIRMADO`), que a Central calcula considerando todas as ocorrências ativas.

**Encerramento da ocorrência:** o operador clica ENCERRAR (ou REJEITAR) e a Central envia `estado_alvo: NORMAL` se não houver outra ocorrência ativa. O ESP32 não possui timeouts próprios de ocorrência.

**Duplicatas e eventos encerrados:** o ESP32 guarda o último `seq` aplicado (inteiro de 64 bits). Comando com `seq` menor ou igual recebe `ACK` com `duplicado:true` e não é executado. Isso cobre reentrega do QoS 1, comando atrasado e tentativa de reabrir um evento já encerrado.

## 4. Protocolo (parte do ESP32)
- Assina `/comandos` e `/central` (QoS 1). Publica em `/status`. client_id `motiva-rm563567-esp32`; campo `dispositivo:"esp32-01"` nas mensagens.
- ACK (`tipo_msg:"ACK"`): `fase:"RECEBIDO"` ao receber e `fase:"APLICADO"` após aplicar; campos `command_id`, `event_id`, `duplicado`, `estado`, `motivo`.
- HEARTBEAT a cada 5 s, retido. LWT retido: `{"tipo_msg":"LWT","online":false,"estado":"OFFLINE"}`.
- SYNC (não retido) ao (re)conectar e ao sair de FALHA; a Central responde `acao:"SINCRONIZAR"` com o `estado_alvo` vigente.
- Dados fictícios apenas; nenhuma imagem trafega no broker.
- **Desvios documentados:** (a) a biblioteca PubSubClient publica apenas em QoS 0, então ACK/HEARTBEAT/SYNC saem em QoS 0 (assinaturas e LWT em QoS 1); (b) `fase:"RECUSADO"` é usada apenas para comando malformado (JSON inválido, `estado_alvo` inválido, `v` ≠ 1), caso não previsto no contrato.

## 5. Robustez
- WiFi e MQTT não bloqueantes, com reconexão e backoff exponencial (1 s a 15 s), keepalive de 10 s.
- FALHA quando o broker cai, a Central publica LWT, ou há 30 s sem heartbeat da Central.
- Sinalização em task FreeRTOS própria (core 0) com millis(): LED e buzzer continuam responsivos mesmo durante tentativas de conexão.

## 6. Testes
Os testes do ESP32 foram feitos com o `test_mqtt.py`, que simula a Central (heartbeat e comandos no contrato v0.2), com o Wokwi em execução e a Central real desligada. Evidências em `evidencias/`.

| Teste | Esperado | Obtido / evidência |
|---|---|---|
| Viabilidade MQTT (Wokwi + HiveMQ) | ESP32 conecta e troca mensagens com o broker externo | Conectou ao broker.hivemq.com, publicou HEARTBEAT retido em `/status` e recebeu comandos em `/comandos`. Evidência: `boot_sync.png`, `serial_wokwi.txt` |
| SUSPEITA → CONFIRMADO → NORMAL | Cores corretas, 2 ACKs por comando | Cada comando gerou `ACK RECEBIDO` e `ACK APLICADO`. Estados NORMAL → SUSPEITA → CONFIRMADO → NORMAL. LED amarelo em SUSPEITA, vermelho com buzzer em CONFIRMADO, verde em NORMAL. Evidência: `suspeita.png`, `confirmado_sincronizar.png`, `boot_sync.png`, `teste_terminal.txt` |
| Comando duplicado (mesmo seq) | `duplicado:true`, nada muda | O reenvio recebeu só `ACK RECEBIDO` com `duplicado=True` (`seq_ja_aplicado`), sem `APLICADO`. Estado seguiu CONFIRMADO. Evidência: `teste_terminal.txt`, `serial_wokwi.txt` |
| Reabrir evento encerrado (seq antigo) | `duplicado:true`, segue NORMAL | SUSPEITA com seq já usado, após o ENCERRAR, recebeu `ACK RECEBIDO` com `duplicado=True`. Estado seguiu NORMAL e o evento não reabriu. Evidência: `teste_terminal.txt` |
| REJEITAR | Volta NORMAL | Em SUSPEITA, REJEITAR devolveu `ACK APLICADO` com estado NORMAL. Evidência: `teste_terminal.txt` |
| SINCRONIZAR com event_id null | Aplica estado_alvo | Comando com `event_id: null` foi aceito e o ESP32 foi a CONFIRMADO (LED vermelho e buzzer). O ENCERRAR seguinte voltou a NORMAL. Evidência: `confirmado_sincronizar.png`, `teste_terminal.txt` |
| `estado_alvo` inválido | `fase:RECUSADO` | Comando com `estado_alvo: XYZ` recebeu `ACK RECUSADO` (`estado_alvo_invalido`) e o estado não mudou. Evidência: `teste_terminal.txt` |
| Queda de conexão com o broker | FALHA e recuperação | Serial registrou `[FALHA] ATIVA`, `[MQTT] conectando...` e `[MQTT] conectado`, com LED magenta. Reconectou sozinho. Evidência: `falha_reconexao.png` |
| Central offline (LWT) | FALHA e recuperação com SYNC | PENDENTE: repetir com o `test_mqtt.py` atualizado (heartbeat pausado) e registrar o LED magenta e o `SYNC`. |
| Central sem heartbeat 30 s | FALHA por timeout e recuperação | PENDENTE: repetir e registrar `[FALHA] ATIVA` por volta de 30 s. |
| Broker indisponível (host inválido em BROKER_HOST) | FALHA e recuperação | PENDENTE: teste controlado com host inválido e depois restaurado. |
| Integração completa | Vídeo, MQTT, Central, ESP32 e confirmação de execução | PENDENTE: integração de 12/10 com a Central e a Visão. |

## 7. Limitações observadas
- O broker é público e sem autenticação: qualquer pessoa pode publicar nos tópicos do grupo. Por isso só trafegam dados fictícios.
- Nas rodadas de teste apareceu um `LWT estado=OFFLINE` durante a janela de 35 s sem heartbeat: o ESP32 perdeu a conexão com o broker e reconectou. Causa não identificada (instabilidade do broker público ou do Wokwi).
- O `ACK RECEBIDO` de um comando novo traz o `seq` do último comando aplicado, e só o `ACK APLICADO` traz o `seq` do comando atual. A Central associa os ACKs pelo `command_id`, então não há efeito prático.
- A biblioteca PubSubClient publica apenas em QoS 0.
- O ESP32 não tem relógio real: envia `uptime_s` e a Central carimba o horário.
