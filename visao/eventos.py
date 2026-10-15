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


class GerenciadorEventos:
    """Mantem um unico event_id por acidente e decide quando reenviar o evento (revisao + 1).

    Mesmo acidente = mesmo par de tracks, ou pares que compartilham um track e tem contato a menos de
    eventos.janela_dedup_s um do outro (o tracking pode trocar o ID do outro veiculo no impacto).
    Um evento so e reenviado se a confianca subir pelo menos eventos.delta_revisao, se o tipo mudar para
    outro de confianca igual ou maior, ou se o veiculo passar a ficar parado na pista.
    """

    def __init__(self, config, camera_id, local, video):
        cfg = config["eventos"]
        self.delta = cfg["delta_revisao"]
        self.janela_s = cfg["janela_dedup_s"]
        self.pasta_evidencias = config["saida"]["evidencias"]
        self.camera_id, self.local, self.video = camera_id, local, video
        self.eventos = []   # um dict de estado por acidente

    def _achar(self, cand, fps):
        """Procura o acidente ja registrado ao qual este candidato pertence."""
        par = {cand["track_a"], cand["track_b"]}
        for ev in self.eventos:
            perto = abs(cand["frame_contato"] - ev["frame_contato"]) <= self.janela_s * fps
            if par == ev["par"] or (par & ev["tracks"] and perto):
                return ev
        return None

    @staticmethod
    def indicadores(cand):
        """Indicadores observados no par. O que nao da para saber fica null ou 'desconhecido'."""
        pessoa = cand["tipo_par"] == "pessoa_veiculo"
        parado = bool(cand["parado_final_a"] or (not pessoa and cand["parado_final_b"]))
        return {
            "veiculos_envolvidos": 1 if pessoa else 2,   # so os do par; outros podem existir e nao sao contados
            "pessoa_envolvida": pessoa,
            "veiculo_parado_na_pista": parado,
            "faixa_obstruida": "desconhecido",           # nao ha informacao de faixas
        }

    def atualizar(self, cand, resultado, fps):
        """Recebe o candidato e seu resultado do classificador. Devolve o evento a publicar, ou None."""
        if resultado["tipo"] == "SEM_ACIDENTE":
            return None
        indicadores = self.indicadores(cand)
        ev = self._achar(cand, fps)
        if ev is None:
            event_id = gerar_event_id(self.camera_id)
            ev = {"event_id": event_id, "revisao": 0, "par": {cand["track_a"], cand["track_b"]},
                  "tracks": set(), "frame_contato": cand["frame_contato"], "tempo_contato_s": cand["tempo_contato_s"],
                  "confianca": -1.0, "tipo": None, "parado": False,   # o que foi publicado por ultimo
                  "conf_max": -1.0, "tipo_max": None,                  # o melhor resultado visto (para a avaliacao)
                  "timestamp": datetime.now().astimezone().isoformat(timespec="seconds")}
            self.eventos.append(ev)
        ev["tracks"] |= {cand["track_a"], cand["track_b"]}
        if resultado["confianca"] > ev["conf_max"]:
            ev["conf_max"], ev["tipo_max"] = resultado["confianca"], resultado["tipo"]

        mudou = (
            ev["revisao"] == 0
            or resultado["confianca"] >= ev["confianca"] + self.delta
            or (resultado["tipo"] != ev["tipo"] and resultado["confianca"] >= ev["confianca"])
            or (indicadores["veiculo_parado_na_pista"] and not ev["parado"])
        )
        if not mudou:
            return None
        ev["revisao"] += 1
        ev["confianca"], ev["tipo"] = resultado["confianca"], resultado["tipo"]
        ev["parado"] = ev["parado"] or indicadores["veiculo_parado_na_pista"]
        ev["frame_contato"] = min(ev["frame_contato"], cand["frame_contato"])
        return montar_evento(
            event_id=ev["event_id"], revisao=ev["revisao"], timestamp=ev["timestamp"],
            camera_id=self.camera_id, local=self.local,
            tipo=resultado["tipo"], confianca=resultado["confianca"], nivel=resultado["nivel"],
            indicadores={**indicadores, "veiculo_parado_na_pista": ev["parado"]},
            video=self.video, frame=cand["frame_contato"], tempo_s=cand["tempo_contato_s"],
            arquivo=f"visao/{self.pasta_evidencias}/{ev['event_id']}.jpg",
        )
