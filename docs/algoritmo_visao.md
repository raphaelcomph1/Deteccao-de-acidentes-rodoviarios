# Algoritmo do módulo de visão (Motiva Vision)

Documento para o relatório. Descreve como o sistema vai do vídeo até o tipo de acidente e a confiança, com a
fórmula do escore, os pesos, os limiares e os limites de reconhecimento.
Todos os valores citados estão em `visao/config.yaml` (seções `analise_temporal` e `classificacao`) e são
**provisórios**: só podem ser ajustados com os vídeos de AJUSTE (seção 6).

## 1. Visão geral

```
vídeo → YOLO + ByteTrack (tracks) → trajetórias suavizadas → análise temporal (pares candidatos)
      → classificador (tipo + confiança) → evento MQTT
```

1. **Detecção e tracking** (`detector_tracker.py`): YOLO11 small (`yolo11s`) com ByteTrack, só as classes
   pessoa, carro, moto, ônibus e caminhão. O modelo nano não detectava o caminhão parado em um dos vídeos
   de ajuste, por isso usamos o small.
2. **Trajetórias** (`trajetorias.py`): por track, posição, tamanho da caixa, velocidade e direção, suavizadas
   por média móvel de 5 quadros que só usa quadros anteriores. Velocidades em **caixas por segundo**
   (pixels/s divididos pela raiz da área da caixa), o que reduz o efeito da perspectiva.
3. **Análise temporal** (`analise_temporal.py`): para cada par de tracks próximos, decide se há um
   **candidato** a acidente e calcula as features do par (seção 2).
4. **Classificador** (`classificador.py`): transforma o candidato em tipo, confiança e nível (seção 3).

Nenhuma etapa usa o nome do arquivo, metadados do vídeo ou o gabarito. A classificação sai só das imagens.

## 2. Análise temporal: quando um par vira candidato

Um par (veículo–veículo ou pessoa–veículo) só vira candidato se passar por **três portões**, nesta ordem.
Sobreposição de caixas sozinha nunca é colisão: é preciso evidência de mudança de movimento ao longo dos quadros.

| Portão | Regra | Parâmetros |
|---|---|---|
| 1. Contato | distância entre as bordas das caixas ≤ 0,10 da menor caixa (0 = sobrepostas) por 2 quadros seguidos | `contato_gap`, `contato_quadros_min` |
| 2. Movimento antes do contato | pelo menos 8 quadros de histórico dos dois tracks; algum veículo com velocidade mediana ≥ 0,5 caixa/s nos 0,5 s anteriores; os dois se aproximando a ≥ 0,3 caixa/s | `pre_min_quadros`, `velocidade_min_pre`, `aproximacao_min` |
| 3. Mudança depois do contato | veículos: um veículo que se movia perde ≥ 50% da velocidade (mediana antes × mediana de 0,3 s a 1,0 s depois) **e** fica parado (< 0,15 caixa/s) por ≥ 0,3 s dentro de 2 s. Pessoa: queda da proporção altura/largura (≥ 0,30), deslocamento brusco (≥ 1 caixa/s) ou sumiço do tracking por ≥ 10 quadros, **e** o veículo desacelera ≥ 30% | `desaceleracao_min`, `parada_min_s`, `pessoa_*` |

Consequências diretas das regras (todas cobertas por `testar_analise_temporal.py`):
- frenagem ou parada **sem contato** não gera candidato;
- veículos **já parados antes** do contato (semáforo, congestionamento) não geram candidato;
- um veículo em movimento batendo em outro parado **gera** candidato (o caminhão parado do vídeo 01);
- ultrapassagem com caixas sobrepostas, sem mudança de movimento, não gera candidato.

