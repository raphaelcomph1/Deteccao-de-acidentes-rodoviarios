"""Testes do GerenciadorEventos (event_id unico, revisao, indicadores e contrato) com candidatos falsos."""

from eventos import GerenciadorEventos, validar_evento
from util import carregar_config

CONFIG = carregar_config("config.yaml")
FPS = 30.0


def candidato(a=1, b=2, contato=100, tipo_par="veiculo_veiculo", parado=False):
    return {"track_a": a, "track_b": b, "frame_contato": contato, "tempo_contato_s": round(contato / FPS, 2),
            "tipo_par": tipo_par, "parado_final_a": parado, "parado_final_b": False}


def resultado(confianca, tipo_detectado="COLISAO_TRASEIRA"):
    from eventos import decidir_tipo
    tipo, nivel = decidir_tipo(tipo_detectado, confianca)
    return {"tipo": tipo, "nivel": nivel, "confianca": confianca, "tipo_detectado": tipo_detectado}


def novo():
    return GerenciadorEventos(CONFIG, "CAM01", "Rodovia ficticia", "video_x.mp4")


def teste_primeiro_evento_e_valido_e_tem_revisao_1():
    ev = novo().atualizar(candidato(), resultado(0.55), FPS)
    assert ev["revisao"] == 1 and ev["tipo"] == "INDETERMINADO" and ev["nivel_confianca"] == "MEDIO"
    assert validar_evento(ev) == [], validar_evento(ev)
    assert ev["evidencia"]["arquivo"] == f'visao/saida/evidencias/{ev["event_id"]}.jpg'
    assert ev["recomendacao"] == {"recurso": "REVISAO_OPERADOR", "regra": "R4"}


def teste_sem_acidente_nao_gera_evento():
    assert novo().atualizar(candidato(), resultado(0.30), FPS) is None


def teste_mesmo_acidente_mantem_event_id_e_incrementa_revisao():
    g = novo()
    e1 = g.atualizar(candidato(), resultado(0.55), FPS)
    assert g.atualizar(candidato(), resultado(0.57), FPS) is None            # subiu menos que delta_revisao
    e2 = g.atualizar(candidato(), resultado(0.75), FPS)
    assert e2["event_id"] == e1["event_id"] and e2["revisao"] == 2 and e2["tipo"] == "COLISAO_TRASEIRA"
    assert g.atualizar(candidato(), resultado(0.60), FPS) is None            # confianca que cai nao reenvia
    assert e2["timestamp"] == e1["timestamp"]


def teste_par_com_track_compartilhado_na_janela_e_o_mesmo_acidente():
    g = novo()
    e1 = g.atualizar(candidato(1, 2, contato=100), resultado(0.55), FPS)
    e2 = g.atualizar(candidato(2, 9, contato=130), resultado(0.80), FPS)       # o ID do outro veiculo mudou no impacto
    assert e2["event_id"] == e1["event_id"] and e2["revisao"] == 2


def teste_acidente_distante_no_tempo_ou_em_outros_tracks_e_outro_evento():
    g = novo()
    e1 = g.atualizar(candidato(1, 2, contato=100), resultado(0.80), FPS)
    e3 = g.atualizar(candidato(5, 6, contato=100), resultado(0.80), FPS)             # outros tracks
    assert e3["event_id"] != e1["event_id"] and e3["revisao"] == 1
    g2 = novo()
    a = g2.atualizar(candidato(1, 2, contato=100), resultado(0.80), FPS)
    b = g2.atualizar(candidato(2, 7, contato=100 + 10 * 30), resultado(0.80), FPS)   # compartilha track, mas 10 s depois
    assert b["event_id"] != a["event_id"]


def teste_indicadores_e_recomendacao():
    g = novo()
    e1 = g.atualizar(candidato(parado=False), resultado(0.80), FPS)
    assert e1["indicadores"] == {"veiculos_envolvidos": 2, "pessoa_envolvida": False,
                                 "veiculo_parado_na_pista": False, "faixa_obstruida": "desconhecido"}
    assert e1["recomendacao"]["regra"] == "R1"
    e2 = g.atualizar(candidato(parado=True), resultado(0.80), FPS)           # passou a ficar parado: reenvia
    assert e2["revisao"] == 2 and e2["recomendacao"]["regra"] == "R2"
    p = novo().atualizar(candidato(tipo_par="pessoa_veiculo"), resultado(0.80, "ATROPELAMENTO"), FPS)
    assert p["indicadores"]["pessoa_envolvida"] is True and p["indicadores"]["veiculos_envolvidos"] == 1
    assert p["recomendacao"]["regra"] == "R3"


if __name__ == "__main__":
    falhas = 0
    for t in [v for k, v in sorted(globals().items()) if k.startswith("teste_")]:
        try:
            t()
            print("ok:", t.__name__)
        except AssertionError as erro:
            falhas += 1
            print("FALHOU:", t.__name__, erro)
    raise SystemExit(1 if falhas else 0)
