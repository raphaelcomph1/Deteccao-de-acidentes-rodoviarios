"""Testes do classificador com cenas sinteticas (reaproveita as cenas de testar_analise_temporal.py)."""

from classificador import classificar_candidatos
from testar_analise_temporal import caixa, rodar_cena
from util import carregar_config

CONFIG = carregar_config("config.yaml")


def classificar_cena(cena, n_quadros=150, conf=0.9):
    analisador = rodar_cena(cena, n_quadros, conf)
    return classificar_candidatos(list(analisador.finais.values()), CONFIG)


def traseira(parada_s=None):
    """A (movendo a 300 px/s) bate em B parado no quadro 60 e para; se parada_s, volta a andar devagar depois."""
    def a(n):
        if parada_s and n > 60 + round(parada_s * 30):
            return caixa(700 + 3 * (n - 60 - round(parada_s * 30)), 200)
        return caixa(100 + 10 * min(n, 60), 200)
    return {1: ("car", a), 2: ("car", lambda n: caixa(762, 200))}


def cruzamento(angulo_graus):
    """A anda para a direita; B chega ao ponto de encontro com o angulo dado entre as direcoes. Os dois param no quadro 60."""
    import math
    vx, vy = 10 * math.cos(math.radians(angulo_graus)), 10 * math.sin(math.radians(angulo_graus))
    return {1: ("car", lambda n: caixa(100 + 10 * min(n, 60), 300)),
            2: ("car", lambda n: caixa(730 - vx * (60 - min(n, 60)), 300 - vy * (60 - min(n, 60))))}


def atropelamento(sinais):
    """Carro a 300 px/s bate na pessoa no quadro 60 e para. 'sinais' diz o que acontece com a pessoa depois."""
    def pessoa(n):
        # o contato so e confirmado uns quadros depois do impacto (suavizacao + 2 quadros seguidos),
        # entao a pessoa so reage a partir do quadro 68
        if n >= 76 and "sumir" in sinais:
            return None
        altura = 50 if n < 68 or "cair" not in sinais else 20
        largura = 20 if n < 68 or "cair" not in sinais else 50
        deslocamento = 40 * max(0, n - 68) if "voar" in sinais and n > 68 else 0
        return caixa(750 + min(deslocamento, 200), 200, largura, altura)
    return {1: ("car", lambda n: caixa(100 + 10 * min(n, 60), 200, 80, 50)), 2: ("person", pessoa)}


def teste_traseira_forte_e_alto():
    r = classificar_cena(traseira())
    assert r["tipo"] == "COLISAO_TRASEIRA" and r["nivel"] == "ALTO", r


def teste_lateral_forte_e_alto():
    r = classificar_cena(cruzamento(90))
    assert r["tipo"] == "COLISAO_LATERAL" and r["nivel"] == "ALTO", r


def teste_angulo_ambiguo_nao_chega_a_alto():
    r = classificar_cena(cruzamento(40))
    assert r["nivel"] != "ALTO" and r["tipo"] in ("INDETERMINADO", "SEM_ACIDENTE"), r


def teste_parada_curta_pontua_menos_que_parada_longa():
    curta, longa = classificar_cena(traseira(0.6)), classificar_cena(traseira())
    assert curta["tipo_detectado"] and curta["confianca"] < longa["confianca"], (curta, longa)


def teste_baixa_confianca_de_deteccao_penaliza():
    boa, ruim = classificar_cena(traseira(), conf=0.9), classificar_cena(traseira(), conf=0.3)
    assert ruim["confianca"] < boa["confianca"]


def teste_atropelamento_forte_chega_a_alto_com_teto():
    r = classificar_cena(atropelamento({"cair", "voar", "sumir"}))
    assert r["tipo"] == "ATROPELAMENTO" and r["nivel"] == "ALTO" and r["confianca"] <= 0.85, r


def teste_atropelamento_com_um_sinal_so_e_indeterminado():
    for sinal in ("sumir", "cair"):
        r = classificar_cena(atropelamento({sinal}))
        assert r["tipo"] == "INDETERMINADO" and r["tipo_detectado"] == "ATROPELAMENTO", (sinal, r)


def teste_sem_candidato_e_sem_acidente():
    r = classificar_cena({1: ("car", lambda n: caixa(100, 200)), 2: ("car", lambda n: caixa(400, 200))})
    assert r["tipo"] == "SEM_ACIDENTE" and r["confianca"] == 0.0


if __name__ == "__main__":
    testes = [v for k, v in sorted(globals().items()) if k.startswith("teste_")]
    falhas = 0
    for t in testes:
        try:
            t()
            print("ok:", t.__name__)
        except AssertionError as erro:
            falhas += 1
            print("FALHOU:", t.__name__, str(erro)[:400])
    raise SystemExit(1 if falhas else 0)
