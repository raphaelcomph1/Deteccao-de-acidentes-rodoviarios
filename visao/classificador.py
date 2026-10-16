"""Classificador: transforma um candidato da analise temporal em tipo + confianca + nivel.

Uso (depuracao): python classificador.py --video caminho.mp4   (precisa do CSV de main.py)

Confianca = soma ponderada de evidencias (cada uma de 0 a 1, de forma gradual) menos penalidades de
qualidade da imagem. A saida traz a contribuicao de cada evidencia para explicar o escore no relatorio.
Formula, pesos e limites: docs/algoritmo_visao.md.

Evidencias de colisao (traseira ou lateral):
  contato        profundidade do contato e velocidade de aproximacao antes dele
  desaceleracao  queda relativa da velocidade logo depois do contato
  parada         tempo parado depois do contato
  geometria      coerencia entre os angulos e o tipo (traseira ou lateral)
Atropelamento (pesos conservadores): contato, veiculo_reage, pessoa (queda/deslocamento/sumico), geometria.
"""

import argparse
from pathlib import Path

import cv2

from analise_temporal import analisar_csv
from eventos import decidir_tipo
from util import carregar_config


def rampa(x, x0, x1):
    """Interpola linearmente de 0 (em x0) a 1 (em x1), limitando em [0, 1]. Funciona com x1 < x0."""
    if x is None:
        return 0.0
    return max(0.0, min(1.0, (x - x0) / (x1 - x0)))


# ---------------------------------------------------------------- geometria

def geometria_colisao(cand, cfg, vel_min):
    """Devolve (g_traseira, g_lateral, como). Cada g vai de 0 a 1.

    Um veiculo parado nao tem direcao confiavel: com so um em movimento usa-se o angulo entre a direcao
    dele e o vetor que liga os dois centros (alinhado = traseira, de lado = lateral). O angulo entre as
    duas direcoes so e usado quando os dois estao em movimento."""
    move_a = cand["v_pre_a"] >= vel_min and cand["alinhamento_a"] is not None
    move_b = cand["v_pre_b"] >= vel_min and cand["alinhamento_b"] is not None
    beta = cand["angulo_direcoes"]

    if move_a and move_b and beta is not None:
        menor_alinh = min(cand["alinhamento_a"], cand["alinhamento_b"])
        g_tras = rampa(beta, cfg["traseira_zero"], cfg["traseira_pleno"]) * \
            rampa(menor_alinh, cfg["alinhamento_tras_dois_zero"], cfg["alinhamento_tras_dois_pleno"])
        g_lat = rampa(beta, cfg["lateral_zero"], cfg["lateral_pleno"]) * \
            rampa(beta, cfg["lateral_max_zero"], cfg["lateral_max_pleno"])
        return g_tras, g_lat, f"dois em movimento: angulo entre direcoes {beta:.0f} graus"

    if move_a != move_b:
        alinh = cand["alinhamento_a"] if move_a else cand["alinhamento_b"]
        g_tras = rampa(alinh, cfg["alinhamento_traseira_zero"], cfg["alinhamento_traseira_pleno"])
        g_lat = rampa(alinh, cfg["alinhamento_lateral_zero"], cfg["alinhamento_lateral_pleno"])
        return g_tras, g_lat, f"um em movimento: alinhamento {alinh:.0f} graus"

    return 0.0, 0.0, "sem direcao confiavel"


def coerencia(g_proprio, g_outro):
    """Coerencia com o tipo escolhido: desconta a geometria do outro tipo (angulo ambiguo, que serve aos dois, vale pouco)."""
    return max(0.0, g_proprio - g_outro)


# ---------------------------------------------------------------- escore

def somar(evidencias, pesos):
    """Lista de contribuicoes (valor x peso) para cada evidencia, na ordem dos pesos."""
    return [{"evidencia": nome, "valor": round(evidencias[nome], 3), "peso": peso,
             "contribuicao": round(evidencias[nome] * peso, 3)} for nome, peso in pesos.items()]


