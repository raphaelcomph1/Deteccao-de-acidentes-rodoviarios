"""Funcoes de apoio compartilhadas pelos scripts (sem dependencias pesadas como o ultralytics)."""

import yaml


def carregar_config(caminho="config.yaml"):
    """Le o arquivo de configuracao YAML."""
    with open(caminho, encoding="utf-8") as f:
        return yaml.safe_load(f)
