import json
import logging
import os
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import requests

from . import config

log = logging.getLogger(__name__)

TENTATIVAS = 3
STATUS_REPETIR = {429, 503}
STATUS_PARAR = {401, 403, 404, 405}  # credencial ou endereço errado: não adianta continuar
LIMITE_ERRO = 2000
URL_IBGE = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios"

class ErroExtracao(Exception):
    pass


# A mensagem cita só nomes de coluna, nunca valores, ela vai para a bronze e para os erros da carga
def problema_na_resposta(resposta, colunas) -> str | None:
    if not isinstance(resposta, dict) or not isinstance(resposta.get("rows"), list):
        return "resposta sem a lista 'rows'"
    esperadas = {coluna.lower() for coluna in colunas}
    for linha in resposta["rows"]:
        if not isinstance(linha, dict):
            return "linha do resultado que não é um objeto JSON"
        recebidas = {coluna.lower() for coluna in linha}
        if recebidas != esperadas:
            return (f"colunas diferentes do esperado (faltam {sorted(esperadas - recebidas)}, "
                    f"sobram {sorted(recebidas - esperadas)})")
    return None


def _espera(tentativa: int, retry_after: str | None = None) -> float:
    if retry_after and retry_after.isdigit():
        return min(float(retry_after), 300.0)
    return 5.0 * 2 ** (tentativa - 1)


# Só tenta de novo quando a consulta não chegou ao provedor ou quando ele pede (429/503).
def enviar(sql: str, sessao, url: str, token: str, perfil: str):
    cabecalho = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "profile-id": perfil}
    for tentativa in range(1, TENTATIVAS + 1):
        try:
            resposta = sessao.post(url, json={"content": sql}, headers=cabecalho,
                                   timeout=(30, config.TIMEOUT_SEGUNDOS))
        except requests.exceptions.ConnectTimeout:
            if tentativa == TENTATIVAS:
                raise ErroExtracao("sem conexão com o SEDAP+") from None
            time.sleep(_espera(tentativa))
            continue
        except requests.exceptions.RequestException as erro:
            # A consulta pode ter sido processada, então não repete sozinho.
            raise ErroExtracao(f"falha na comunicação com o SEDAP+ ({type(erro).__name__})") from None
        if resposta.status_code in STATUS_REPETIR and tentativa < TENTATIVAS:
            time.sleep(_espera(tentativa, resposta.headers.get("Retry-After")))
            continue
        return resposta


def registro(consulta, resposta, executado_em: datetime) -> dict:
    doc = {
        "consulta": consulta.nome,
        "ano": consulta.ano,
        "fato": consulta.fato,
        "variavel": consulta.variavel,
        "prioridade": consulta.prioridade,
        "colunas": list(consulta.colunas),
        "sql": consulta.sql,
        "executado_em": executado_em.isoformat(timespec="seconds"),
        "status_http": resposta.status_code,
        "status": None,
        "resposta": None,
        "resposta_texto": None,
        "erro": None,
    }
    if not 200 <= resposta.status_code < 300:  # o SEDAP+ responde 201 quando a consulta dá certo
        doc.update(status="recusada", erro=resposta.text[:LIMITE_ERRO])
        return doc
    try:
        corpo = resposta.json()
    except ValueError:
        doc.update(status="resposta_invalida", resposta_texto=resposta.text, erro="resposta não é JSON")
        return doc
    problema = problema_na_resposta(corpo, consulta.colunas)
    doc.update(status="resposta_invalida" if problema else "ok", resposta=corpo, erro=problema)
    return doc


def gravar(doc: dict, pasta: Path) -> Path:
    destino_pasta = Path(pasta) / str(doc["ano"])
    destino_pasta.mkdir(parents=True, exist_ok=True)
    momento = datetime.fromisoformat(doc["executado_em"]).strftime("%Y%m%dT%H%M%S")
    destino = destino_pasta / f"{doc['consulta']}__{momento}.json"
    if destino.exists():
        raise ErroExtracao(f"arquivo já existe na bronze: {destino.name}")
    temporario = destino.with_suffix(".tmp")
    temporario.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporario, destino)
    return destino


def ja_enviadas(pasta: Path) -> set:
    enviadas = set()
    for arquivo in Path(pasta).glob("*/*.json"):
        doc = json.loads(arquivo.read_text(encoding="utf-8"))
        enviadas.add((doc["consulta"], doc["ano"], doc["sql"]))
    return enviadas


def extrair(consultas, refazer: bool = False, sessao=None, pasta=None) -> Counter:
    url = config.obrigatoria("SEDAP_API_URL")
    token = config.obrigatoria("SEDAP_TOKEN")
    perfil = config.obrigatoria("SEDAP_PROFILE_ID")
    pasta = Path(pasta or config.PASTA_BRONZE)
    enviadas = set() if refazer else ja_enviadas(pasta)
    sessao = sessao or requests.Session()
    total = Counter()
    for consulta in consultas:
        if (consulta.nome, consulta.ano, consulta.sql) in enviadas:
            total["já na bronze"] += 1
            continue
        if total["enviadas"]:
            time.sleep(config.PAUSA_SEGUNDOS)
        inicio = time.monotonic()
        executado_em = datetime.now().astimezone()
        resposta = enviar(consulta.sql, sessao, url, token, perfil)
        if resposta.status_code in STATUS_PARAR | STATUS_REPETIR:
            raise ErroExtracao(f"{consulta.nome} {consulta.ano}: HTTP {resposta.status_code}; extração interrompida")
        doc = registro(consulta, resposta, executado_em)
        gravar(doc, pasta)
        total["enviadas"] += 1
        total[doc["status"]] += 1
        linhas = len(doc["resposta"]["rows"]) if doc["status"] == "ok" else "-"
        log.info("%s %s: %s (HTTP %s, %s linhas, %.0f s)", consulta.nome, consulta.ano, doc["status"],
                 resposta.status_code, linhas, time.monotonic() - inicio)
    return total


def baixar_ibge(destino=None, sessao=None) -> Path:
    destino = Path(destino or config.PASTA_REFERENCIAS / "ibge_municipios.json")
    if destino.exists():
        raise ErroExtracao(f"{destino.name} já existe; apague-o para baixar de novo")
    resposta = (sessao or requests).get(URL_IBGE, timeout=(30, 120))
    resposta.raise_for_status()
    destino.write_bytes(resposta.content)
    return destino