Features de cada candidato (todas viram colunas do CSV de depuração): `gap_contato`, `aproximacao_norm`,
velocidade antes (`v_pre_*`), desaceleração (`decel_*`), tempo parado (`parada_pos_s_*`), ângulo entre as
direções (`angulo_direcoes`, só com os dois em movimento), alinhamento (`alinhamento_*`: ângulo entre a
direção do veículo e o vetor que liga os centros; 0° = vinha de frente para o outro, 90° = vinha de lado),
`queda_proporcao`, `desloc_brusco`, `pessoa_sumiu`, `conf_media` e `continuidade`.

## 3. Classificador

### 3.1 Confiança

```
confianca = clip( Σ (peso_i × evidencia_i)  −  penal_conf  −  penal_continuidade , 0, 1 )
```

Cada `evidencia_i` vai de 0 a 1 de forma **gradual** (rampa linear entre dois valores, `rampa(x, x0, x1)` =
0 em `x0`, 1 em `x1`). Assim um acidente com parada curta ou ângulo ambíguo perde confiança aos poucos e cai em
`INDETERMINADO` em vez de sumir. A saída do classificador lista, para cada evidência, valor × peso =
contribuição (`classificador.py --video ...`), para explicar o escore.

### 3.2 Colisões (traseira e lateral)

| Evidência | Peso | Como é calculada |
|---|---|---|
| `contato` | 0,15 | metade pela profundidade do contato (sobreposto = 1, a 0,10 de distância = 0) e metade pela velocidade de aproximação (0 → 0, 1 caixa/s ou mais → 1) |
| `desaceleracao` | 0,25 | queda relativa de velocidade do veículo que mais freou: 0,30 → 0, 0,90 → 1 |
| `parada` | 0,20 | tempo parado desse veículo depois do contato: 0 s → 0, 1 s ou mais → 1 |
| `geometria` | 0,40 | coerência dos ângulos com o tipo escolhido (3.3) |

O peso maior da geometria é intencional: o tipo depende dela, então um ângulo ambíguo, mesmo com todo o
resto perfeito, não pode chegar a `ALTO` (verificado em `testar_classificador.py`).

### 3.3 Geometria e escolha do tipo

- **Um veículo em movimento e o outro parado** (caso do caminhão do vídeo 01): a direção do veículo parado é
  ruído e **não é usada**. Usa-se o alinhamento α do veículo em movimento (ângulo entre a direção dele e o
  vetor até o outro): `g_traseira = rampa(α, 60°, 30°)` (1 até 30°, 0 a partir de 60°) e
  `g_lateral = rampa(α, 45°, 75°)` (0 até 45°, 1 a partir de 75°).
- **Os dois em movimento**: usa-se o ângulo β entre as direções.
  `g_traseira = rampa(β, 45°, 20°) × rampa(menor alinhamento, 70°, 40°)` (direções quase paralelas e um vindo de
  frente para o outro); `g_lateral = rampa(β, 30°, 60°) × rampa(β, 150°, 120°)` (platô entre 60° e 120°;
  colisão frontal, β perto de 180°, não é lateral).
- O tipo é o de maior `g`. A coerência usada no escore é `g_do_tipo − g_do_outro_tipo`, então um ângulo que serve
  aos dois tipos vale pouco.

### 3.4 Atropelamento (pesos conservadores)

| Evidência | Peso | Como é calculada |
|---|---|---|
| `contato` | 0,10 | igual às colisões |
| `veiculo_reage` | 0,15 | desaceleração do veículo: 0 → 0, 0,90 → 1 |
| `pessoa` | 0,50 | `0,5 × queda + 0,3 × deslocamento + 0,2 × sumiço` (queda: proporção da caixa, 0 → 0 e 0,60 → 1; deslocamento: 0 → 0 e 3 caixas/s → 1; sumiço: 0 ou 1). **Sinal isolado vale pela metade**: só vale inteiro com pelo menos dois sinais (≥ 0,3) juntos |
| `geometria` | 0,25 | veículo vindo de frente para a pessoa: `rampa(α, 60°, 30°)` |

