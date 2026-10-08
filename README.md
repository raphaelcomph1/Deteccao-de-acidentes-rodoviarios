# Motiva Vision — CP3 FIAP

Protótipo acadêmico que analisa vídeos de rodovia, detecta possíveis acidentes, classifica o tipo
e envia o evento a uma central, que aciona uma sinalização simulada. É uma **simulação**: os dados são
fictícios, não há diagnóstico médico nem acionamento de serviços reais, e toda decisão de atendimento
fica com o operador humano.

O enunciado completo está em `docs/CP3-MORGS.pdf`. Prazo de entrega: 16/10/2026.

## Objetivo

Dado um vídeo de rodovia, o sistema:

1. detecta veículos e pessoas e acompanha cada um ao longo dos quadros;
2. identifica indícios de acidente a partir do **movimento ao longo do tempo** (não só da sobreposição de caixas);
3. classifica o evento em `COLISAO_TRASEIRA`, `COLISAO_LATERAL`, `ATROPELAMENTO` ou `INDETERMINADO`
   (quando não há evidência suficiente, pede revisão humana);
4. publica o evento via MQTT para a central, onde um operador confirma ou rejeita;
5. aciona a sinalização em um ESP32 (Wokwi), que devolve um ACK à central.

## Arquitetura

```
vídeo → visão computacional → MQTT → central (operador confirma/rejeita) → ESP32 → ACK de volta à central
         (/visao)                     (/central, Node-RED)                  (/esp32, Wokwi)
```

Broker: `broker.hivemq.com:1883` (plano B: `test.mosquitto.org:1883`), configurável. Tópicos sob
`motiva/rm563567/` (`eventos`, `comandos`, `status`, `central`). O formato das mensagens está no
contrato `docs/contrato_mqtt.md` (v0.2, congelado), com exemplos em `docs/exemplos/`.
O broker é público: trafegam apenas dados fictícios e nenhuma imagem.

## Estrutura de pastas

```
.
├── Agents.md                # instruções para agentes de código (regras do projeto e checklist)
├── README.md
├── visao/                   # módulo de visão computacional
│   ├── main.py              # YOLO + ByteTrack em um vídeo; gera o CSV de trajetórias
│   ├── trajetorias.py       # velocidade e direção por track (CSV + vídeo de conferência)
│   ├── eventos.py           # event_id, matriz de recomendação, montagem e validação do evento
│   ├── publicador_mqtt.py   # publicação MQTT (QoS 1, reconexão, fila de pendentes)
│   ├── avaliar.py           # compara o resultado com dados/gabarito.csv
│   ├── detector_tracker.py  # envolve o YOLO com o tracking ByteTrack
│   ├── util.py              # leitura do config.yaml
│   ├── config.yaml          # modelo, tópicos, limiares, parâmetros das trajetórias
│   ├── requirements.txt     # versões fixadas
│   ├── testar_mqtt.py, testar_mqtt_offline.py, testar_avaliar.py   # testes
│   └── saida/               # (ignorada pelo git) logs, evidências e resultados
├── central/                 # Node-RED + dashboard (em construção)
├── esp32/                   # firmware do ESP32 no Wokwi (em construção)
├── dados/                   # gabarito.csv; dados/dev/ para vídeos de desenvolvimento (ignorada pelo git)
├── videos/                  # vídeos (ignorada pelo git; vídeos nunca são commitados)
└── docs/                    # enunciado, contrato MQTT, exemplos de mensagens, vídeos de desenvolvimento
```

## Integrantes

| Nome | RM | Módulo |
|---|---|---|
| Raphael Mischiatti | 563567 | Visão computacional (`/visao`) |
| Guilherme Rezende | 563500 | Central Node-RED + dashboard (`/central`) |
| Fernando Caire | 563415 | ESP32 no Wokwi (`/esp32`) |

## Módulo de visão

### Instalação

Requer **Python 3.11**. Os comandos abaixo são para o PowerShell (Windows); no Linux/macOS, use
`python3.11` e `source .venv/bin/activate`.

```powershell
cd visao
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

O modelo `yolo11n.pt` é baixado automaticamente pelo ultralytics na primeira execução do `main.py`
e salvo em `visao/modelos/` (ignorado pelo git).

**Observação sobre o torch (CPU) no Linux:** o `pip` do Linux costuma baixar o `torch` com suporte a CUDA,
que é muito grande. O projeto roda em CPU; para evitar esse download, instale antes a versão CPU e depois
os requisitos:

```bash
pip install torch==2.14.1 torchvision==0.29.1 --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
```

No Windows, o `pip` já instala o `torch` na versão CPU (foi o que usamos). A instalação no Linux acima
**não foi testada** neste projeto.

### Como rodar

Todos os comandos são executados dentro de `visao/`, com o ambiente virtual ativo.

**`main.py`** — detecta e rastreia veículos e pessoas em um vídeo e grava o CSV de trajetórias brutas
em `saida/logs/<video>_trajetorias.csv`.

```powershell
python main.py --video ..\videos\video.mp4
python main.py --video ..\videos\video.mp4 --mostrar          # abre janela com os IDs (q sai)
python main.py --video ..\videos\video.mp4 --salvar-video     # grava vídeo anotado em saida/
```

**`trajetorias.py`** — a partir do CSV do `main.py`, calcula posição, tamanho da caixa, velocidade
(px/s e normalizada pelo tamanho da caixa) e direção, com média móvel. Descarta tracks muito curtos.
Gera `saida/logs/<video>_features.csv` e `saida/logs/<video>_trajetorias.mp4` (rastro e vetor de
velocidade, para conferir os valores). Rode o `main.py` antes.

```powershell
python trajetorias.py --video ..\videos\video.mp4
```

**`testar_mqtt.py`** — teste de conexão: valida o `docs/exemplos/evento.json` contra o contrato, publica no
broker e confere o recebimento por um segundo cliente. Por padrão usa o tópico de teste
`motiva/rm563567/teste_visao`; `--real` publica em `motiva/rm563567/eventos` (avise o grupo antes) e
`--broker test.mosquitto.org` usa o plano B.

```powershell
python testar_mqtt.py
python testar_mqtt_offline.py     # testa a fila de pendentes com um broker inexistente
```

**`avaliar.py`** — roda o pipeline nos vídeos citados em `dados/gabarito.csv` e compara com o rótulo
esperado. É o único arquivo autorizado a ler o gabarito. Gera `saida/resultados/resultados_<conjunto>_<data>.csv`
e imprime acertos, falsos positivos, falsos negativos, classificações erradas, indeterminados, precisão e recall.

```powershell
python avaliar.py --conjunto AJUSTE
python avaliar.py --conjunto RESERVADO    # só para a avaliação final
```

> **Estado atual:** a função `analisar_video()` do `avaliar.py` ainda não está implementada (é um `TODO`),
> então rodá-lo hoje termina com uma mensagem de erro. O `testar_avaliar.py` testa o restante do script com
> um gabarito falso. A análise temporal e o classificador de acidentes ainda não existem.

### Regras do projeto

As regras do módulo de visão (por exemplo: nunca classificar pelo nome do arquivo, nunca ler o gabarito
fora do `avaliar.py`, nunca usar os vídeos RESERVADOS para ajustes) estão no `Agents.md`.

## Central

Em construção.

## ESP32

Em construção.

## Resultados

Ainda não há resultados de avaliação: os vídeos de teste não foram usados e a classificação de acidentes
não foi implementada.
