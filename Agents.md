# AGENTS.md — Motiva Vision (CP3 FIAP)

Instruções para agentes de código (Claude Code e similares) que trabalham neste repositório.
Leia este arquivo inteiro antes de qualquer tarefa.

## 1. O projeto

Protótipo acadêmico que analisa vídeos de rodovia, detecta possíveis acidentes, classifica o tipo,
publica o evento via MQTT para uma central (Node-RED) e aciona a sinalização em um ESP32 (Wokwi).
O enunciado completo está em `docs/CP3-MORGS.pdf`. Em caso de dúvida sobre requisitos, consulte o PDF.

Cadeia completa:
`vídeo → visão computacional → MQTT → central (operador confirma/rejeita) → ESP32 → ACK de volta à central`

- **Prazo de entrega:** 16/10/2026 (meta interna: 15/10/2026)
- **Natureza:** simulação acadêmica. Nada de diagnóstico médico, nada de acionar serviços reais.
  Toda decisão de atendimento fica com o operador humano.

## 2. Equipe e responsabilidades

| Pessoa | RM | Módulo | Pasta |
|---|---|---|---|
| Raphael Mischiatti (entrega) | 563567 | Visão computacional | `/visao` |
| Guilherme Rezende | 563500 | Central Node-RED + dashboard | `/central` |
| Fernando Caire | 563415 | ESP32 (Wokwi) | `/esp32` |

**Não altere arquivos fora da pasta do módulo em que você está trabalhando**, a menos que o usuário peça.

## 3. Decisões já fechadas (não mudar sem aprovação do grupo)

- **Categorias:** `COLISAO_TRASEIRA`, `COLISAO_LATERAL`, `ATROPELAMENTO`, mais as saídas `SEM_ACIDENTE` e `INDETERMINADO`.
- **Broker:** `broker.hivemq.com:1883` (plano B: `test.mosquitto.org:1883`). Host sempre configurável, nunca fixo no código.
- **Tópicos:** `motiva/rm563567/eventos`, `/comandos`, `/status`, `/central`.
- **Evidência:** o campo `arquivo` do evento usa `visao/saida/evidencias/<event_id>.jpg` (relativo à raiz do repositório); o código da visão grava em `saida/evidencias/` dentro de `visao/`.
- **Contrato das mensagens:** `docs/contrato_mqtt.md` (v0.2) e exemplos em `docs/exemplos/`.
  **O contrato é congelado.** Toda mensagem produzida deve seguir exatamente esses campos e valores.
  Se uma mudança parecer necessária, pare e avise o usuário em vez de alterar.

## 4. Regras proibidas (requisitos do professor — violar zera a nota da visão)

1. **Nunca classificar pelo nome do arquivo de vídeo** ou por qualquer metadado externo.
   A classificação tem de resultar da análise das imagens.
2. **Nunca ler `dados/gabarito.csv` no código de detecção.** Ele só pode ser lido pelo script de avaliação.
3. **Nunca usar os vídeos do conjunto RESERVADO para ajustar limiares, pesos ou regras.**
   Ajustes usam apenas os vídeos de AJUSTE.
4. **Sobreposição de caixas (bounding boxes) sozinha não é colisão.** Toda detecção precisa de evidência temporal
   (mudança de movimento ao longo dos quadros).
5. **Nada de inventar certeza:** sem evidência suficiente, a saída é `INDETERMINADO` com pedido de revisão humana.
6. **Nunca enviar imagens pelo broker** (é público). Só o caminho local do arquivo de evidência.
7. **Nunca commitar** vídeos, credenciais, arquivos `.env` ou pesos de modelo grandes.
8. **Vídeos de desenvolvimento nunca podem ser os mesmos dos 12 vídeos de teste** (AJUSTE e RESERVADO). Eles ficam em `dados/dev/` (ignorada pelo git) e são listados em `docs/videos_desenvolvimento.md` (arquivo, tipo, fonte, observação).

## 5. Módulo de visão (`/visao`)

### Stack
- Python 3.11, ambiente virtual em `.venv`
- `ultralytics` (YOLO pré-treinado + tracking ByteTrack), `opencv-python`, `paho-mqtt`, `pyyaml`
- Versões fixadas em `visao/requirements.txt` (atualizar sempre que instalar algo)
- Usar o modelo nano/pequeno da Ultralytics para rodar em CPU de notebook

### Estrutura sugerida
```
visao/
├── main.py                 # ponto de entrada: python main.py --video caminho.mp4
├── util.py                 # carregar_config (sem dependências pesadas)
├── config.yaml             # broker, tópicos, limiares, mapeamento video -> camera_id/local
├── detector_tracker.py     # YOLO + ByteTrack, devolve tracks por quadro
├── trajetorias.py          # histórico por track: posição, velocidade, direção, tamanho da caixa
├── analise_temporal.py     # features entre pares de tracks (aproximação, parada brusca, ângulo)
├── classificador.py        # regras -> tipo + confiança + nível
├── eventos.py              # event_id, deduplicação, matriz de recomendação, montagem do JSON
├── publicador_mqtt.py      # conexão e publicação (QoS 1, reconexão)
├── avaliar.py              # roda os vídeos e compara com dados/gabarito.csv (único que lê o gabarito)
└── saida/                  # ignorada no git: evidencias/*.jpg, logs/*.csv
```

### Classes YOLO (COCO) usadas
`0 person`, `2 car`, `3 motorcycle`, `5 bus`, `7 truck`. Ignorar as demais.

