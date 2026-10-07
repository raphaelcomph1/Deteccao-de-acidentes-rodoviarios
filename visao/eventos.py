"""Monta o evento no formato do contrato MQTT v0.2 (docs/contrato_mqtt.md)."""

import secrets
from datetime import datetime

TIPOS_IDX = {
    "SEM_ACIDENTE": 0,
    "COLISAO_TRASEIRA": 1,
    "COLISAO_LATERAL": 2,
    "ATROPELAMENTO": 3,
    "INDETERMINADO": 9,
}
COLISOES = ("COLISAO_TRASEIRA", "COLISAO_LATERAL")


def gerar_event_id(camera_id, instante=None):
    """Gera evt-<camera>-<AAAAMMDDTHHMMSS>-<4 hex aleatorios>."""
    instante = instante or datetime.now()
    return f"evt-{camera_id}-{instante:%Y%m%dT%H%M%S}-{secrets.token_hex(2)}"


def nivel_confianca(confianca, limiar_alto=0.70, limiar_medio=0.40):
    """Converte a confianca (0 a 1) em ALTO, MEDIO ou BAIXO."""
    if confianca >= limiar_alto:
        return "ALTO"
    if confianca >= limiar_medio:
        return "MEDIO"
    return "BAIXO"


def decidir_tipo(tipo_detectado, confianca, limiar_alto=0.70, limiar_medio=0.40):
    """Aplica os limiares: ALTO mantem o tipo, MEDIO vira INDETERMINADO, BAIXO vira SEM_ACIDENTE."""
    nivel = nivel_confianca(confianca, limiar_alto, limiar_medio)
    if nivel == "ALTO":
        return tipo_detectado, nivel
    if nivel == "MEDIO":
        return "INDETERMINADO", nivel
    return "SEM_ACIDENTE", nivel


def recomendar(tipo, veiculo_parado_na_pista):
    """Aplica a matriz R1-R4 (prioridade R4 > R3 > R2 > R1). Devolve (recurso, regra)."""
    if tipo == "INDETERMINADO":
        return "REVISAO_OPERADOR", "R4"
    if tipo == "ATROPELAMENTO":
        return "RESGATE_PRIORITARIO", "R3"
    if tipo in COLISOES and veiculo_parado_na_pista is True:
        return "AMBULANCIA_SIMULADA", "R2"
    return "AVALIACAO_EQUIPE", "R1"


def montar_evento(*, event_id, camera_id, local, tipo, confianca, nivel, indicadores,
                  video, frame, tempo_s, arquivo, revisao=1, timestamp=None):
    """Monta o dicionario do evento. Nao deve ser chamado para SEM_ACIDENTE (nao publica)."""
    if tipo == "SEM_ACIDENTE":
        raise ValueError("SEM_ACIDENTE nao gera evento")
    recurso, regra = recomendar(tipo, indicadores.get("veiculo_parado_na_pista"))
    timestamp = timestamp or datetime.now().astimezone().isoformat(timespec="seconds")
    return {
        "v": 1,
        "event_id": event_id,
        "revisao": revisao,
        "timestamp": timestamp,
        "camera_id": camera_id,
        "local": local,
        "tipo": tipo,
        "tipo_idx": TIPOS_IDX[tipo],
        "confianca": round(confianca, 2),
        "nivel_confianca": nivel,
        "indicadores": indicadores,
        "recomendacao": {"recurso": recurso, "regra": regra},
        "estado": "SUSPEITA",
        "evidencia": {"video": video, "frame": frame, "tempo_s": tempo_s, "arquivo": arquivo},
    }


def validar_evento(evento):
    """Confere campos e valores do contrato. Devolve lista de problemas (vazia se estiver ok)."""
    campos = ["v", "event_id", "revisao", "timestamp", "camera_id", "local", "tipo", "tipo_idx",
              "confianca", "nivel_confianca", "indicadores", "recomendacao", "estado", "evidencia"]
    problemas = [f"falta o campo {c}" for c in campos if c not in evento]
    if problemas:
        return problemas
    if evento["v"] != 1:
        problemas.append("v deve ser 1")
    if TIPOS_IDX.get(evento["tipo"]) != evento["tipo_idx"] or evento["tipo"] == "SEM_ACIDENTE":
        problemas.append("tipo/tipo_idx invalidos para publicacao")
    if not 0 <= evento["confianca"] <= 1:
        problemas.append("confianca fora de 0 a 1")
    if evento["nivel_confianca"] not in ("ALTO", "MEDIO"):
        problemas.append("nivel_confianca deve ser ALTO ou MEDIO")
    if evento["estado"] != "SUSPEITA":
        problemas.append("a visao sempre publica estado SUSPEITA")
    if evento["indicadores"].get("faixa_obstruida") not in ("sim", "nao", "desconhecido"):
        problemas.append("faixa_obstruida invalida")
    return problemas