def penalidades(cand, cfg):
    """Penalidades por baixa confianca media das deteccoes e por tracking instavel (visibilidade ruim)."""
    lista = []
    if cand.get("conf_media") is not None:
        valor = cfg["penalidade_max"] * rampa(cand["conf_media"], cfg["conf_ref"], cfg["conf_min"])
        lista.append({"evidencia": "penal_conf_deteccao", "valor": cand["conf_media"], "contribuicao": round(-valor, 3)})
    if cand.get("continuidade") is not None:
        valor = cfg["penalidade_max"] * rampa(cand["continuidade"], cfg["continuidade_ref"], cfg["continuidade_min"])
        lista.append({"evidencia": "penal_continuidade_track", "valor": cand["continuidade"], "contribuicao": round(-valor, 3)})
    return lista


def evidencia_contato(cand, cfg):
    """Contato: metade pela profundidade (sobreposto = 1) e metade pela velocidade de aproximacao."""
    limite = 0.10   # contato_gap do analise_temporal; o gap do candidato nunca passa disto
    profundidade = 1.0 - min(cand["gap_contato"], limite) / limite
    return 0.5 * profundidade + 0.5 * rampa(cand["aproximacao_norm"], 0.0, cfg["aproximacao_ref"])


def classificar_colisao(cand, cfg, vel_min):
    """Escore de uma colisao veiculo-veiculo. Devolve (tipo_detectado, escore, contribuicoes, geometria)."""
    g_tras, g_lat, como = geometria_colisao(cand, cfg, vel_min)
    tipo = "COLISAO_TRASEIRA" if g_tras >= g_lat else "COLISAO_LATERAL"
    geom = coerencia(g_tras, g_lat) if tipo == "COLISAO_TRASEIRA" else coerencia(g_lat, g_tras)
    # o veiculo que mais perdeu velocidade e o que conta para desaceleracao e parada
    lado = "a" if cand["decel_a"] >= cand["decel_b"] else "b"
    evidencias = {
        "contato": evidencia_contato(cand, cfg),
        "desaceleracao": rampa(cand["decel_" + lado], cfg["desaceleracao_zero"], cfg["desaceleracao_cheia"]),
        "parada": rampa(cand["parada_pos_s_" + lado], 0.0, cfg["parada_ref_s"]),
        "geometria": geom,
    }
    contribuicoes = somar(evidencias, cfg["pesos_colisao"])
    geometria = {"g_traseira": round(g_tras, 3), "g_lateral": round(g_lat, 3), "como": como, "veiculo_que_freou": lado}
    return tipo, sum(c["contribuicao"] for c in contribuicoes), contribuicoes, geometria


def classificar_atropelamento(cand, cfg, vel_min):
    """Escore de um atropelamento (par pessoa-veiculo, a = veiculo). Pesos conservadores e com teto."""
    queda = rampa(cand["queda_proporcao"], 0.0, cfg["pessoa_queda_ref"])
    desloc = rampa(cand["desloc_brusco"], 0.0, cfg["pessoa_desloc_ref"])
    sumiu = 1.0 if cand["pessoa_sumiu"] else 0.0
    sinal_pessoa = 0.5 * queda + 0.3 * desloc + 0.2 * sumiu
    # um sinal isolado pode ser ruido (caixa que oscila) ou oclusao (pessoa escondida atras do veiculo):
    # so vale por inteiro quando pelo menos dois sinais aparecem juntos
    n_sinais = sum(v >= 0.3 for v in (queda, desloc, sumiu))
    if n_sinais < 2:
        sinal_pessoa *= 0.5
    alinh = cand["alinhamento_a"] if cand["v_pre_a"] >= vel_min else None
    evidencias = {
        "contato": evidencia_contato(cand, cfg),
        "veiculo_reage": rampa(cand["decel_a"], 0.0, cfg["desaceleracao_cheia"]),
        "pessoa": sinal_pessoa,
        "geometria": rampa(alinh, cfg["alinhamento_traseira_zero"], cfg["alinhamento_traseira_pleno"]),
    }
    contribuicoes = somar(evidencias, cfg["pesos_atropelamento"])
    geometria = {"como": "veiculo vindo de frente para a pessoa" if alinh is not None else "sem direcao confiavel",
                 "queda": round(queda, 3), "desloc": round(desloc, 3), "sumiu": bool(sumiu)}
    return "ATROPELAMENTO", sum(c["contribuicao"] for c in contribuicoes), contribuicoes, geometria


