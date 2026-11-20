import csv
from dataclasses import dataclass

from . import config

CONTAGEM = "COUNT(*)"

# O SEDAP+ não soma colunas NUMERIC, só INT64.
def soma(coluna: str) -> str:
    return f"SUM(CAST({coluna} AS INT64))"


# IES pública (1 federal, 2 estadual, 3 municipal) com sede em MT (51)
FILTRO_ESCOPO = ("i.CO_UF = '51'", "i.TP_CATEGORIA_ADMINISTRATIVA IN ('1', '2', '3')")

# Códigos de TP_SITUACAO
SITUACOES = (
    ("alunos_cursando", 2),
    ("alunos_matricula_trancada", 3),
    ("alunos_desvinculados", 4),
    ("alunos_transferidos", 5),
    ("concluintes_total", 6),
    ("alunos_falecidos", 7),
)

TURNOS = ("INTEGRAL", "MATUTINO", "VESPERTINO", "NOTURNO", "EAD")

# O SEDAP+ não deixa identificar a IES no resultado então o CO_IES só entra no JOIN, nunca no SELECT ou no GROUP BY. 
# Colunas com o mesmo nome em SUP_IES e SUP_CURSO ganham sufixo para não se repetirem no resultado.
ATRIBUTOS_IES = (
    ("i.CO_MANTENEDORA", "CO_MANTENEDORA"),
    ("i.TP_CATEGORIA_ADMINISTRATIVA", "TP_CATEGORIA_ADMINISTRATIVA"),
    ("i.TP_ORGANIZACAO_ACADEMICA", "TP_ORGANIZACAO_ACADEMICA"),
    ("i.CO_REGIAO", "CO_REGIAO_IES"),
    ("i.CO_UF", "CO_UF_IES"),
    ("i.CO_MUNICIPIO", "CO_MUNICIPIO_IES"),
)
ATRIBUTOS_CURSO = (
    ("c.NO_CURSO", "NO_CURSO"),
    ("c.TP_SITUACAO", "TP_SITUACAO_CURSO"),
    ("c.CO_CINE_ROTULO", "CO_CINE_ROTULO"),
    ("c.TP_GRAU_ACADEMICO", "TP_GRAU_ACADEMICO"),
    ("c.TP_NIVEL_ACADEMICO", "TP_NIVEL_ACADEMICO"),
    ("c.TP_MODALIDADE_ENSINO", "TP_MODALIDADE_ENSINO"),
    ("c.IN_GRATUITO", "IN_GRATUITO"),
    ("c.NU_CARGA_HORARIA", "NU_CARGA_HORARIA"),
    ("c.DT_INICIO_FUNCIONAMENTO", "DT_INICIO_FUNCIONAMENTO"),
    ("c.CO_MUNICIPIO", "CO_MUNICIPIO_CURSO"),
    ("c.IN_CAPITAL", "IN_CAPITAL_CURSO"),
    ("c.IN_INTEGRAL", "IN_INTEGRAL"),
    ("c.IN_MATUTINO", "IN_MATUTINO"),
    ("c.IN_VESPERTINO", "IN_VESPERTINO"),
    ("c.IN_NOTURNO", "IN_NOTURNO"),
    *((f"c.NU_INTEGRALIZACAO_{turno}", f"NU_INTEGRALIZACAO_{turno}") for turno in TURNOS),
    ("c.IN_OFERECE_DISC_SEMI_PRES", "IN_OFERECE_DISC_SEMI_PRES"),
    ("c.NU_PERC_CARGA_SEMI_PRES", "NU_PERC_CARGA_SEMI_PRES"),
)

INGRESSANTES = (soma("a.IN_INGRESSO_TOTAL"), "ingressantes_total")
MATRICULAS = (soma("a.IN_MATRICULA"), "matriculas_total")