Além disso a confiança do atropelamento tem **teto de 0,85** (`teto_atropelamento`). Em cenas sintéticas, um sinal
isolado (só sumiu, ou só caiu) dá `INDETERMINADO`; dois ou mais sinais dão `ALTO`.

### 3.5 Penalidades (visibilidade ruim)

Cada uma subtrai até 0,15 do escore:
- `penal_conf_deteccao`: confiança média das detecções dos veículos do par (janela de 1 s antes a 2 s depois do
  contato). 0 acima de 0,60, máxima abaixo de 0,35;
- `penal_continuidade_track`: fração dos quadros dessa janela em que o track apareceu (tracking instável, buracos,
  troca de ID). 0 acima de 0,80, máxima abaixo de 0,30. Para pessoa só conta o veículo, porque sumir do tracking é
  uma das evidências do atropelamento.

### 3.6 Limiares (contrato v0.2)

| Confiança | Nível | Resultado |
|---|---|---|
| ≥ 0,70 | `ALTO` | classifica no tipo detectado |
| 0,40 a 0,69 | `MEDIO` | publica como `INDETERMINADO` (revisão humana, recomendação R4) |
| < 0,40 | `BAIXO` | `SEM_ACIDENTE`, não publica evento |

Se o vídeo tem vários candidatos, vale o de maior confiança. Sem candidatos, a saída é `SEM_ACIDENTE`.

## 4. Resultados da Etapa 2 (antes de qualquer ajuste; os da Etapa 4 estão na seção 6)

Vídeos de AJUSTE (`videos/`), pesos e limiares da seção 3:

| Vídeo | Cena | Tipo | Detectado | Confiança | Nível |
|---|---|---|---|---|---|
| `video_01` | carro bate na traseira de caminhão parado | `INDETERMINADO` | `COLISAO_TRASEIRA` | 0,68 | MEDIO |
| `video_02` | colisão em cruzamento, com tracking instável | `INDETERMINADO` | `COLISAO_LATERAL` | 0,61 | MEDIO |
| `video_06` | tráfego denso sem acidente | `SEM_ACIDENTE` | nenhum candidato | 0,00 | BAIXO |

Nos dois acidentes o tipo detectado está certo, mas a confiança fica abaixo de 0,70 por causa das penalidades de
tracking (continuidade 0,54 no vídeo 01 e 0,26 no vídeo 02, medidas ainda com a janela pós-contato) e da parada curta. Ver limitações e a seção 6.

Cenas sintéticas (`testar_classificador.py`):

| Cena | Tipo | Confiança | Nível |
|---|---|---|---|
| traseira forte | `COLISAO_TRASEIRA` | 0,94 | ALTO |
| traseira com parada curta (0,6 s) | `COLISAO_TRASEIRA` | 0,76 | ALTO |
| lateral a 90° | `COLISAO_LATERAL` | 1,00 | ALTO |
| ângulo ambíguo (40°) | `INDETERMINADO` | 0,66 | MEDIO |
| atropelamento com 3 sinais | `ATROPELAMENTO` | 0,85 (teto) | ALTO |
| atropelamento só com sumiço | `INDETERMINADO` | 0,52 | MEDIO |
| atropelamento só com queda | `INDETERMINADO` | 0,59 | MEDIO |

## 5. Limitações e limites de reconhecimento

- **Margem curta em `parada_min_s = 0,3 s`.** Esse limiar eliminou o único falso positivo do vídeo 06 (dois
  carros minúsculos ao fundo, parada de 0,2 s), mas os acidentes reais do ajuste ficam em 0,43 s (vídeo 01) e
  0,63 s (vídeo 02). A folga é pequena e o valor foi escolhido com apenas três vídeos; uma colisão em que o
  veículo reduz a velocidade sem parar por 0,3 s não gera candidato.
