# Contrato MQTT — Motiva Vision (v0.2)

**Status:** congelado em 07/10/2026.
Qualquer mudança deve ser avisada no grupo **antes** e gera uma nova versão (campo `v`).

Responsáveis: Raphael (Visão), Guilherme (Central), Fernando (ESP32).

---

## 1. Broker

| Item | Valor |
|---|---|
| Broker principal | `broker.hivemq.com`, porta `1883` (sem TLS) |
| Plano B | `test.mosquitto.org`, porta `1883` |
| Configuração | o host do broker fica em arquivo de configuração, nunca fixo no código |

**Segurança e conformidade:** o broker é público. Qualquer pessoa pode ler e publicar nos nossos tópicos.
Por isso, trafegam **apenas dados fictícios**, nenhuma imagem e nenhuma informação pessoal.
A possibilidade de terceiros publicarem nos tópicos é registrada como limitação no relatório.

### Client IDs (únicos, para um módulo não derrubar o outro)

| Módulo | client_id |
|---|---|
| Visão | `motiva-rm563567-visao` |
| Central | `motiva-rm563567-central` |
| ESP32 | `motiva-rm563567-esp32` |

Nunca rodar duas instâncias do mesmo módulo ao mesmo tempo.

---

## 2. Tópicos

| Tópico | Publica | Assina | QoS | Retained |
|---|---|---|---|---|
| `motiva/rm563567/eventos` | Visão | Central | 1 | Não |
| `motiva/rm563567/comandos` | Central | ESP32 | 1 | **Não** (evita reexecução ao reconectar) |
| `motiva/rm563567/status` | ESP32 | Central | 1 | Só `HEARTBEAT` e `LWT` |
| `motiva/rm563567/central` | Central | ESP32 | 1 | Sim (`HEARTBEAT` e `LWT`) |

---

## 3. Convenções gerais

- Formato: JSON UTF-8, campos em `snake_case`, valores sem acento.
- Toda mensagem leva `"v": 1` (versão do contrato).
- `timestamp`: ISO-8601 com fuso, ex.: `2026-10-12T14:32:10-03:00`.
- `confianca`: número de 0.00 a 1.00 (adimensional, 2 casas decimais).
- Indicador que não foi possível observar: `null` ou `"desconhecido"`. **Nunca inventar.**

### Formato dos identificadores

| ID | Formato | Exemplo |
|---|---|---|
| `event_id` | `evt-<camera>-<AAAAMMDDTHHMMSS>-<4 hex aleatórios>` | `evt-CAM01-20261012T143210-a3f9` |
| `command_id` | `cmd-<AAAAMMDDTHHMMSS>-<4 hex aleatórios>` | `cmd-20261012T143240-7c21` |

O sufixo aleatório garante que reiniciar um script nunca gere um ID repetido.

---

## 4. Valores permitidos

### `tipo` / `tipo_idx`

| tipo | tipo_idx | Publica evento? |
|---|---|---|
| `SEM_ACIDENTE` | 0 | Não (só log local de testes) |
| `COLISAO_TRASEIRA` | 1 | Sim |
| `COLISAO_LATERAL` | 2 | Sim |
| `ATROPELAMENTO` | 3 | Sim |
| `INDETERMINADO` | 9 | Sim, pedindo revisão humana |

### `nivel_confianca` (limiares provisórios, ajustados pela Visão)

| Nível | Faixa | Resultado |
|---|---|---|
| `ALTO` | confianca ≥ 0.70 | classifica no tipo detectado |
| `MEDIO` | 0.40 ≤ confianca < 0.70 | publica como `INDETERMINADO` |
| `BAIXO` | confianca < 0.40 | `SEM_ACIDENTE` (não publica) |

### Estados

| Campo | Valores |
|---|---|
| `estado` do evento | `SUSPEITA`, `CONFIRMADO`, `REJEITADO`, `ENCERRADO` |
| `estado` do ESP32 | `NORMAL`, `SUSPEITA`, `CONFIRMADO`, `FALHA` (e `OFFLINE` apenas no LWT) |
| `acao` do comando | `SUSPEITA`, `CONFIRMAR`, `REJEITAR`, `ENCERRAR`, `SINCRONIZAR` |
| `estado_alvo` | `NORMAL`, `SUSPEITA`, `CONFIRMADO` |

### Matriz de recomendação (regras fictícias, não constituem protocolo médico)

Toda recomendação é uma **sugestão** até o operador confirmar.
A Visão aplica a matriz e preenche `recomendacao`; a Central exibe a regra e a justificativa.
Prioridade quando mais de uma regra se aplica: **R4 > R3 > R2 > R1**.

| Regra | Condição simulada | `recurso` |
|---|---|---|
| R1 | Acidente detectado, sem indicador adicional de risco | `AVALIACAO_EQUIPE` |
| R2 | Colisão (traseira ou lateral) com indicador adicional de risco (`veiculo_parado_na_pista = true`) | `AMBULANCIA_SIMULADA` |
| R3 | Atropelamento identificado | `RESGATE_PRIORITARIO` |
| R4 | `tipo = INDETERMINADO` (imagem ambígua ou confiança média) | `REVISAO_OPERADOR` |

---

## 5. Mensagens

Exemplos completos em `docs/exemplos/`.

### 5.1 Evento — `eventos` (Visão → Central) — `exemplos/evento.json`