# Conta todos os docentes, em exercício ou afastados
# Doutor = escolaridade 5, tempo integral = regime 1 ou 2 (com ou sem dedicação exclusiva)
MEDIDAS_DOCENTES = (
    (CONTAGEM, "docentes_total"),
    ("SUM(CASE WHEN d.TP_ESCOLARIDADE = '5' THEN 1 ELSE 0 END)", "docentes_doutores"),
    ("SUM(CASE WHEN d.TP_REGIME_TRABALHO IN ('1', '2') THEN 1 ELSE 0 END)", "docentes_tempo_integral"),
)


@dataclass(frozen=True)
class Consulta:
    nome: str
    ano: int
    fato: str
    prioridade: int  # só define a ordem de extração
    sql: str
    colunas: tuple
    variavel: str | None = None


def tabela(nome: str, ano: int) -> str:
    return "`" + config.TABELA_SEDAP.format(tabela=nome, ano=ano) + "`"


def _valor(codigo, texto: bool) -> str:
    return f"'{codigo}'" if texto else str(codigo)


def _montar(grupo, medidas, origem, filtros):
    itens = (*grupo, *medidas)
    sql = (
        "SELECT\n  " + ",\n  ".join(f"{expressao} AS {nome}" for expressao, nome in itens)
        + "\n" + origem
        + "\nWHERE " + "\n  AND ".join(filtros)
        + "\nGROUP BY\n  " + ",\n  ".join(expressao for expressao, _ in grupo)
    )
    return sql, tuple(nome for _, nome in itens)


def _alunos(ano, extra=(), medidas_extra=()):
    texto = ano == 2019
    chave_ies = "a.CO_IES" if texto else "CAST(a.CO_IES AS STRING)"
    chave_curso = "a.CO_CURSO" if texto else "CAST(a.CO_CURSO AS STRING)"
    grupo = (("a.NU_ANO", "NU_ANO"), ("a.CO_CURSO", "CO_CURSO"), *ATRIBUTOS_IES, *ATRIBUTOS_CURSO, *extra)
    medidas = (
        (CONTAGEM, "vinculos_total"),
        *((f"SUM(CASE WHEN a.TP_SITUACAO = {_valor(codigo, texto)} THEN 1 ELSE 0 END)", nome)
          for nome, codigo in SITUACOES),
        *medidas_extra,
    )
    origem = (
        f"FROM {tabela('SUP_ALUNO', ano)} AS a\n"
        f"JOIN {tabela('SUP_IES', ano)} AS i ON {chave_ies} = i.CO_IES\n"
        f"LEFT JOIN {tabela('SUP_CURSO', ano)} AS c ON {chave_curso} = c.CO_CURSO"
    )
    filtros = (*FILTRO_ESCOPO, f"a.TP_NIVEL_ACADEMICO = {_valor(1, texto)}")  # 1 = graduação
    return _montar(grupo, medidas, origem, filtros)


def _docentes_curso(ano):
    grupo = (("dc.NU_ANO", "NU_ANO"), ("dc.CO_CURSO", "CO_CURSO"))
    # A SUP_DOCENTE_CURSO não tem o nível do curso: a SUP_CURSO entra só para ficar com a graduação
    origem = (
        f"FROM {tabela('SUP_DOCENTE_CURSO', ano)} AS dc\n"
        f"JOIN {tabela('SUP_DOCENTE', ano)} AS d ON dc.CO_DOCENTE_IES = d.CO_DOCENTE_IES\n"
        f"JOIN {tabela('SUP_IES', ano)} AS i ON dc.CO_IES = i.CO_IES\n"
        f"JOIN {tabela('SUP_CURSO', ano)} AS c ON dc.CO_CURSO = c.CO_CURSO"
    )
    filtros = (*FILTRO_ESCOPO, "c.TP_NIVEL_ACADEMICO = '1'")  # 1 = graduação
    return _montar(grupo, MEDIDAS_DOCENTES, origem, filtros)


