"""Avaliacao: roda os videos do gabarito e compara o resultado com o rotulo esperado.

ESTE E O UNICO ARQUIVO DO PROJETO AUTORIZADO A LER dados/gabarito.csv.
O codigo de deteccao nunca recebe o rotulo, o conjunto nem qualquer coluna do gabarito.

Uso: python avaliar.py --conjunto AJUSTE   (ou RESERVADO; sem --conjunto avalia todos)

Tipos de erro:
- falso_positivo: esperado SEM_ACIDENTE, obtido um tipo de acidente
- falso_negativo: esperado um acidente, obtido SEM_ACIDENTE
- classificacao_errada: os dois sao acidentes, mas de tipos diferentes
- indeterminado: o sistema respondeu INDETERMINADO (pediu revisao humana) e o esperado era outro
Precisao e recall sao de DETECCAO (acidente x sem acidente): INDETERMINADO conta como detectado.
"""

import argparse
import csv
from datetime import datetime
from pathlib import Path

from detector_tracker import carregar_config
from eventos import TIPOS_IDX

CAMPOS_RESULTADO = ["video", "conjunto", "rotulo_esperado", "resultado_obtido", "confianca", "event_id",
                    "instante_esperado_s", "instante_detectado_s", "acerto", "tipo_erro"]


def analisar_video(caminho_video, config):
    """Roda o pipeline de deteccao em um video.

    Deve devolver um dict com: tipo (um de TIPOS_IDX), confianca (0 a 1), event_id (ou None)
    e instante_s (instante detectado, ou None). Recebe SOMENTE o caminho do video e a config.
    """
    # TODO: implementar quando a analise temporal e o classificador existirem
    # (detector_tracker -> trajetorias -> analise_temporal -> classificador -> eventos).
    raise NotImplementedError("pipeline de deteccao ainda nao implementado")


def tipo_de_erro(esperado, obtido):
    """Classifica o erro. Devolve '' quando acertou."""
    if esperado == obtido:
        return ""
    if obtido == "INDETERMINADO":
        return "indeterminado"
    if obtido == "SEM_ACIDENTE":
        return "falso_negativo"
    if esperado == "SEM_ACIDENTE":
        return "falso_positivo"
    return "classificacao_errada"


def ler_gabarito(caminho, conjunto=None):
    """Le o gabarito e devolve as linhas validas do conjunto pedido. Avisa sobre as ignoradas."""
    linhas = []
    with open(caminho, encoding="utf-8", newline="") as f:
        for linha in csv.DictReader(f):
            esperado = (linha.get("rotulo_esperado") or "").strip()
            if conjunto and (linha.get("conjunto") or "").strip().upper() != conjunto:
                continue
            if esperado not in TIPOS_IDX:
                print(f"[aviso] {linha['video']}: rotulo_esperado vazio ou invalido ({esperado!r}); ignorado")
                continue
            linhas.append(linha)
    return linhas


def resumir(resultados):
    """Conta acertos e erros e calcula precisao/recall de deteccao (None se nao der para calcular)."""
    erros = [r["tipo_erro"] for r in resultados]
    tp = sum(1 for r in resultados if r["rotulo_esperado"] != "SEM_ACIDENTE" and r["resultado_obtido"] != "SEM_ACIDENTE")
    fp = sum(1 for r in resultados if r["rotulo_esperado"] == "SEM_ACIDENTE" and r["resultado_obtido"] != "SEM_ACIDENTE")
    fn = sum(1 for r in resultados if r["rotulo_esperado"] != "SEM_ACIDENTE" and r["resultado_obtido"] == "SEM_ACIDENTE")
    return {
        "total": len(resultados),
        "acertos": sum(1 for r in resultados if r["acerto"] == "sim"),
        "falsos_positivos": erros.count("falso_positivo"),
        "falsos_negativos": erros.count("falso_negativo"),
        "classificacoes_erradas": erros.count("classificacao_errada"),
        "indeterminados": erros.count("indeterminado"),
        "precisao": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
    }