def classificar(cand, config):
    """Classifica um candidato. Devolve dict com tipo_detectado, confianca, tipo, nivel e as contribuicoes."""
    cfg = config["classificacao"]
    vel_min = config["analise_temporal"]["velocidade_min_pre"]
    if cand["tipo_par"] == "pessoa_veiculo":
        tipo_detectado, escore, contribuicoes, geometria = classificar_atropelamento(cand, cfg, vel_min)
    else:
        tipo_detectado, escore, contribuicoes, geometria = classificar_colisao(cand, cfg, vel_min)
    penal = penalidades(cand, cfg)
    escore += sum(p["contribuicao"] for p in penal)
    if tipo_detectado == "ATROPELAMENTO":
        escore = min(escore, cfg["teto_atropelamento"])
    elif cand.get("menor_caixa_px") is not None and cand["menor_caixa_px"] < cfg["teto_resolucao_caixa_px"] \
            and escore > cfg["teto_resolucao_confianca"]:
        penal.append({"evidencia": "teto_resolucao_caixa", "valor": cand["menor_caixa_px"],
                      "contribuicao": round(cfg["teto_resolucao_confianca"] - escore, 3)})
        escore = cfg["teto_resolucao_confianca"]
    confianca = max(0.0, min(1.0, escore))
    tipo, nivel = decidir_tipo(tipo_detectado, round(confianca, 2), cfg["limiar_alto"], cfg["limiar_medio"])
    return {
        "tipo_detectado": tipo_detectado, "tipo": tipo, "nivel": nivel, "confianca": round(confianca, 2),
        "contribuicoes": contribuicoes + penal, "geometria": geometria,
        "track_a": cand["track_a"], "track_b": cand["track_b"], "frame_contato": cand["frame_contato"],
        "tempo_contato_s": cand["tempo_contato_s"],
    }


def classificar_candidatos(candidatos, config):
    """Classifica todos os candidatos e devolve o de maior confianca (ou SEM_ACIDENTE se nao houver nenhum)."""
    resultados = [classificar(c, config) for c in candidatos]
    if not resultados:
        return {"tipo_detectado": None, "tipo": "SEM_ACIDENTE", "nivel": "BAIXO", "confianca": 0.0,
                "contribuicoes": [], "geometria": {}, "track_a": None, "track_b": None,
                "frame_contato": None, "tempo_contato_s": None}
    return max(resultados, key=lambda r: r["confianca"])


def texto_contribuicoes(resultado):
    """Uma linha por evidencia: valor x peso = contribuicao (para o log e o relatorio)."""
    linhas = []
    for c in resultado["contribuicoes"]:
        if "peso" in c:
            linhas.append(f'    {c["evidencia"]:<26} valor {c["valor"]:.2f} x peso {c["peso"]:.2f} = {c["contribuicao"]:+.3f}')
        else:
            linhas.append(f'    {c["evidencia"]:<26} valor {c["valor"]:.2f}                = {c["contribuicao"]:+.3f}')
    return "\n".join(linhas)


def main():
    ap = argparse.ArgumentParser(description="Classifica os candidatos de um video (depuracao)")
    ap.add_argument("--video", required=True)
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    config = carregar_config(args.config)
    nome = Path(args.video).stem
    csv_entrada = Path(config["saida"]["logs"]) / f"{nome}_trajetorias.csv"
    if not csv_entrada.exists():
        raise SystemExit(f"nao achei {csv_entrada}; rode antes: python main.py --video {args.video}")
    cap = cv2.VideoCapture(args.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n_quadros = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()

    analisador, _, _ = analisar_csv(csv_entrada, fps, n_quadros, config)
    resultado = classificar_candidatos(list(analisador.finais.values()), config)
    print(f'{nome}: {resultado["tipo"]} (detectado: {resultado["tipo_detectado"]}) '
          f'confianca {resultado["confianca"]:.2f} nivel {resultado["nivel"]}')
    print(texto_contribuicoes(resultado))
    print("   ", resultado["geometria"])


if __name__ == "__main__":
    main()
