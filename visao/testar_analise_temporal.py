"""Testes da analise temporal com cenas sinteticas (nao usa video nem o gabarito).

Cada cena e uma lista de caixas por quadro; o resultado esperado esta no nome do teste.
"""

from analise_temporal import AnalisadorTemporal
from trajetorias import HistoricoTrack
from util import carregar_config

FPS = 30.0


def rodar_cena(cena, n_quadros, conf=0.9):
    """cena: {track_id: (classe, funcao(quadro) -> caixa ou None)}. Devolve o analisador depois de todos os quadros."""
    config = carregar_config("config.yaml")
    analisador = AnalisadorTemporal(config, FPS, config["trajetorias"]["min_quadros"])
    historicos = {tid: HistoricoTrack(tid, FPS, config["trajetorias"]["janela_suavizacao"]) for tid in cena}
    for n in range(n_quadros):
        features = []
        for tid, (classe, caixa_em) in cena.items():
            caixa = caixa_em(n)
            if caixa:
                features.append(historicos[tid].adicionar(n, classe, caixa, conf))
        analisador.processar_quadro(n, features)
    return analisador


def caixa(cx, cy, w=60, h=40):
    return [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]



def teste_colisao_traseira_gera_candidato():
    cena = {1: ("car", lambda n: caixa(100 + 10 * min(n, 60), 200)),
            2: ("car", lambda n: caixa(100 + 10 * 60 + 62, 200))}
    a = rodar_cena(cena, 150)
    assert len(a.finais) == 1, a.descartes
    c = next(iter(a.finais.values()))
    assert c["decel_a"] >= 0.9 and c["parada_pos_s_a"] > 1.0, c


def teste_dois_carros_parados_lado_a_lado_nao_geram():
    cena = {1: ("car", lambda n: caixa(100, 200)), 2: ("car", lambda n: caixa(162, 200))}
    a = rodar_cena(cena, 150)
    assert not a.finais


def teste_frenagem_sem_contato_nao_gera():
    cena = {1: ("car", lambda n: caixa(100 + 10 * min(n, 60), 200)),
            2: ("car", lambda n: caixa(1500, 200))}
    a = rodar_cena(cena, 150)
    assert not a.finais and not a.descartes


def teste_sobreposicao_sem_mudanca_de_movimento_nao_gera():
    """Um carro ultrapassa outro (caixas se cruzam), os dois mantem a velocidade."""
    cena = {1: ("car", lambda n: caixa(100 + 10 * n, 200)),
            2: ("car", lambda n: caixa(400 + 5 * n, 215))}
    a = rodar_cena(cena, 150)
    assert not a.finais


def teste_atropelamento_sintetico_gera_candidato():
    """Carro a 300 px/s bate na pessoa no quadro 60; o carro para e a pessoa sai do tracking."""
    cena = {1: ("car", lambda n: caixa(100 + 10 * min(n, 60), 200, 80, 50)),
            2: ("person", lambda n: caixa(100 + 600 + 50, 200, 20, 50) if n < 68 else None)}
    a = rodar_cena(cena, 150)
    assert len(a.finais) == 1, a.descartes
    c = next(iter(a.finais.values()))
    assert c["tipo_par"] == "pessoa_veiculo" and c["pessoa_sumiu"], c


def teste_pessoa_perto_de_veiculo_parado_nao_gera():
    cena = {1: ("car", lambda n: caixa(300, 200, 80, 50)), 2: ("person", lambda n: caixa(350, 200, 20, 50))}
    a = rodar_cena(cena, 150)
    assert not a.finais


if __name__ == "__main__":
    testes = [v for k, v in sorted(globals().items()) if k.startswith("teste_")]
    for t in testes:
        t()
        print("ok:", t.__name__)
    print("Todos os testes passaram.")