def avaliar(gabarito, conjunto, pasta_videos, config, analisar=analisar_video, pasta_saida=None, agora=None):
    """Avalia os videos do gabarito. Devolve (resultados, resumo, caminho_csv)."""
    resultados = []
    for linha in ler_gabarito(gabarito, conjunto):
        esperado = linha["rotulo_esperado"].strip()
        try:
            saida = analisar(Path(pasta_videos) / linha["video"], config)
        except NotImplementedError:
            raise
        except Exception as erro:  # um video com problema nao derruba a avaliacao inteira
            print(f"[aviso] {linha['video']}: falha ao analisar ({erro}); ignorado")
            continue
        obtido = saida["tipo"]
        erro_tipo = tipo_de_erro(esperado, obtido)
        resultados.append({
            "video": linha["video"],
            "conjunto": (linha.get("conjunto") or "").strip().upper(),
            "rotulo_esperado": esperado,
            "resultado_obtido": obtido,
            "confianca": "" if saida.get("confianca") is None else round(saida["confianca"], 2),
            "event_id": saida.get("event_id") or "",
            "instante_esperado_s": linha.get("instante_impacto_s") or "",
            "instante_detectado_s": "" if saida.get("instante_s") is None else saida["instante_s"],
            "acerto": "sim" if not erro_tipo else "nao",
            "tipo_erro": erro_tipo,
        })

    pasta = Path(pasta_saida or "saida/resultados")
    pasta.mkdir(parents=True, exist_ok=True)
    carimbo = (agora or datetime.now()).strftime("%Y%m%d_%H%M%S")
    caminho_csv = pasta / f"resultados_{conjunto or 'TODOS'}_{carimbo}.csv"
    with open(caminho_csv, "w", newline="", encoding="utf-8") as f:
        escritor = csv.DictWriter(f, fieldnames=CAMPOS_RESULTADO)
        escritor.writeheader()
        escritor.writerows(resultados)
    return resultados, resumir(resultados), caminho_csv


def imprimir_resumo(resumo, caminho_csv):
    """Mostra o resumo no terminal."""
    def pct(v):
        return "n/d" if v is None else f"{v:.0%}"
    print(f"\nVideos avaliados: {resumo['total']}")
    print(f"  acertos:                {resumo['acertos']}")
    print(f"  falsos positivos:       {resumo['falsos_positivos']}")
    print(f"  falsos negativos:       {resumo['falsos_negativos']}")
    print(f"  classificacoes erradas: {resumo['classificacoes_erradas']}")
    print(f"  indeterminados:         {resumo['indeterminados']}")
    print(f"  precisao (deteccao):    {pct(resumo['precisao'])}")
    print(f"  recall (deteccao):      {pct(resumo['recall'])}")
    print(f"Resultados em {caminho_csv}")


def main():
    ap = argparse.ArgumentParser(description="Avalia o pipeline de visao contra o gabarito")
    ap.add_argument("--conjunto", choices=["AJUSTE", "RESERVADO"], help="filtra por conjunto (padrao: todos)")
    ap.add_argument("--gabarito", default="../dados/gabarito.csv")
    ap.add_argument("--videos", default="../videos", help="pasta com os videos citados no gabarito")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    if args.conjunto == "RESERVADO":
        print("[atencao] conjunto RESERVADO: so para a avaliacao final; nao use para ajustar limiares ou pesos.")
    config = carregar_config(args.config)
    try:
        _, resumo, caminho_csv = avaliar(args.gabarito, args.conjunto, args.videos, config)
    except NotImplementedError as erro:
        raise SystemExit(f"[erro] {erro}. Implemente analisar_video() em avaliar.py (veja o TODO).")
    imprimir_resumo(resumo, caminho_csv)


if __name__ == "__main__":
    main()