### Diretrizes iniciais da análise temporal (a refinar com os vídeos de ajuste)
- Medir velocidades em **pixels por segundo** usando o FPS real do vídeo, e normalizar pelo tamanho da caixa
  para reduzir o efeito de perspectiva (veículos longe parecem mais lentos).
- Suavizar posições (média móvel de alguns quadros) antes de calcular velocidade e aceleração.
- Indícios de acidente: aproximação rápida entre dois tracks, contato/proximidade, **desaceleração brusca**
  e veículo(s) parado(s) depois do contato.
- Diferenciação:
  - **Traseira:** direções quase paralelas, um track atrás do outro no sentido do movimento.
  - **Lateral:** direções com ângulo grande entre si (aprox. 45° a 135°) no momento do contato.
  - **Atropelamento:** contato entre `person` e veículo, seguido de mudança brusca no pedestre
    (para, some do tracking ou muda a proporção da caixa).
- **Confiança:** combinação ponderada de evidências normalizadas entre 0 e 1. A fórmula e os pesos
  devem ficar documentados em `docs/` (o relatório precisa explicar como o escore é obtido).
- **Limiares (provisórios):** ≥ 0.70 classifica (`ALTO`); 0.40–0.69 vira `INDETERMINADO` (`MEDIO`);
  < 0.40 é `SEM_ACIDENTE` e não publica evento.

### Desenvolvimento até os vídeos de AJUSTE chegarem
- A lógica (análise temporal, classificação) é desenvolvida com os vídeos de `dados/dev/`, registrados em `docs/videos_desenvolvimento.md`.
- Quando os vídeos de AJUSTE chegarem, eles passam a ser a base para ajustar limiares e pesos.

### Eventos
- `event_id` no formato `evt-<camera>-<AAAAMMDDTHHMMSS>-<4 hex aleatórios>`.
- Um mesmo acidente mantém **um único `event_id`** enquanto os tracks envolvidos estiverem na cena.
  Atualizações reenviam o mesmo `event_id` com `revisao` incrementada.
- Ao detectar, salvar o quadro de evidência com as caixas desenhadas em `saida/evidencias/<event_id>.jpg`
  e registrar `video`, `frame` e `tempo_s`.
- A matriz de recomendação (R1–R4) está em `docs/contrato_mqtt.md`, seção 4. Prioridade R4 > R3 > R2 > R1.

## 6. Convenções de código

- Código e comentários em português, nomes em `snake_case`, sem acentos em identificadores.
- Configurações em `config.yaml`; credenciais (se existirem) em `.env`, nunca no código.
- Funções pequenas e com docstring curta explicando o que fazem. O grupo precisa conseguir explicar
  o algoritmo no relatório, então **prefira clareza a esperteza**.
- Ao concluir uma mudança, explique ao usuário em poucas linhas o que foi feito e por quê.
- Commits pequenos, mensagens em português no imperativo (ex.: `adiciona calculo de velocidade por track`).
- Antes de commitar, rode o código no vídeo de teste e confirme que funciona.

## 7. Estado atual e próximos passos (atualizar conforme avançar)

- [x] Ambiente Python 3.11 + `requirements.txt` com versões
- [ ] YOLO + ByteTrack rodando em um vídeo de treino, com IDs estáveis desenhados na tela — pipeline pronto (`main.py --mostrar`), testado só em vídeo sintético; falta validar a estabilidade dos IDs em vídeo real
- [x] Registro de trajetórias por track em CSV (`saida/logs/`)
- [x] Publicação de `docs/exemplos/evento.json` no broker (teste de conexão) — `testar_mqtt.py` (tópico de teste; `--real` publica em `eventos`)
- [x] Caminho da evidência padronizado (`visao/saida/evidencias/<event_id>.jpg`, relativo à raiz) no contrato e no exemplo
- [x] `trajetorias.py`: velocidade (px/s e normalizada pela caixa) e direção suavizadas, CSV + vídeo com rastro e vetor — validado só em vídeo sintético (30 px/s medido ≈ 30,4); falta conferir em vídeo real (a direção oscila, pode precisar de janela maior)
- [x] `dados/dev/` e `docs/videos_desenvolvimento.md` criados (tabela ainda vazia; nenhum vídeo de desenvolvimento adicionado)
- [x] Esqueleto do `avaliar.py` (`--conjunto`, CSV em `saida/resultados/`, resumo, precisão/recall), testado com gabarito falso em arquivo temporário; `analisar_video()` ainda é um TODO
- [x] `publicador_mqtt.py`: `publicar()` não derruba o programa com o broker fora; guarda pendentes em memória e reenvia ao reconectar (`testar_mqtt_offline.py`)
- [x] `avaliar.py`: esperado `INDETERMINADO` + acidente obtido = `excesso_de_confianca`; esses vídeos ficam fora de precisão/recall e têm contador próprio
- [x] `carregar_config` movido para `visao/util.py` (avaliar e testes não dependem mais do ultralytics)
- [x] `README.md` na raiz (objetivo, arquitetura, pastas, integrantes, instalação e uso da visão; Central e ESP32 marcados "em construção")
- [ ] Análise temporal e classificação (usar `dados/dev/` até os 6 vídeos de AJUSTE chegarem, previstos para 09/10)
- [ ] Deduplicação, evidências e publicação real dos eventos
- [ ] Ligar o pipeline real ao `avaliar.py` e fazer a avaliação final nos vídeos RESERVADOS (14/10)
