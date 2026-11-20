import csv
import io
import json
import logging
import os
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from . import config
from .extracao import problema_na_resposta

log = logging.getLogger(__name__)

# None = JSON de municípios do IBGE, os demais são CSV com estas colunas obrigatórias
REFERENCIAS = {
    "codigos.csv": {"tabela", "variavel", "codigo", "descricao"},
    "fatores_perfil.csv": {"prioridade", "grupo_fator", "fator", "variavel", "codigo_categoria",
                           "categoria", "ordem_categoria", "variavel_origem"},
    # Colunas da aba AUX_CINE_BRASIL do catálogo do INEP/SEDAP
    "cine_brasil.csv": {"CO_CINE_ROTULO", "NO_CINE_ROTULO", "CO_CINE_AREA_GERAL", "NO_CINE_AREA_GERAL",
                        "CO_CINE_AREA_ESPECIFICA", "NO_CINE_AREA_ESPECIFICA",
                        "CO_CINE_AREA_DETALHADA", "NO_CINE_AREA_DETALHADA"},
    "ibge_municipios.json": None,
}
OBRIGATORIAS = {"codigos.csv", "fatores_perfil.csv"}

TABELAS_GOLD = ("dim_tempo", "dim_instituicao", "dim_curso", "dim_area_conhecimento", "dim_localidade",
                "dim_oferta", "dim_perfil", "fato_evasao_curso", "fato_evasao_coorte",
                "fato_evasao_perfil", "fato_contexto_ies")

INSERIR_RESULTADO = """
INSERT INTO bronze.sedap_resultado (arquivo, consulta, ano, fato, variavel, prioridade, sql,
    executado_em, status, status_http, resposta, resposta_texto, erro)
VALUES (%(arquivo)s, %(consulta)s, %(ano)s, %(fato)s, %(variavel)s, %(prioridade)s, %(sql)s,
    %(executado_em)s, %(status)s, %(status_http)s, %(resposta)s, %(resposta_texto)s, %(erro)s)
"""
INSERIR_REFERENCIA = "INSERT INTO bronze.referencia (arquivo, linha, dados) VALUES (%s, %s, %s)"


def conectar(conninfo: str | None = None):
    if conninfo:
        return psycopg.connect(conninfo)
    return psycopg.connect(
        host=os.environ.get("POSTGRES_HOST") or "localhost",
        port=os.environ.get("POSTGRES_PORT") or "5432",
        dbname=config.obrigatoria("POSTGRES_DB"),
        user=config.obrigatoria("POSTGRES_USER"),
        password=config.obrigatoria("POSTGRES_PASSWORD"),
    )


def ler_resultados(pasta: Path):
    for arquivo in sorted(Path(pasta).glob("*/*.json")):
        doc = json.loads(arquivo.read_text(encoding="utf-8"))
        if doc["status"] == "ok":
            problema = problema_na_resposta(doc["resposta"], doc["colunas"])
            if problema:
                raise ValueError(f"{arquivo.name}: {problema}")
        yield {**doc, "arquivo": arquivo.relative_to(pasta).as_posix(),
               "resposta": Jsonb(doc["resposta"]) if doc["resposta"] is not None else None}


def ler_csv(caminho: Path, colunas: set) -> list:
    bruto = caminho.read_bytes()
    try:
        texto = bruto.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = bruto.decode("latin-1")
    cabecalho = texto.splitlines()[0] if texto else ""
    leitor = csv.DictReader(io.StringIO(texto), delimiter=";" if ";" in cabecalho else ",")
    faltam = colunas - set(leitor.fieldnames or ())
    if faltam:
        raise ValueError(f"{caminho.name}: faltam as colunas {sorted(faltam)}")
    return list(leitor)


def ler_referencias(pasta: Path):
    for nome, colunas in REFERENCIAS.items():
        caminho = Path(pasta) / nome
        if not caminho.exists():
            if nome in OBRIGATORIAS:
                raise FileNotFoundError(f"Referência obrigatória ausente: {caminho}")
            log.warning("Referência ausente: %s. Os nomes que dependem dela ficam vazios.", nome)
            continue
        if colunas is not None:
            yield nome, ler_csv(caminho, colunas)
            continue
        municipios = json.loads(caminho.read_text(encoding="utf-8"))
        if not isinstance(municipios, list) or not all(isinstance(m, dict) and "id" in m for m in municipios):
            raise ValueError(f"{nome}: esperada a lista de municípios da API de localidades do IBGE")
        yield nome, municipios


def _executar_sql(conexao, nome: str) -> None:
    conexao.execute((config.PASTA_SQL / nome).read_text(encoding="utf-8"))


def transformar(conninfo: str | None = None, pasta_bronze=None, pasta_referencias=None) -> dict:
    pasta_bronze = Path(pasta_bronze or config.PASTA_BRONZE)
    pasta_referencias = Path(pasta_referencias or config.PASTA_REFERENCIAS)
    # Tudo numa transação: commit ao sair do bloco e rollback se algo falhar
    with conectar(conninfo) as conexao:
        _executar_sql(conexao, "bronze.sql")
        with conexao.cursor() as cursor:
            cursor.executemany(INSERIR_RESULTADO, list(ler_resultados(pasta_bronze)))
            cursor.executemany(INSERIR_REFERENCIA, [
                (nome, numero, Jsonb(linha))
                for nome, linhas in ler_referencias(pasta_referencias)
                for numero, linha in enumerate(linhas, start=1)
            ])
        _executar_sql(conexao, "silver.sql")
        _executar_sql(conexao, "gold.sql")
        return {tabela: conexao.execute(f"SELECT count(*) FROM gold.{tabela}").fetchone()[0]
                for tabela in TABELAS_GOLD}
