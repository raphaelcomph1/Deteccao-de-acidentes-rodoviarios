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

### 3.7 Teto de confiança por resolução (colisão veículo–veículo)

Depois das penalidades, se a **menor dimensão (largura ou altura) das duas caixas no quadro do contato** for menor
que `teto_resolucao_caixa_px` (50 px, `config.yaml`), a confiança fica limitada a `teto_resolucao_confianca`
(0,65): no máximo `INDETERMINADO` (`MEDIO`). A contribuição aparece na saída como `teto_resolucao_caixa`.
O candidato registra o valor medido em `menor_caixa_px`. A regra **não vale para atropelamento** (a largura de
uma pessoa é pequena por natureza e não mede a distância do objeto). Justificativa física e resultados na seção 6.4.

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
- **Atropelamento: a detecção é o limite, não a regra.** No `video_07` (vídeo real de AJUSTE, 352×288) a vítima,
  com cerca de 15×25 px, **nunca é detectada como `person`** (pequena, escura, agachada na faixa e depois caída no
  chão), e o carro só passa a ser rastreado em 3,22 s (track #60, classificado como `truck`), cerca de 0,4 s antes
  do impacto. Nenhum par pessoa–veículo chega a contato, então as features da pessoa (queda, deslocamento e
  sumiço) nem chegam a ser medidas e o resultado é `SEM_ACIDENTE` (falso negativo). Decisão do grupo (16/10/2026):
  **nenhuma mudança**; fica registrado como limitação de detecção de objetos pequenos em baixa resolução. Fora
  esse vídeo, a regra e os pesos conservadores (teto de 0,85, sinal isolado vale metade) só foram testados em
  cenas geradas por código.
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
- **Colisão em veículos pequenos na imagem.** O teto da seção 3.7 limita a `INDETERMINADO` colisões em que a
  menor caixa tem menos de 50 px: mesmo uma batida real nessa escala não é afirmada com `ALTO`.
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

### 6.4 Teto de confiança por resolução (aplicado, 16/10/2026)

**Problema.** O `video_11` (tráfego normal, 1280×720, esperado `SEM_ACIDENTE`) gerou `COLISAO_TRASEIRA` com
confiança 0,89 (`ALTO`). O par é um carro que chega a uma fila parada e freia: as duas caixas (73×30 e 78×29 px,
ao fundo da rua) ficam a 4,8% da menor caixa uma da outra, o que cumpre `contato_gap = 0,10`. A regra "um em
movimento, o outro parado, alinhados, o primeiro freia e para" descreve uma batida traseira, mas também descreve
um carro entrando numa fila. A única prova de contato é o gap em pixels, e em veículo pequeno na imagem ele não
é confiável.

**Regra.** Em colisão veículo–veículo, se a menor dimensão das duas caixas no contato for < 50 px, a confiança
fica limitada a 0,65 (no máximo `INDETERMINADO`, que publica o evento para revisão humana, recomendação R4).

**Por que 50 px (raciocínio físico, não ajuste ao resultado).**
1. O contato é declarado quando o gap entre as bordas é ≤ 10% da caixa menor. Para o gap provar contato, essa
   tolerância precisa ser maior que a incerteza de posição das bordas.
2. O ruído de borda foi medido nas trajetórias dos vídeos de AJUSTE, em veículos praticamente parados (desvio
   padrão de cada borda ao longo de 1 s): mediana de 0,1 a 1,1 px conforme o vídeo; p90 de até 1,8 px (pior caso,
   `video_08`, com chuva).
3. O gap soma duas bordas: √2 × 1,8 ≈ 2,5 px; com margem de 2σ, ≈ 5 px.
4. Tolerância de 10% ≥ 5 px ⇒ caixa de pelo menos 5 / 0,10 = **50 px**. Abaixo disso, "gap ≤ 10%" está dentro do
   ruído do detector.
Nos vídeos de AJUSTE qualquer valor entre 30 e 80 px dá o mesmo resultado; os 50 px vêm da conta acima, não de um
ajuste. Limites, ambos no `config.yaml` (`classificacao`): `teto_resolucao_caixa_px: 50`,
`teto_resolucao_confianca: 0,65`.

**Escopo.** Só colisão veículo–veículo. A menor dimensão de uma caixa de pessoa é a largura, pequena por natureza
(10 a 17 px nas pessoas do `video_07`), e aplicar a regra a ela limitaria todo atropelamento a `INDETERMINADO` sem que
isso diga nada sobre a distância. O atropelamento mantém o teto próprio de 0,85.

**Antes e depois** (`menor_caixa_px` vem das trajetórias dos 6 vídeos de AJUSTE; as demais colunas, do mesmo código
com o limite zerado × 50):

| Vídeo | Menor caixa no contato | Antes | Depois |
|---|---|---|---|
| `video_01` | 27 px | `INDETERMINADO` 0,65 | `INDETERMINADO` 0,65 (já estava no teto) |
| `video_02` | 80 px | `INDETERMINADO` 0,61 | `INDETERMINADO` 0,61 (acima de 50 px) |
| `video_06`, `video_07`, `video_08` | sem candidato | `SEM_ACIDENTE` | `SEM_ACIDENTE` |
| `video_11` | 27 px | `COLISAO_TRASEIRA` 0,89 (`ALTO`) | `INDETERMINADO` 0,65 |

**Limitação.** O teto não diz que o `video_11` não tem acidente; só que, com caixas desse tamanho, o gap não
basta para afirmar contato. O evento ainda é publicado como `INDETERMINADO` (o `avaliar.py` o conta como `indeterminado` e
entra como detecção indevida na precisão, mas sem afirmar acidente). Uma batida real em veículos pequenos na imagem também vai a no máximo `INDETERMINADO`.

### 6.5 Melhoria futura (não implementada): parada brusca × frenagem gradual

Distinguir o impacto de uma frenagem normal pelo **perfil** da desaceleração: em colisão a velocidade cai quase de
uma vez (pico de desaceleração em poucos quadros) e o outro veículo também é perturbado; ao entrar numa fila o
carro reduz gradualmente e o parado não muda. O `video_11` tem desaceleração de 0,89 mas medida numa janela de 1 s
(`janela_decel_s`), sem informação de quão rápida foi. Exigiria medir a taxa de variação da velocidade e a reação
do segundo veículo; fica como trabalho futuro (não foi aplicado para não mexer em regras após o ajuste).

### 6.6 Resultado final do conjunto AJUSTE (6 vídeos, código com o teto da seção 6.4)

`avaliar.py --conjunto AJUSTE` (pipeline completo, sem publicar no broker):

| Vídeo | Esperado | Obtido | Confiança | Instante esperado → detectado | Resultado |
|---|---|---|---|---|---|
| `video_01` | `COLISAO_TRASEIRA` | `INDETERMINADO` | 0,65 | 5 s → 5,13 s | indeterminado (tipo detectado certo) |
| `video_02` | `COLISAO_LATERAL` | `INDETERMINADO` | 0,61 | 8 s → 8,17 s | indeterminado (tipo detectado certo) |
| `video_06` | `SEM_ACIDENTE` | `SEM_ACIDENTE` | 0,00 | – | acerto |
| `video_07` | `ATROPELAMENTO` | `SEM_ACIDENTE` | 0,00 | 3 s → – | falso negativo (limitação de detecção, seção 5) |
| `video_08` | `SEM_ACIDENTE` | `SEM_ACIDENTE` | 0,00 | – | acerto |
| `video_11` | `SEM_ACIDENTE` | `INDETERMINADO` | 0,65 | – → 160,83 s | indeterminado (sem o teto seria `COLISAO_TRASEIRA` 0,89) |

Resumo: 2 acertos, 3 indeterminados, 1 falso negativo, 0 falsos positivos afirmados, 0 classificações erradas;
precisão e recall de detecção de 67%. **Nenhum acidente é afirmado com `ALTO` nos vídeos de AJUSTE.**

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

## 8. Resultados finais: conjunto RESERVADO (código congelado na tag `v1.0-congelado`)

`avaliar.py --conjunto RESERVADO --videos ../videos_reservado` (16/10/2026), sem publicar no broker, com os pesos,
limiares e regras da tag `v1.0-congelado` (commit com o teto da seção 6.4). Nenhum parâmetro foi alterado depois de
abrir os vídeos reservados. Arquivos: `docs/resultados_finais/resultados_reservado.csv` (mais
`resultados_ajuste.csv`, para comparação) e as imagens de evidência dos eventos publicados. Um segundo
processamento dos mesmos vídeos (para extrair as features) deu exatamente as mesmas confianças.

### 8.1 Tabela

| Vídeo | Esperado | Obtido | Confiança | Instante esperado → detectado | Resultado |
|---|---|---|---|---|---|
| `video_03` | `COLISAO_LATERAL` | `COLISAO_LATERAL` | 0,82 (`ALTO`) | 7 s → 6,43 s | acerto |
| `video_04` | `SEM_ACIDENTE` | `SEM_ACIDENTE` | 0,00 | – | acerto |
| `video_05` | `COLISAO_TRASEIRA` | `SEM_ACIDENTE` | 0,00 | 4 s → – | **falso negativo** |
| `video_09` (chuva forte) | `SEM_ACIDENTE` | `SEM_ACIDENTE` | 0,00 | – | acerto |
| `video_10` | `ATROPELAMENTO` | `INDETERMINADO` | 0,54 | 8 s → 46,03 s (ver 8.2) | **indeterminado** |
| `video_12` | `SEM_ACIDENTE` | `SEM_ACIDENTE` | 0,00 | – | acerto |

Resumo: 4 acertos, 1 falso negativo, 1 indeterminado, 0 falsos positivos, 0 classificações erradas, 0 excessos de
confiança; precisão de detecção 100% e recall 67%. Um acidente afirmado com `ALTO` (`video_03`). Nos vídeos sem
acidente (04, 09, 12) não houve evento. **Atenção ao `video_10`:** a confiança (0,54) e o instante (46,03 s) da
tabela são os do evento de maior confiança do vídeo, que **não é** o acidente esperado (ver abaixo).

### 8.2 Análise de cada erro e de cada resultado não afirmado (a partir das features)

Só explicação: nenhuma correção foi proposta nem aplicada.

**`video_03` (acerto, com duas ressalvas).** Par carro #78 × carro #17, contato em 6,43 s: os dois se movem (v_pre
0,66 e 0,76), ângulo entre as direções 99° (lateral), aproximação 2,64 caixas/s, desaceleração 0,82 e 0,72, parados
por 1,43 s e 1,03 s. Escore 0,15 + 0,217 + 0,20 + 0,40 − 0,15 = 0,82. Ressalvas: (a) o contato foi registrado
0,57 s **antes** do instante do gabarito (7 s, valor inteiro anotado a olho); (b) a penalidade de continuidade ficou
no máximo (−0,15; continuidade 0,30, o segundo carro só entra em cena pouco antes do contato) e a menor caixa no
contato mede 52 px, **2 px acima do limite de 50 px do teto da seção 3.7**: com caixas 2 px menores o resultado
seria `INDETERMINADO` 0,65. Um segundo par (#9 × #17, 0,70) é o mesmo acidente (mesmo `event_id`).

**`video_05` (falso negativo, noturno, 848×478).** O analisador não gerou nenhum candidato. Houve 8 contatos descartados (pares), nenhum entre 2 e 5 s: cinco entre 0,33 s e 0,83 s (quatro "já parados antes", fila no semáforo, e
um "sem histórico") e três em 11 e 13 s. Features dos veículos: caixas pequenas (41×36, 41×30 e 54×43 px, menores dimensões abaixo do limite de
50 px, então o teto de resolução limitaria qualquer candidato a `INDETERMINADO`), confiança média de detecção de
0,49 a 0,51 e quase parados (velocidade mediana 0,13 e 0,14 caixa/s; o #5 anda a 0,60). Os pares #3 × #4 e #3 × #5
estão a gap ≤ 0,2 durante a janela do acidente (0,00 e 0,09 em 4,0 s), mas ambos já tinham sido descartados em
0,33 s por "já parados antes", e o código só reavalia um par depois que ele se afasta (`liberar_gap` = 1,0): o
#3 × #4 nunca se separa (gap máximo 0,34), então não é reavaliado. Não confirmei se o #3 × #5 chegou a ser
liberado. Também não consegui identificar visualmente o impacto nos quadros desse vídeo (carros de ~30 a 50 px,
noite, luzes dos faróis). Causa provável: veículos já encostados na imagem desde o início da cena, sem fase de
aproximação visível, combinados com detecção fraca à noite.

**`video_10` (`INDETERMINADO`, 480×272).** Há dois eventos no vídeo:
- *Evento no instante do acidente (contato em 7,5 a 7,8 s, emitido aos 8,0 s):* `INDETERMINADO` 0,40 → 0,45. O
  carro #96 se move rápido (v_pre 4,4 a 4,5 caixas/s) e cruza o grupo de pedestres na faixa; três pares com
  pessoas (#3, #101, #120). Pessoa: queda da proporção 0,07 a 0,23, deslocamento brusco 1,5 a 4,1 caixas/s, **nenhuma
  sumiu do tracking**. Carro: desaceleração de só 0,39 a 0,40 (a referência de reação plena é 0,90) e parado por
  0,07 s, ou seja, não freou. Geometria 0,6, 0,0 e 0,77 (alinhamento 66°/70° no par com a pessoa #101 que mais se
  desloca). Escores máximos 0,40, 0,26 e 0,43, com a contribuição da pessoa em só 0,07 a 0,09 de 0,50 possíveis.
  As pessoas têm 8 a 11 px de largura (e ~25 px de altura), então queda de proporção e deslocamento ficam ruidosos,
  o mesmo problema de objeto pequeno do `video_07` (seção 5). No recorte, o carro escuro atravessa o grupo de
  pedestres entre 7,2 s e 8,4 s; não consegui confirmar visualmente quem foi atingido.
- *Evento aos 46,03 s:* carro #613 × pessoa #650, `INDETERMINADO` 0,52 (0,54 no máximo; a pessoa some do tracking,
  o carro desacelera 0,61). **Não é o acidente esperado** (8 s), mas, por ter confiança maior, é o que o
  `avaliar.py` reporta (por isso 46,03 s na tabela). O tipo obtido (`INDETERMINADO`) coincide com o evento do
  acidente, mas o instante reportado não.
Em resumo: o evento do instante certo ficou em 0,45 (limiar de `ALTO`: 0,70) porque os sinais da pessoa são fracos
(caixa minúscula) e o carro não freia; e um evento espúrio de maior confiança passou a representar o vídeo.

**Vídeos sem acidente (04, 09, 12).** Nenhum candidato e nenhum evento; incluindo o `video_09` com chuva forte e
detecção ruidosa.
