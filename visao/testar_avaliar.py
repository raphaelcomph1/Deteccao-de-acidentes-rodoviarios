"""Teste do avaliar.py com gabarito falso em arquivo temporario (nao toca em dados/gabarito.csv)."""

import csv
import tempfile
from pathlib import Path

import avaliar

GABARITO_FALSO = """video,rotulo_esperado,conjunto,instante_impacto_s,descricao,responsavel,nome_original,fonte,observacao
a.mp4,COLISAO_TRASEIRA,AJUSTE,5.0,,,,,
b.mp4,COLISAO_LATERAL,AJUSTE,3.0,,,,,
c.mp4,ATROPELAMENTO,AJUSTE,8.0,,,,,
d.mp4,SEM_ACIDENTE,AJUSTE,,,,,,
e.mp4,SEM_ACIDENTE,AJUSTE,,,,,,
f.mp4,COLISAO_TRASEIRA,RESERVADO,2.0,,,,,
g.mp4,,AJUSTE,,,,,,
h.mp4,COLISAO_LATERAL,AJUSTE,4.0,,,,,
i.mp4,COLISAO_TRASEIRA,AJUSTE,6.0,,,,,
"""

# respostas combinadas so para este teste; o detector real nunca olha o nome do arquivo
RESPOSTAS = {
    "a.mp4": ("COLISAO_TRASEIRA", 0.85, "evt-CAM01-X-0001", 5.2),   # acerto
    "b.mp4": ("COLISAO_TRASEIRA", 0.75, "evt-CAM01-X-0002", 3.1),   # classificacao errada
    "c.mp4": ("SEM_ACIDENTE", 0.20, None, None),                     # falso negativo
    "d.mp4": ("SEM_ACIDENTE", 0.10, None, None),                     # acerto (negativo verdadeiro)
    "e.mp4": ("COLISAO_LATERAL", 0.80, "evt-CAM01-X-0003", 7.0),    # falso positivo
    "f.mp4": ("COLISAO_TRASEIRA", 0.90, "evt-CAM01-X-0004", 2.0),   # fora do conjunto AJUSTE
    "h.mp4": ("INDETERMINADO", 0.55, "evt-CAM01-X-0005", 4.5),      # indeterminado
    "i.mp4": None,                                                   # falha ao analisar
}


def analisador_falso(caminho, config):
    resposta = RESPOSTAS[caminho.name]
    if resposta is None:
        raise RuntimeError("video corrompido")
    tipo, conf, event_id, instante = resposta
    return {"tipo": tipo, "confianca": conf, "event_id": event_id, "instante_s": instante}


def main():
    with tempfile.TemporaryDirectory() as tmp:
        gabarito = Path(tmp) / "gabarito_falso.csv"
        gabarito.write_text(GABARITO_FALSO, encoding="utf-8")
        resultados, resumo, caminho = avaliar.avaliar(
            gabarito, "AJUSTE", "videos_falsos", {}, analisar=analisador_falso, pasta_saida=Path(tmp) / "res")

        assert [r["video"] for r in resultados] == ["a.mp4", "b.mp4", "c.mp4", "d.mp4", "e.mp4", "h.mp4"]
        erros = {r["video"]: r["tipo_erro"] for r in resultados}
        assert erros == {"a.mp4": "", "b.mp4": "classificacao_errada", "c.mp4": "falso_negativo",
                         "d.mp4": "", "e.mp4": "falso_positivo", "h.mp4": "indeterminado"}, erros
        assert resumo["total"] == 6 and resumo["acertos"] == 2
        assert resumo["falsos_positivos"] == 1 and resumo["falsos_negativos"] == 1
        assert resumo["classificacoes_erradas"] == 1 and resumo["indeterminados"] == 1
        # deteccao: TP = a, b, h (3); FP = e (1); FN = c (1)
        assert resumo["precisao"] == 3 / 4 and resumo["recall"] == 3 / 4, resumo
        with open(caminho, encoding="utf-8") as f:
            linhas = list(csv.DictReader(f))
        assert list(linhas[0]) == avaliar.CAMPOS_RESULTADO and len(linhas) == 6
        assert caminho.name.startswith("resultados_AJUSTE_")

        # sem filtro: inclui o RESERVADO
        todos, _, caminho2 = avaliar.avaliar(gabarito, None, "videos_falsos", {}, analisar=analisador_falso,
                                             pasta_saida=Path(tmp) / "res")
        assert "f.mp4" in [r["video"] for r in todos] and caminho2.name.startswith("resultados_TODOS_")

        # o stub real deve falhar de forma clara
        try:
            avaliar.avaliar(gabarito, "AJUSTE", "videos_falsos", {}, pasta_saida=Path(tmp) / "res")
        except NotImplementedError:
            pass
        else:
            raise AssertionError("o stub deveria levantar NotImplementedError")

        avaliar.imprimir_resumo(resumo, caminho)
    print("\nTodos os testes passaram.")


if __name__ == "__main__":
    main()
