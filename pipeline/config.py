import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PASTA_BRONZE = RAIZ / "dados" / "bronze" / "sedap"
PASTA_CONSULTAS = RAIZ / "dados" / "consultas"
PASTA_REFERENCIAS = RAIZ / "referencias"
PASTA_SQL = RAIZ / "sql"

ANOS = (2019, 2020, 2021, 2022, 2023)


def carregar_env(caminho: Path = RAIZ / ".env") -> None:
    if not caminho.exists():
        return
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = (parte.strip() for parte in linha.split("=", 1))
        if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "\"'":
            valor = valor[1:-1]
        os.environ.setdefault(chave, valor)


carregar_env()

# Padrão dos exemplos do provedor; falta confirmar com ele o nome real das tabelas.
TABELA_SEDAP = os.environ.get("SEDAP_TABELA") or "raw.{tabela}_{ano}_SEDAP"
TIMEOUT_SEGUNDOS = float(os.environ.get("SEDAP_TIMEOUT_SEGUNDOS") or 300)
PAUSA_SEGUNDOS = float(os.environ.get("SEDAP_PAUSA_SEGUNDOS") or 2)


def obrigatoria(nome: str) -> str:
    valor = os.environ.get(nome, "").strip()
    if not valor:
        raise SystemExit(f"Defina {nome} no arquivo .env.")
    return valor
