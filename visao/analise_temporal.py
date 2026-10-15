"""Analise temporal: features entre pares de tracks (veiculo-veiculo e pessoa-veiculo).

Uso (depuracao): python analise_temporal.py --video caminho.mp4
  Precisa do CSV de main.py (saida/logs/<video>_trajetorias.csv). Gera em saida/logs/:
  <video>_candidatos.csv, <video>_descartes.csv, <video>_depuracao.mp4 e <video>_candidato_<a>_<b>.jpg

Ideia: um par vira CANDIDATO so se passar por tres portoes, nesta ordem:
  1. contato: as caixas se tocam (ou chegam muito perto) por alguns quadros seguidos;
  2. movimento antes do contato: o historico existe, algum track estava se movendo e os dois se aproximavam
     (carros parados lado a lado, congestionamento e semaforo nao passam);
  3. mudanca de movimento depois do contato: um veiculo que se movia perde a velocidade e para (veiculos) ou a
     pessoa cai/some/e deslocada e o veiculo reage (pessoa-veiculo). So sobreposicao de caixas nao basta.
Frenagem ou parada sem contato nunca gera candidato. Quem decide o TIPO e a confianca e o classificador.

Velocidade "antes do contato" = mediana da velocidade nos ultimos janela_direcao_s; desaceleracao = queda relativa
entre essa mediana e a mediana de 0,3 s a janela_decel_s depois do contato (0 = manteve, 1 = parou).

Unidades: distancias em "tamanhos de caixa" (raiz da area da caixa suavizada); velocidades em caixas/s
(velocidade_norm de trajetorias.py). Tudo causal: so usa quadros ja vistos, entao serve para tempo real
(os candidatos vao sendo atualizados durante a janela pos-contato).
"""

import argparse
import csv
import math
from collections import defaultdict, deque
from itertools import combinations
from pathlib import Path
from statistics import median

import cv2

from trajetorias import HistoricoTrack
from util import carregar_config

VEICULOS = {"car", "motorcycle", "bus", "truck"}
PESSOA = "person"

CAMPOS_DESCARTE = ["frame_contato", "tempo_contato_s", "track_a", "track_b", "motivo"]


# ---------------------------------------------------------------- geometria

def escala(f):
    """Tamanho de referencia da caixa (raiz da area), em pixels."""
    return math.sqrt(max(f["largura"] * f["altura"], 1.0))


def gap_normalizado(a, b):
    """Distancia entre as bordas das caixas (0 = sobrepostas) dividida pela menor caixa."""
    dx = max(0.0, abs(a["cx"] - b["cx"]) - (a["largura"] + b["largura"]) / 2)
    dy = max(0.0, abs(a["cy"] - b["cy"]) - (a["altura"] + b["altura"]) / 2)
    return math.hypot(dx, dy) / min(escala(a), escala(b))