| Campo | Tipo | Descrição |
|---|---|---|
| `v` | int | versão do contrato |
| `event_id` | string | identificador único da ocorrência |
| `revisao` | int | começa em 1; incrementa se a Visão atualizar o mesmo evento |
| `timestamp` | string | data/hora da detecção, com fuso |
| `camera_id` | string | origem, ex.: `CAM01` |
| `local` | string | trecho da rodovia (fictício) |
| `tipo` / `tipo_idx` | string / int | ver seção 4 |
| `confianca` | float | 0.00 a 1.00 |
| `nivel_confianca` | string | `ALTO` ou `MEDIO` |
| `indicadores` | objeto | `veiculos_envolvidos` (int/null), `pessoa_envolvida` (bool/null), `veiculo_parado_na_pista` (bool/null), `faixa_obstruida` (`sim`/`nao`/`desconhecido`) |
| `recomendacao` | objeto | `recurso` e `regra` (seção 4) |
| `estado` | string | a Visão sempre publica `SUSPEITA` |
| `evidencia` | objeto | `video`, `frame`, `tempo_s` e `arquivo`. `arquivo` é o caminho **local** da imagem, no padrão `visao/saida/evidencias/<event_id>.jpg`, **relativo à raiz do repositório** (a Central monta o caminho completo a partir dele). A imagem não trafega pelo broker. |

### 5.2 Comando — `comandos` (Central → ESP32) — `exemplos/comando_confirmar.json`

| Campo | Tipo | Descrição |
|---|---|---|
| `v` | int | versão do contrato |
| `command_id` | string | identificador único do comando |
| `seq` | int (64 bits) | horário da Central em milissegundos (epoch); sempre crescente |
| `event_id` | string/null | evento relacionado (`null` em `SINCRONIZAR`) |
| `acao` | string | ver seção 4 |
| `estado_alvo` | string | estado que o ESP32 deve exibir, calculado pela Central considerando **todas** as ocorrências ativas |

A Central é a única fonte de verdade do estado da sinalização. O ESP32 só aplica `estado_alvo`.

### 5.3 Status — `status` (ESP32 → Central)

| `tipo_msg` | Quando | Retained | Exemplo |
|---|---|---|---|
| `HEARTBEAT` | a cada 5 s | Sim | `exemplos/status_heartbeat.json` |
| `ACK` | ao receber (`fase: RECEBIDO`) e ao aplicar (`fase: APLICADO`) cada comando | Não | `exemplos/status_ack.json` |
| `SYNC` | ao (re)conectar, pedindo o estado atual | Não | `exemplos/status_sync.json` |
| `LWT` | publicado pelo broker se o ESP32 cair | Sim | `exemplos/status_lwt.json` |

O ESP32 não precisa de relógio real: envia `uptime_s` e a Central carimba o horário de recebimento.

### 5.4 Central — `central` (Central → ESP32)

| `tipo_msg` | Quando | Retained | Exemplo |
|---|---|---|---|
| `HEARTBEAT` | a cada 10 s | Sim | `exemplos/central_heartbeat.json` |
| `LWT` | publicado pelo broker se a Central cair | Sim | `exemplos/central_lwt.json` |

---

## 6. Regras de comportamento

### Duplicatas e ordem
- **Eventos:** a Central usa `event_id` como chave. Se já existe, atualiza o registro (quando `revisao` for maior). Evento `ENCERRADO` nunca é reaberto.
- **Comandos:** o ESP32 guarda o último `seq` aplicado. Se chegar `seq` menor ou igual, responde `ACK` com `"duplicado": true` e **não executa de novo**. Isso cobre duplicata do QoS 1, comando atrasado e tentativa de reabrir evento encerrado.

### Fluxo normal
1. Visão publica evento (`estado: SUSPEITA`).
2. Central registra e envia comando `SUSPEITA` automaticamente (sinalização preliminar, visualmente diferente da confirmada).
3. Operador clica **CONFIRMAR** ou **REJEITAR**; a Central registra ação e horário e envia o comando.
4. ESP32 responde `ACK RECEBIDO` e depois `ACK APLICADO`; a Central mostra "enviado" × "confirmado pelo ESP32".
5. Operador clica **ENCERRAR**; o ESP32 volta a `NORMAL` se não houver outra ocorrência ativa.

### Timeouts e falha
| Situação | Comportamento |
|---|---|
| Central sem heartbeat do ESP32 por 15 s (ou LWT recebido) | mostra ESP32 como desconectado |
| ESP32 sem conexão com o broker | estado `FALHA`; reconecta com intervalo crescente, sem travar (`millis()`) |
| ESP32 sem heartbeat da Central por 30 s (ou LWT da Central) | estado `FALHA` |
| ESP32 reconecta | publica `SYNC`; a Central responde com `acao: SINCRONIZAR` e o `estado_alvo` vigente |

### Limites técnicos
- ESP32 (PubSubClient): usar `setBufferSize(512)`; comandos devem ficar abaixo de 512 bytes.
- `seq` deve ser lido como inteiro de 64 bits (`unsigned long long`) no ESP32.

---

## 7. Teste rápido pelo terminal

Publicar um evento falso para testar a Central (sem precisar da Visão):

```bash
mosquitto_pub -h broker.hivemq.com -p 1883 -q 1 \
  -t motiva/rm563567/eventos -f docs/exemplos/evento.json
```

Acompanhar todas as mensagens do grupo:

```bash
mosquitto_sub -h broker.hivemq.com -p 1883 -v -t 'motiva/rm563567/#'
```