# Uma linha por instituição (combinação dos atributos da IES): as IES iguais nesses atributos são somadas.
def _docentes_ies(ano):
    grupo = (("d.NU_ANO", "NU_ANO"), *ATRIBUTOS_IES)
    origem = (
        f"FROM {tabela('SUP_DOCENTE', ano)} AS d\n"
        f"JOIN {tabela('SUP_IES', ano)} AS i ON d.CO_IES = i.CO_IES"
    )
    return _montar(grupo, MEDIDAS_DOCENTES, origem, FILTRO_ESCOPO)


# Três fatores são calculados na própria consulta e o SEDAP+ aceita esses cálculos no GROUP BY.
# Os CASE não tem ELSE de propósito: sem dado, a categoria fica nula.
def expressao_fator(variavel: str, ano: int) -> str:
    if variavel == "FAIXA_ETARIA":
        # Em 2019 usa NU_IDADE; depois, o ano do Censo menos o ano de DT_NASCIMENTO (texto dd/mm/aaaa)
        idade = "a.NU_IDADE" if ano == 2019 else "(a.NU_ANO - CAST(SUBSTR(a.DT_NASCIMENTO, 7, 4) AS INT64))"
        return (f"CASE WHEN {idade} <= 19 THEN 'ATE_19' WHEN {idade} <= 24 THEN '20_24' "
                f"WHEN {idade} <= 29 THEN '25_29' WHEN {idade} <= 39 THEN '30_39' "
                f"WHEN {idade} >= 40 THEN '40_MAIS' END")
    if variavel == "NATURALIDADE":
        mt = _valor(51, ano == 2019)
        return (f"CASE WHEN a.CO_UF_NASCIMENTO = {mt} THEN 'MT' "
                "WHEN a.CO_UF_NASCIMENTO IS NOT NULL THEN 'OUTRA_UF' END")
    if variavel == "CARGA_HORARIA_CUMPRIDA":
        # Compara por multiplicação para não dividir por zero.
        integ, total = "a.QT_CARGA_HORARIA_INTEG", "a.QT_CARGA_HORARIA_TOTAL"
        return (f"CASE WHEN {total} > 0 AND {integ} * 4 < {total} THEN '0_25' "
                f"WHEN {total} > 0 AND {integ} * 2 < {total} THEN '25_50' "
                f"WHEN {total} > 0 AND {integ} * 4 < {total} * 3 THEN '50_75' "
                f"WHEN {total} > 0 AND {integ} * 4 >= {total} * 3 THEN '75_MAIS' END")
    return f"a.{variavel}"


def fatores() -> list:
    vistos = {}
    with open(config.PASTA_REFERENCIAS / "fatores_perfil.csv", encoding="utf-8", newline="") as arquivo:
        for linha in csv.DictReader(arquivo, delimiter=";"):
            vistos.setdefault(linha["variavel"], int(linha["prioridade"]))
    return list(vistos.items())


def catalogo(anos=config.ANOS) -> list:
    lista = []
    perfil = fatores()
    for ano in anos:
        fixas = (
            ("alunos_curso", "evasao_curso", _alunos(ano, medidas_extra=(INGRESSANTES, MATRICULAS))),
            ("docentes_curso", "evasao_curso", _docentes_curso(ano)),
            ("alunos_coorte", "evasao_coorte",
             _alunos(ano, extra=(("a.NU_ANO_INGRESSO", "NU_ANO_INGRESSO"),), medidas_extra=(INGRESSANTES,))),
            ("docentes_ies", "contexto_ies", _docentes_ies(ano)),
        )
        for nome, fato, (sql, colunas) in fixas:
            lista.append(Consulta(nome, ano, fato, 1, sql, colunas))
        for variavel, prioridade in perfil:
            sql, colunas = _alunos(ano, extra=((expressao_fator(variavel, ano), "CATEGORIA_PERFIL"),))
            lista.append(Consulta(f"perfil_{variavel.lower()}", ano, "evasao_perfil", prioridade,
                                  sql, colunas, variavel))
    return sorted(lista, key=lambda consulta: (consulta.prioridade, consulta.ano))