def angulo_entre(v1, v2):
    """Angulo (0 a 180 graus) entre dois vetores (x, y)."""
    n1, n2 = math.hypot(*v1), math.hypot(*v2)
    if n1 == 0 or n2 == 0:
        return None
    cos = (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


def tipo_do_par(classe_a, classe_b):
    """'veiculo_veiculo', 'pessoa_veiculo' ou None (outros pares sao ignorados)."""
    if classe_a in VEICULOS and classe_b in VEICULOS:
        return "veiculo_veiculo"
    if PESSOA in (classe_a, classe_b) and (classe_a in VEICULOS or classe_b in VEICULOS):
        return "pessoa_veiculo"
    return None


def arredondar(valor, casas=2):
    return None if valor is None else round(valor, casas)


# ---------------------------------------------------------------- analisador

class AnalisadorTemporal:
    """Recebe as features de cada quadro (em ordem) e devolve os candidatos a acidente."""

    def __init__(self, config, fps, min_quadros):
        self.cfg = config["analise_temporal"]
        self.fps = fps
        self.min_quadros = min_quadros
        self.historico = defaultdict(lambda: deque(maxlen=int(12 * fps)))   # track_id -> features
        self.pares = {}          # (id_a, id_b) -> estado do par
        self.descartes = []      # pares com contato que nao viraram candidato (com o motivo)
        self.finais = {}         # (id_a, id_b) -> ultimo estado do candidato

    # --- utilitarios
    def n_quadros(self, segundos):
        return max(1, round(segundos * self.fps))

    def janela(self, track_id, ini, fim):
        """Features do track entre os quadros ini e fim (inclusive)."""
        return [f for f in self.historico[track_id] if ini <= f["frame"] <= fim]

    def descartar(self, estado, motivo, quadro_contato):
        estado["estado"] = "descartado"
        self.descartes.append({
            "frame_contato": quadro_contato, "tempo_contato_s": round(quadro_contato / self.fps, 2),
            "track_a": estado["a"], "track_b": estado["b"], "motivo": motivo})

    # --- passo principal
    def processar_quadro(self, quadro, features):
        """Atualiza os historicos com as features do quadro e devolve os candidatos ativos neste quadro."""
        for f in features:
            self.historico[f["track_id"]].append(f)
        validos = [f for f in features if len(self.historico[f["track_id"]]) >= self.min_quadros]

        for fa, fb in combinations(validos, 2):
            tipo = tipo_do_par(fa["classe"], fb["classe"])
            if tipo is None:
                continue
            if tipo == "pessoa_veiculo" and fa["classe"] == PESSOA:
                fa, fb = fb, fa                 # no par pessoa-veiculo, a = veiculo e b = pessoa
            chave = tuple(sorted((fa["track_id"], fb["track_id"])))
            estado = self.pares.setdefault(chave, {
                "estado": "longe", "seq": 0, "inicio_seq": None, "tipo": tipo,
                "a": fa["track_id"], "b": fb["track_id"], "c": None, "pre": None, "teve_candidato": False})
            self.atualizar_contato(estado, quadro, gap_normalizado(fa, fb))

        candidatos = []
        for estado in self.pares.values():
            if estado["estado"] == "observando":
                candidato = self.observar(estado, quadro)
                if candidato:
                    candidatos.append(candidato)
        return candidatos

    def atualizar_contato(self, estado, quadro, gap):
        """Maquina de estados do par: longe -> contato -> observando -> encerrado (ate se afastar)."""
        cfg = self.cfg
        if gap > cfg["contato_gap"]:
            estado["seq"] = 0
            if estado["estado"] in ("descartado", "encerrado") and gap > cfg["liberar_gap"]:
                estado["estado"] = "longe"
            return
        if estado["estado"] != "longe":
            return
        if estado["seq"] == 0:
            estado["inicio_seq"] = quadro
        estado["seq"] += 1
        if estado["seq"] < cfg["contato_quadros_min"]:
            return
        # contato confirmado: avalia o que veio antes (portao 2)
        estado["c"] = estado["inicio_seq"]
        pre, motivo = self.calcular_pre(estado)
        if pre is None:
            self.descartar(estado, motivo, estado["c"])
        else:
            estado["pre"] = pre
            estado["estado"] = "observando"

    # --- antes do contato
    def calcular_pre(self, estado):
        """Velocidade, aproximacao, direcoes e alinhamento antes do contato. Devolve (dict, None) ou (None, motivo)."""
        cfg, fps, c = self.cfg, self.fps, estado["c"]
        ida, idb = estado["a"], estado["b"]
        n_pre = self.n_quadros(cfg["janela_pre_s"])
        ja = {f["frame"]: f for f in self.janela(ida, c - n_pre, c - 1)}
        jb = {f["frame"]: f for f in self.janela(idb, c - n_pre, c - 1)}
        comuns = sorted(set(ja) & set(jb))
        if len(comuns) < cfg["pre_min_quadros"]:
            return None, "sem_historico_pre"

        n_dir = self.n_quadros(cfg["janela_direcao_s"])
        v_a = self.velocidade_mediana(ja.values(), c - n_dir)
        v_b = self.velocidade_mediana(jb.values(), c - n_dir)
        if estado["tipo"] == "pessoa_veiculo":
            if v_a < cfg["velocidade_min_pre"]:
                return None, "veiculo_parado_antes"
        elif max(v_a, v_b) < cfg["velocidade_min_pre"]:
            return None, "ja_parados_antes"

        fa_c, fb_c = self.janela(ida, c, c)[0], self.janela(idb, c, c)[0]
        gap_c = gap_normalizado(fa_c, fb_c)
        f0 = comuns[0]
        aproximacao = (gap_normalizado(ja[f0], jb[f0]) - gap_c) / ((c - f0) / fps)
        if aproximacao < cfg["aproximacao_min"]:
            return None, "sem_aproximacao"

        dir_a = self.direcao_media([f for f in ja.values() if f["frame"] >= c - n_dir])
        dir_b = self.direcao_media([f for f in jb.values() if f["frame"] >= c - n_dir])
        a_para_b = (fb_c["cx"] - fa_c["cx"], fb_c["cy"] - fa_c["cy"])
        b_para_a = (-a_para_b[0], -a_para_b[1])
        return {
            "gap_contato": gap_c, "aproximacao_norm": aproximacao,
            "v_pre_a": v_a, "v_pre_b": v_b,
            "angulo_direcoes": angulo_entre(dir_a, dir_b) if dir_a and dir_b else None,
            # 0 graus = o track vinha de frente para o outro; 90 graus = vinha de lado
            "alinhamento_a": angulo_entre(dir_a, a_para_b) if dir_a else None,
            "alinhamento_b": angulo_entre(dir_b, b_para_a) if dir_b else None,
        }, None

    @staticmethod
    def velocidade_mediana(features, desde):
        """Mediana da velocidade normalizada a partir do quadro 'desde' (se nao houver, usa todos os quadros)."""
        recentes = [f["velocidade_norm"] for f in features if f["frame"] >= desde]
        return median(recentes or [f["velocidade_norm"] for f in features])

    def direcao_media(self, features):
        """Vetor medio de velocidade (px/s). None se o track estava praticamente parado."""
        if not features:
            return None
        mx = sum(f["vx"] for f in features) / len(features)
        my = sum(f["vy"] for f in features) / len(features)
        escala_media = sum(escala(f) for f in features) / len(features)
        if math.hypot(mx, my) / escala_media < self.cfg["velocidade_min_direcao"]:
            return None
        return (mx, my)

    # --- depois do contato
    def observar(self, estado, quadro):
        """Mede a mudanca de movimento depois do contato (portao 3). Devolve o candidato ou None."""
        n_pos = self.n_quadros(self.cfg["janela_pos_s"])
        completo = quadro - estado["c"] >= n_pos
        pos = self.calcular_pos(estado, quadro)
        evidencia = self.tem_evidencia(estado["tipo"], pos)
        candidato = None
        if evidencia:
            estado["teve_candidato"] = True
            candidato = self.montar_candidato(estado, pos, quadro, completo)
            self.finais[(min(estado["a"], estado["b"]), max(estado["a"], estado["b"]))] = candidato
        if completo:
            estado["estado"] = "encerrado"
            if not estado["teve_candidato"]:
                self.descartes.append({
                    "frame_contato": estado["c"], "tempo_contato_s": round(estado["c"] / self.fps, 2),
                    "track_a": estado["a"], "track_b": estado["b"], "motivo": "sem_mudanca_de_movimento"})
        return candidato

    def calcular_pos(self, estado, quadro):
        """Features pos-contato de cada track do par, usando os quadros de c ate agora (maximo janela_pos_s)."""
        cfg, fps, c = self.cfg, self.fps, estado["c"]
        fim = min(quadro, c + self.n_quadros(cfg["janela_pos_s"]))
        fim_decel = c + self.n_quadros(cfg["janela_decel_s"])
        pos = {}
        for lado in ("a", "b"):
            ident = estado[lado]
            jan = self.janela(ident, c, fim)
            v_pre = estado["pre"]["v_pre_" + lado]
            se_movia = v_pre >= cfg["velocidade_min_pre"]
            decel = 0.0
            if se_movia:
                depois = [f["velocidade_norm"] for f in jan if c + self.n_quadros(0.3) <= f["frame"] <= fim_decel]
                if len(depois) >= 3:
                    decel = min(1.0, max(0.0, v_pre - median(depois)) / v_pre)
            parada = sum(f["velocidade_norm"] < cfg["velocidade_parado"] for f in jan) / fps if se_movia else 0.0
            pos[lado] = {
                "decel": decel, "parada_pos_s": parada,
                "parado_final": bool(jan) and jan[-1]["velocidade_norm"] < cfg["velocidade_parado"],
                "ultimo_quadro": self.historico[ident][-1]["frame"] if self.historico[ident] else None,
            }
        if estado["tipo"] == "pessoa_veiculo":
            pos["pessoa"] = self.calcular_pessoa(estado, quadro, fim)
        return pos

    def calcular_pessoa(self, estado, quadro, fim):
        """Queda da proporcao da caixa, deslocamento brusco e sumico do tracking da pessoa."""
        cfg, c, ident = self.cfg, estado["c"], estado["b"]
        n_pre = self.n_quadros(cfg["janela_pre_s"])
        pre = self.janela(ident, c - n_pre, c - 1)
        jan = self.janela(ident, c, fim)
        queda = desloc = 0.0
        if pre and jan:
            prop_pre = median(f["altura"] / max(f["largura"], 1.0) for f in pre)
            prop_pos = min(f["altura"] / max(f["largura"], 1.0) for f in jan)
            queda = max(0.0, 1 - prop_pos / prop_pre)
            v_pre = median(f["velocidade_norm"] for f in pre)
            desloc = max(0.0, max(f["velocidade_norm"] for f in jan) - v_pre)
        ultimo = self.historico[ident][-1]["frame"]
        sumiu = ultimo >= c and quadro - ultimo >= cfg["pessoa_quadros_sumico"]
        return {"queda_proporcao": queda, "desloc_brusco": desloc, "sumiu": sumiu}

    def tem_evidencia(self, tipo, pos):
        """Portao 3: houve mudanca de movimento depois do contato?"""
        cfg = self.cfg
        if tipo == "veiculo_veiculo":
            return any(pos[l]["decel"] >= cfg["desaceleracao_min"] and pos[l]["parada_pos_s"] >= cfg["parada_min_s"]
                       for l in ("a", "b"))
        p = pos["pessoa"]
        sinal_pessoa = (p["sumiu"] or p["queda_proporcao"] >= cfg["pessoa_queda_min"]
                        or p["desloc_brusco"] >= cfg["pessoa_desloc_min"])
        return sinal_pessoa and pos["a"]["decel"] >= cfg["pessoa_veiculo_reage_min"]

    def montar_candidato(self, estado, pos, quadro, completo):
        """Junta tudo em um dict plano (uma linha do CSV de candidatos)."""
        pre, c = estado["pre"], estado["c"]
        cand = {
            "frame": quadro, "tempo_s": round(quadro / self.fps, 2),
            "frame_contato": c, "tempo_contato_s": round(c / self.fps, 2),
            "tipo_par": estado["tipo"],
            "track_a": estado["a"], "classe_a": self.historico[estado["a"]][-1]["classe"],
            "track_b": estado["b"], "classe_b": self.historico[estado["b"]][-1]["classe"],
            "gap_contato": arredondar(pre["gap_contato"], 3),
            "aproximacao_norm": arredondar(pre["aproximacao_norm"]),
            "angulo_direcoes": arredondar(pre["angulo_direcoes"], 0),
            "alinhamento_a": arredondar(pre["alinhamento_a"], 0), "alinhamento_b": arredondar(pre["alinhamento_b"], 0),
        }
        for lado in ("a", "b"):
            cand.update({
                f"v_pre_{lado}": arredondar(pre["v_pre_" + lado]),
                f"decel_{lado}": arredondar(pos[lado]["decel"]),
                f"parada_pos_s_{lado}": arredondar(pos[lado]["parada_pos_s"]),
                f"parado_final_{lado}": pos[lado]["parado_final"],
            })
        pessoa = pos.get("pessoa")
        cand["queda_proporcao"] = arredondar(pessoa["queda_proporcao"]) if pessoa else None
        cand["desloc_brusco"] = arredondar(pessoa["desloc_brusco"]) if pessoa else None
        cand["pessoa_sumiu"] = pessoa["sumiu"] if pessoa else None
        cand["completo"] = completo
        return cand


# ---------------------------------------------------------------- execucao sobre o CSV de main.py

def ler_deteccoes(caminho_csv):
    """Le o CSV de main.py e agrupa as deteccoes por quadro."""
    por_quadro = defaultdict(list)
    with open(caminho_csv, encoding="utf-8") as f:
        for linha in csv.DictReader(f):
            por_quadro[int(linha["frame"])].append(linha)
    return por_quadro


def analisar_csv(caminho_csv, fps, n_quadros, config):
    """Roda o analisador quadro a quadro. Devolve (analisador, candidatos_por_quadro, features_por_quadro)."""
    cfg_traj = config["trajetorias"]
    deteccoes = ler_deteccoes(caminho_csv)
    analisador = AnalisadorTemporal(config, fps, cfg_traj["min_quadros"])
    historicos = {}
    candidatos_por_quadro, features_por_quadro = {}, {}
    for n in range(n_quadros):
        features = []
        for d in deteccoes.get(n, []):
            tid = int(d["track_id"])
            h = historicos.setdefault(tid, HistoricoTrack(tid, fps, cfg_traj["janela_suavizacao"]))
            features.append(h.adicionar(n, d["classe"], [float(d[k]) for k in ("x1", "y1", "x2", "y2")], d["conf"]))
        features_por_quadro[n] = {f["track_id"]: f for f in features}
        candidatos_por_quadro[n] = analisador.processar_quadro(n, features)
    return analisador, candidatos_por_quadro, features_por_quadro


# ---------------------------------------------------------------- depuracao visual

def texto_candidato(c):
    """Linhas de texto do painel de depuracao."""
    linhas = [f'par #{c["track_a"]} {c["classe_a"]} x #{c["track_b"]} {c["classe_b"]}  contato t={c["tempo_contato_s"]}s',
              f'aprox {c["aproximacao_norm"]}  gap {c["gap_contato"]}  ang_dir {c["angulo_direcoes"]}  '
              f'alinh {c["alinhamento_a"]}/{c["alinhamento_b"]}',
              f'A: v_pre {c["v_pre_a"]} decel {c["decel_a"]} parada {c["parada_pos_s_a"]}s',
              f'B: v_pre {c["v_pre_b"]} decel {c["decel_b"]} parada {c["parada_pos_s_b"]}s']
    if c["tipo_par"] == "pessoa_veiculo":
        linhas.append(f'pessoa: queda {c["queda_proporcao"]} desloc {c["desloc_brusco"]} sumiu {c["pessoa_sumiu"]}')
    return linhas


def gerar_depuracao(caminho_video, destino_video, pasta, nome, deteccoes, candidatos_por_quadro, finais, fps, cfg):
    """Video com todas as caixas (cinza) e os pares candidatos destacados com as features."""
    cap = cv2.VideoCapture(str(caminho_video))
    largura, altura = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    gravador = cv2.VideoWriter(str(destino_video), cv2.VideoWriter_fourcc(*"mp4v"), fps, (largura, altura))
    n_pos = max(1, round(cfg["janela_pos_s"] * fps))
    segurar = int(2 * fps)                     # mantem o destaque 2 s depois da janela
    ultimo = {}                                # chave -> ultimo candidato emitido (para o painel)
    salvas = set()
    n = 0
    while True:
        ok, quadro = cap.read()
        if not ok:
            break
        caixas = {int(d["track_id"]): [int(float(d[k])) for k in ("x1", "y1", "x2", "y2")] for d in deteccoes.get(n, [])}
        for tid, (x1, y1, x2, y2) in caixas.items():
            cv2.rectangle(quadro, (x1, y1), (x2, y2), (160, 160, 160), 1)
            cv2.putText(quadro, f"#{tid}", (x1, max(y1 - 4, 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)
        for c in candidatos_por_quadro.get(n, []):
            ultimo[(min(c["track_a"], c["track_b"]), max(c["track_a"], c["track_b"]))] = c
        painel = 20
        for chave, c in finais.items():
            if not (c["frame_contato"] - int(fps) <= n <= c["frame_contato"] + n_pos + segurar):
                continue
            atual = ultimo.get(chave, c)
            fase = ("PRE" if n < c["frame_contato"] else "CONTATO" if n == c["frame_contato"]
                    else "OBSERVANDO" if n < c["frame_contato"] + n_pos else "COMPLETO")
            pontos = []
            for tid in chave:
                if tid in caixas:
                    x1, y1, x2, y2 = caixas[tid]
                    cv2.rectangle(quadro, (x1, y1), (x2, y2), (0, 0, 255), 3)
                    pontos.append(((x1 + x2) // 2, (y1 + y2) // 2))
            if len(pontos) == 2:
                cv2.line(quadro, pontos[0], pontos[1], (0, 255, 255), 2)
            linhas = [f"[{fase}] frame {n}"] + texto_candidato(atual)
            fundo = quadro.copy()
            cv2.rectangle(fundo, (0, painel - 16), (min(largura, 760), painel + 18 * (len(linhas) - 1) + 6), (0, 0, 0), -1)
            quadro = cv2.addWeighted(fundo, 0.6, quadro, 0.4, 0)
            for texto in linhas:
                cv2.putText(quadro, texto, (10, painel), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
                painel += 18
            painel += 10
            if n == c["frame_contato"] and chave not in salvas:    # imagem do momento do contato, com as features finais
                salvas.add(chave)
                cv2.imwrite(str(pasta / f"{nome}_candidato_{chave[0]}_{chave[1]}.jpg"), quadro)
        gravador.write(quadro)
        n += 1
    cap.release()
    gravador.release()


def salvar_csv(linhas, campos, caminho):
    with open(caminho, "w", newline="", encoding="utf-8") as f:
        escritor = csv.DictWriter(f, fieldnames=campos)
        escritor.writeheader()
        escritor.writerows(linhas)


def main():
    ap = argparse.ArgumentParser(description="Analise temporal de pares de tracks (depuracao)")
    ap.add_argument("--video", required=True)
    ap.add_argument("--csv", help="CSV de main.py (padrao: saida/logs/<video>_trajetorias.csv)")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    config = carregar_config(args.config)
    pasta = Path(config["saida"]["logs"])
    nome = Path(args.video).stem
    csv_entrada = Path(args.csv) if args.csv else pasta / f"{nome}_trajetorias.csv"
    if not csv_entrada.exists():
        raise SystemExit(f"nao achei {csv_entrada}; rode antes: python main.py --video {args.video}")

    cap = cv2.VideoCapture(args.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n_quadros = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()

    analisador, por_quadro, _ = analisar_csv(csv_entrada, fps, n_quadros, config)
    finais = analisador.finais
    if finais:
        salvar_csv(list(finais.values()), list(next(iter(finais.values()))), pasta / f"{nome}_candidatos.csv")
    salvar_csv(analisador.descartes, CAMPOS_DESCARTE, pasta / f"{nome}_descartes.csv")
    for antigo in pasta.glob(f"{nome}_candidato_*.jpg"):   # imagens de rodadas anteriores
        antigo.unlink()
    gerar_depuracao(args.video, pasta / f"{nome}_depuracao.mp4", pasta, nome, ler_deteccoes(csv_entrada),
                    por_quadro, finais, fps, config["analise_temporal"])

    print(f"{len(finais)} candidato(s), {len(analisador.descartes)} par(es) com contato descartado(s)")
    motivos = defaultdict(int)
    for d in analisador.descartes:
        motivos[d["motivo"]] += 1
    for motivo, qtd in sorted(motivos.items()):
        print(f"  descartados por {motivo}: {qtd}")
    for c in finais.values():
        print("  " + " | ".join(texto_candidato(c)))


if __name__ == "__main__":
    main()