- **O tracking funde caixas no impacto.** No vídeo 02, as duas caixas viram uma só no momento da batida e os IDs
  mudam logo depois; um dos pares foi descartado por `sem_historico_pre`. Como a penalidade
  de continuidade agora só olha o período antes do contato (seção 6.1), isso não pesa mais no escore, mas o
  segundo carro só aparece 10 quadros antes do contato e a continuidade ficou em 0,30 (penalidade máxima; ver a
  proposta 6.3). O vídeo 01 tem buracos reais de detecção antes do contato (o caminhão parado aparece em 14 de 30
  quadros, continuidade 0,47). Não há religação de tracks perdidos nesta versão.
- **Atropelamento validado só em cena sintética.** Não há vídeo real de atropelamento para ajuste. A regra
  e os pesos conservadores (teto de 0,85, sinal isolado vale metade) foram definidos por raciocínio e testados só
  em cenas geradas por código. O comportamento em vídeo real é desconhecido.
- **Atraso da suavização.** O contato só é confirmado alguns quadros depois do impacto (média móvel de 5 quadros
  mais 2 quadros seguidos). Se a pessoa é projetada rápido logo no impacto, o contato pode nem ser registrado.
- **Ângulos em 2D.** As direções são medidas na imagem, não no chão; com câmera inclinada, ângulos reais de 90°
  podem aparecer bem diferentes. Com um veículo parado, o alinhamento não separa uma batida lateral de uma
  traseira quando o outro veículo vem de frente para o parado.
- **Detecção.** À noite, com chuva e com luzes fortes, o YOLO falha com veículos grandes ou parados e confunde
  classes (o caminhão de bombeiros foi detectado como `car`). Objetos muito pequenos têm velocidade normalizada
  ruidosa.
- **Só duas colisões e três tipos.** Colisão frontal, capotamento, queda de moto ou ciclista e acidentes sem
  contato entre os tracks não têm categoria própria; tendem a `INDETERMINADO` ou a não gerar candidato.
- **Poucos vídeos.** Pesos e limiares são provisórios e vieram de raciocínio físico; a calibração usa apenas
  três vídeos de ajuste.

## 6. Ajustes feitos com os vídeos de AJUSTE (Etapa 4)

### 6.1 Continuidade do tracking só antes do contato (aplicado)

**O que mudou.** `penal_continuidade_track` media a fração de quadros em que o track aparecia na janela de
1 s antes até 2 s depois do contato. Passou a medir **só a janela de 1 s antes do contato**.

**Justificativa geral.** Depois do contato, as caixas se fundem no impacto e os IDs mudam; isso é consequência
da própria colisão, não sinal de vídeo ruim. Penalizar esse trecho puniria justamente os acidentes mais violentos.
Antes do contato, buracos de detecção continuam indicando imagem ruim (noite, chuva, brilho).

**Antes e depois** (`classificador.py`, mesmos pesos, mesmos vídeos):

| Vídeo | Continuidade antes → depois | Penalidade antes → depois | Confiança antes → depois | Tipo |
|---|---|---|---|---|
| `video_01` (traseira) | 0,54 → 0,47 | −0,079 → −0,100 | 0,68 → 0,65 | `INDETERMINADO` (traseira) nos dois casos |
| `video_02` (lateral) | 0,26 → 0,30 | −0,150 → −0,150 | 0,61 → 0,61 | `INDETERMINADO` (lateral) nos dois casos |

**Resultado: o ajuste é correto em princípio, mas não melhora nenhum escore.** No `video_01` a continuidade
antes do contato é ainda pior que a medida antiga: o caminhão parado é detectado em só 14 dos 30 quadros
anteriores (à noite, com luzes fortes), então o buraco é real e a penalidade é justa. No `video_02` a penalidade
já estava no máximo.

### 6.2 Resultado do `avaliar.py --conjunto AJUSTE`

Pesos e limiares iguais aos das seções anteriores, só com o ajuste 6.1:

| Vídeo | Esperado | Obtido | Confiança | Erro |
|---|---|---|---|---|
| `video_01` | `COLISAO_TRASEIRA` | `INDETERMINADO` | 0,65 | indeterminado (tipo detectado certo) |
| `video_02` | `COLISAO_LATERAL` | `INDETERMINADO` | 0,61 | indeterminado (tipo detectado certo) |
| `video_06` | `SEM_ACIDENTE` | `SEM_ACIDENTE` | 0,00 | nenhum |
| `video_08` (chuva) | `SEM_ACIDENTE` | `SEM_ACIDENTE` | 0,00 | nenhum |
| `video_07` (atropelamento) | `ATROPELAMENTO` | não avaliado | | arquivo ainda não chegou (o `avaliar.py` só avisa e segue) |

Resumo: 2 acertos, 2 indeterminados, 0 falsos positivos, 0 falsos negativos; precisão e recall de **detecção**
100% (INDETERMINADO conta como detectado). A taxa de classificação exata é 2 de 4 e **nenhum acidente foi
afirmado com ALTO** nos vídeos de ajuste.

O `video_08` (chuva, visibilidade prejudicada, confiança média das detecções 0,58) gerou 33 tracks e 8 pares com
contato, todos descartados pelos portões da análise temporal (7 por "já parados antes" e 1 por "veículo parado
antes"): não houve candidato nem evento. Não houve falso positivo, portanto não foi preciso investigar.

### 6.3 Melhoria proposta (não adotada)

**Medir a continuidade a partir da primeira aparição do track na janela**, e não desde o início da janela.
Justificativa geral: um veículo que acabou de entrar no campo de visão não tem histórico para ter "buracos";
instabilidade é quadro faltando **dentro** da vida do track. Com essa definição, buracos reais (como o do caminhão
no `video_01`, 14 de 30) continuam penalizados.

**Decisão (15/10/2026): não adotar.** Fica registrada como melhoria proposta para trabalho futuro. O principal
motivo é o risco de mascarar troca de ID: um track novo criado por troca de ID parece "entrada em cena" e deixaria
de ser penalizado, escondendo justamente a instabilidade que a penalidade quer medir.

Efeito simulado nos vídeos de ajuste (sem aplicar): `video_01` não muda (0,47, o track já existia na janela inteira);
`video_02` passaria de 0,30 para 0,90 (o segundo carro entra em cena 10 quadros antes do contato e aparece em 9 deles)
e a confiança iria de 0,61 para 0,76 (`COLISAO_LATERAL`, ALTO). O efeito prático ficaria concentrado em um único
vídeo de ajuste.

Nenhum outro peso ou limiar foi alterado nesta etapa.

## 7. Indicadores do evento e o que significam

Os indicadores do contrato (`indicadores` no evento) vêm só do que a visão observou no par de tracks:

| Indicador | Como é preenchido | O que **não** quer dizer |
|---|---|---|
| `veiculos_envolvidos` | 2 em colisão veículo–veículo, 1 em atropelamento (só os veículos do par) | pode haver outros veículos envolvidos que não entraram no par |
| `pessoa_envolvida` | `true` se o candidato é um par pessoa–veículo | `false` significa **"nenhuma pessoa detectada perto do contato"**, não ausência confirmada: uma pessoa que o detector não viu (escuro, chuva, oclusão) não aparece |
| `veiculo_parado_na_pista` | `true` se algum veículo do par está parado (< 0,15 caixa/s) no último quadro observado; vira `true` em uma revisão se isso acontecer depois | `false` pode também significar que o veículo saiu de cena |
| `faixa_obstruida` | sempre `desconhecido` (não há informação de faixas) | |

O que não for possível observar fica `null` ou `desconhecido`; nada é inventado. A recomendação (R1 a R4) é só uma
sugestão aplicada sobre esses indicadores e depende do operador.

