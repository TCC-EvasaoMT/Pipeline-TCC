-- Silver reconstruída a cada execução a partir da bronze. Ela padroniza os códigos, junta os anos e as referências e marca ruído e grupos pequenos, sem corrigir nada.
-- Um curso repetido na mesma consulta faz a carga falhar.

DROP SCHEMA IF EXISTS silver CASCADE;
CREATE SCHEMA silver;

-- Se algum ano vem como texto ('51') e outro com número (51 ou 51.0): tudo vira '51'
CREATE FUNCTION silver.codigo(valor text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE WHEN valor ~ '^[0-9]+\.0+$' THEN split_part(valor, '.', 1) ELSE valor END
$$;

-- A API pode devolver os nomes de coluna em outra caixa
CREATE FUNCTION silver.minusculas(linha jsonb) RETURNS jsonb
LANGUAGE sql IMMUTABLE AS $$
    SELECT coalesce(jsonb_object_agg(lower(chave), valor), '{}'::jsonb)
    FROM jsonb_each(linha) AS e(chave, valor)
$$;

-- Negativo vem do ruído do SEDAP+: só marca, o valor fica como veio.
CREATE FUNCTION silver.tem_negativo(VARIADIC valores numeric[]) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
    SELECT coalesce(bool_or(valor < 0), false) FROM unnest(valores) AS valor
$$;

-- Base de risco (vínculos menos falecidos) abaixo de 30
CREATE FUNCTION silver.grupo_pequeno(vinculos numeric, falecidos numeric) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
    SELECT vinculos - falecidos < 30
$$;

-- Nos CSV de referência, célula vazia vira nulo
CREATE TABLE silver.codigo_catalogo AS
SELECT dados->>'tabela' AS tabela,
       dados->>'variavel' AS variavel,
       nullif(dados->>'codigo', '') AS codigo,
       dados->>'descricao' AS descricao
FROM bronze.referencia
WHERE arquivo = 'codigos.csv';
ALTER TABLE silver.codigo_catalogo ADD UNIQUE NULLS NOT DISTINCT (tabela, variavel, codigo);

-- Código fora do catálogo aparece como veio, sem ser descartado
CREATE FUNCTION silver.decodificar(p_tabela text, p_variavel text, p_codigo text) RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT coalesce(
        (SELECT descricao FROM silver.codigo_catalogo
          WHERE tabela = p_tabela AND variavel = p_variavel AND codigo IS NOT DISTINCT FROM p_codigo),
        'Código ' || p_codigo || ' não previsto no catálogo')
$$;

CREATE TABLE silver.fator_perfil AS
SELECT (dados->>'prioridade')::int AS prioridade,
       dados->>'grupo_fator' AS grupo_fator,
       dados->>'fator' AS fator,
       dados->>'variavel' AS variavel,
       nullif(dados->>'codigo_categoria', '') AS codigo_categoria,
       dados->>'categoria' AS categoria,
       (dados->>'ordem_categoria')::int AS ordem_categoria,
       dados->>'variavel_origem' AS variavel_origem
FROM bronze.referencia
WHERE arquivo = 'fatores_perfil.csv';
ALTER TABLE silver.fator_perfil ADD UNIQUE NULLS NOT DISTINCT (variavel, codigo_categoria);

-- Municípios criados depois de 2017 não têm meso nem microrregião no IBGE, para eles, a UF e a região vêm da região imediata
CREATE TABLE silver.municipio AS
SELECT dados->>'id' AS codigo_municipio,
       dados->>'nome' AS municipio,
       coalesce(dados #>> '{microrregiao,mesorregiao,UF,sigla}',
                dados #>> '{regiao-imediata,regiao-intermediaria,UF,sigla}') AS uf,
       coalesce(dados #>> '{microrregiao,mesorregiao,UF,nome}',
                dados #>> '{regiao-imediata,regiao-intermediaria,UF,nome}') AS nome_uf,
       coalesce(dados #>> '{microrregiao,mesorregiao,UF,regiao,nome}',
                dados #>> '{regiao-imediata,regiao-intermediaria,UF,regiao,nome}') AS regiao,
       dados #>> '{microrregiao,mesorregiao,nome}' AS mesorregiao,
       dados #>> '{microrregiao,nome}' AS microrregiao
FROM bronze.referencia
WHERE arquivo = 'ibge_municipios.json';
ALTER TABLE silver.municipio ADD PRIMARY KEY (codigo_municipio);

CREATE TABLE silver.cine AS
SELECT dados->>'CO_CINE_ROTULO' AS codigo_rotulo_cine,
       dados->>'NO_CINE_ROTULO' AS nome_curso_cine,
       dados->>'CO_CINE_AREA_GERAL' AS codigo_area_geral,
       dados->>'NO_CINE_AREA_GERAL' AS area_geral,
       dados->>'CO_CINE_AREA_ESPECIFICA' AS codigo_area_especifica,
       dados->>'NO_CINE_AREA_ESPECIFICA' AS area_especifica,
       dados->>'CO_CINE_AREA_DETALHADA' AS codigo_area_detalhada,
       dados->>'NO_CINE_AREA_DETALHADA' AS area_detalhada
FROM bronze.referencia
WHERE arquivo = 'cine_brasil.csv';
ALTER TABLE silver.cine ADD PRIMARY KEY (codigo_rotulo_cine);

-- Só a resposta ok mais recente de cada consulta e ano. As recusadas ficam só na bronze.
CREATE TABLE silver.linha_sedap AS
SELECT r.consulta, r.ano, r.fato, r.variavel, silver.minusculas(elemento.valor) AS linha
FROM (
    SELECT DISTINCT ON (consulta, ano) consulta, ano, fato, variavel, resposta
    FROM bronze.sedap_resultado
    WHERE status = 'ok'
    ORDER BY consulta, ano, executado_em DESC
) AS r
CROSS JOIN LATERAL jsonb_array_elements(r.resposta->'rows') AS elemento(valor);

CREATE TABLE silver.curso_ano AS
WITH bruto AS (
    SELECT DISTINCT ON (ano, silver.codigo(linha->>'co_curso'))
        ano,
        silver.codigo(linha->>'co_curso') AS co_curso,
        -- Instituição do curso (ver silver.instituicao).
        silver.codigo(linha->>'co_mantenedora') AS co_mantenedora,
        silver.codigo(linha->>'tp_categoria_administrativa') AS tp_categoria_administrativa,
        silver.codigo(linha->>'tp_organizacao_academica') AS tp_organizacao_academica,
        silver.codigo(linha->>'co_municipio_ies') AS co_municipio_ies,
        linha->>'no_curso' AS no_curso,
        silver.codigo(linha->>'tp_situacao_curso') AS tp_situacao_curso,
        linha->>'co_cine_rotulo' AS co_cine_rotulo,
        silver.codigo(linha->>'tp_grau_academico') AS tp_grau_academico,
        silver.codigo(linha->>'tp_nivel_academico') AS tp_nivel_academico,
        silver.codigo(linha->>'tp_modalidade_ensino') AS tp_modalidade_ensino,
        (linha->>'in_gratuito')::numeric AS in_gratuito,
        (linha->>'nu_carga_horaria')::numeric AS nu_carga_horaria,
        linha->>'dt_inicio_funcionamento' AS dt_inicio_funcionamento,
        silver.codigo(linha->>'co_municipio_curso') AS co_municipio_curso,
        (linha->>'in_capital_curso')::numeric AS in_capital_curso,
        (linha->>'in_integral')::numeric AS in_integral,
        (linha->>'in_matutino')::numeric AS in_matutino,
        (linha->>'in_vespertino')::numeric AS in_vespertino,
        (linha->>'in_noturno')::numeric AS in_noturno,
        (linha->>'nu_integralizacao_integral')::numeric AS nu_integralizacao_integral,
        (linha->>'nu_integralizacao_matutino')::numeric AS nu_integralizacao_matutino,
        (linha->>'nu_integralizacao_vespertino')::numeric AS nu_integralizacao_vespertino,
        (linha->>'nu_integralizacao_noturno')::numeric AS nu_integralizacao_noturno,
        (linha->>'nu_integralizacao_ead')::numeric AS nu_integralizacao_ead,
        (linha->>'in_oferece_disc_semi_pres')::numeric AS in_oferece_disc_semi_pres,
        (linha->>'nu_perc_carga_semi_pres')::numeric AS nu_perc_carga_semi_pres
    FROM silver.linha_sedap
    WHERE consulta IN ('alunos_curso', 'alunos_coorte') OR fato = 'evasao_perfil'
    ORDER BY ano, silver.codigo(linha->>'co_curso'), consulta
)
SELECT
    b.*,
    silver.decodificar('SUP_CURSO', 'TP_SITUACAO', b.tp_situacao_curso) AS situacao_curso,
    silver.decodificar('SUP_CURSO', 'TP_GRAU_ACADEMICO', b.tp_grau_academico) AS grau_academico,
    silver.decodificar('SUP_CURSO', 'TP_NIVEL_ACADEMICO', b.tp_nivel_academico) AS nivel_academico,
    silver.decodificar('SUP_CURSO', 'TP_MODALIDADE_ENSINO', b.tp_modalidade_ensino) AS modalidade_ensino,
    -- Um turno, "Mais de um turno" ou "Não se aplica (EaD)".
    CASE
        WHEN coalesce(b.in_integral, 0) + coalesce(b.in_matutino, 0)
           + coalesce(b.in_vespertino, 0) + coalesce(b.in_noturno, 0) > 1 THEN 'Mais de um turno'
        WHEN b.in_integral = 1 THEN 'Integral'
        WHEN b.in_matutino = 1 THEN 'Matutino'
        WHEN b.in_vespertino = 1 THEN 'Vespertino'
        WHEN b.in_noturno = 1 THEN 'Noturno'
        WHEN b.tp_modalidade_ensino = '2' THEN 'Não se aplica (EaD)'
    END AS turno,
    CASE b.in_gratuito WHEN 1 THEN true WHEN 0 THEN false END AS eh_gratuito,
    CASE b.in_oferece_disc_semi_pres WHEN 1 THEN true WHEN 0 THEN false END AS possui_parte_ead,
    b.nu_perc_carga_semi_pres AS percentual_carga_horaria_ead,
    CASE b.in_capital_curso WHEN 1 THEN true WHEN 0 THEN false END AS eh_capital,
    -- Se houver mais de um prazo, fica o menor.
    least(b.nu_integralizacao_integral, b.nu_integralizacao_matutino, b.nu_integralizacao_vespertino,
          b.nu_integralizacao_noturno, b.nu_integralizacao_ead) AS prazo_integralizacao_anos,
    substring(b.dt_inicio_funcionamento FROM '[0-9]{4}')::int AS ano_inicio_funcionamento,
    m.municipio, m.uf, m.nome_uf, m.regiao, m.mesorregiao, m.microrregiao,
    k.nome_curso_cine, k.codigo_area_geral, k.area_geral, k.codigo_area_especifica,
    k.area_especifica, k.codigo_area_detalhada, k.area_detalhada
FROM bruto AS b
LEFT JOIN silver.municipio AS m ON m.codigo_municipio = b.co_municipio_curso
LEFT JOIN silver.cine AS k ON k.codigo_rotulo_cine = b.co_cine_rotulo;
ALTER TABLE silver.curso_ano ADD PRIMARY KEY (ano, co_curso);

-- O SEDAP+ não deixa identificar a IES. Filtramos por IES públicas como sede em MT e 4 atributos delas
CREATE TABLE silver.instituicao AS
WITH bruto AS (
    SELECT DISTINCT
        silver.codigo(linha->>'co_mantenedora') AS co_mantenedora,
        silver.codigo(linha->>'tp_categoria_administrativa') AS tp_categoria_administrativa,
        silver.codigo(linha->>'tp_organizacao_academica') AS tp_organizacao_academica,
        silver.codigo(linha->>'co_municipio_ies') AS co_municipio_ies
    FROM silver.linha_sedap
    WHERE consulta IN ('alunos_curso', 'alunos_coorte', 'docentes_ies') OR fato = 'evasao_perfil'
)
SELECT
    b.*,
    silver.decodificar('SUP_IES', 'TP_CATEGORIA_ADMINISTRATIVA', b.tp_categoria_administrativa)
        AS categoria_administrativa,
    silver.decodificar('SUP_IES', 'TP_ORGANIZACAO_ACADEMICA', b.tp_organizacao_academica)
        AS organizacao_academica,
    m.municipio AS municipio_sede, m.uf AS uf_sede, m.regiao AS regiao_sede,
    m.mesorregiao AS mesorregiao_sede, m.microrregiao AS microrregiao_sede
FROM bruto AS b
LEFT JOIN silver.municipio AS m ON m.codigo_municipio = b.co_municipio_ies;
ALTER TABLE silver.instituicao
    ADD UNIQUE NULLS NOT DISTINCT (co_mantenedora, tp_categoria_administrativa, tp_organizacao_academica,
                                   co_municipio_ies);

-- Entra todo curso que aparece em qualquer uma das duas consultas (alunos e docentes).
CREATE TABLE silver.evasao_curso AS
WITH alunos AS (
    SELECT ano,
           silver.codigo(linha->>'co_curso') AS co_curso,
           (linha->>'vinculos_total')::numeric AS vinculos_total,
           (linha->>'alunos_cursando')::numeric AS alunos_cursando,
           (linha->>'alunos_matricula_trancada')::numeric AS alunos_matricula_trancada,
           (linha->>'alunos_desvinculados')::numeric AS alunos_desvinculados,
           (linha->>'alunos_transferidos')::numeric AS alunos_transferidos,
           (linha->>'concluintes_total')::numeric AS concluintes_total,
           (linha->>'alunos_falecidos')::numeric AS alunos_falecidos,
           (linha->>'ingressantes_total')::numeric AS ingressantes_total,
           (linha->>'matriculas_total')::numeric AS matriculas_total
    FROM silver.linha_sedap
    WHERE consulta = 'alunos_curso'
),
docentes AS (
    SELECT ano,
           silver.codigo(linha->>'co_curso') AS co_curso,
           (linha->>'docentes_total')::numeric AS docentes_total,
           (linha->>'docentes_doutores')::numeric AS docentes_doutores,
           (linha->>'docentes_tempo_integral')::numeric AS docentes_tempo_integral
    FROM silver.linha_sedap
    WHERE consulta = 'docentes_curso'
),
chaves AS (
    SELECT ano, co_curso FROM alunos
    UNION SELECT ano, co_curso FROM docentes
)
SELECT k.ano, k.co_curso,
       a.vinculos_total, a.alunos_cursando, a.alunos_matricula_trancada, a.alunos_desvinculados,
       a.alunos_transferidos, a.concluintes_total, a.alunos_falecidos,
       a.ingressantes_total, a.matriculas_total,
       d.docentes_total, d.docentes_doutores, d.docentes_tempo_integral,
       silver.tem_negativo(a.vinculos_total, a.alunos_cursando, a.alunos_matricula_trancada,
                           a.alunos_desvinculados, a.alunos_transferidos, a.concluintes_total,
                           a.alunos_falecidos, a.ingressantes_total, a.matriculas_total,
                           d.docentes_total, d.docentes_doutores, d.docentes_tempo_integral)
           AS flag_valor_negativo,
       silver.grupo_pequeno(a.vinculos_total, a.alunos_falecidos) AS flag_grupo_pequeno
FROM chaves AS k
LEFT JOIN alunos AS a USING (ano, co_curso)
LEFT JOIN docentes AS d USING (ano, co_curso);
ALTER TABLE silver.evasao_curso ADD PRIMARY KEY (ano, co_curso);

CREATE TABLE silver.evasao_coorte AS
SELECT c.*,
       silver.tem_negativo(c.vinculos_total, c.alunos_cursando, c.alunos_matricula_trancada,
                           c.alunos_desvinculados, c.alunos_transferidos, c.concluintes_total,
                           c.alunos_falecidos, c.ingressantes_total) AS flag_valor_negativo,
       silver.grupo_pequeno(c.vinculos_total, c.alunos_falecidos) AS flag_grupo_pequeno
FROM (
    SELECT ano,
           silver.codigo(linha->>'co_curso') AS co_curso,
           silver.codigo(linha->>'nu_ano_ingresso')::int AS ano_ingresso,
           (linha->>'vinculos_total')::numeric AS vinculos_total,
           (linha->>'alunos_cursando')::numeric AS alunos_cursando,
           (linha->>'alunos_matricula_trancada')::numeric AS alunos_matricula_trancada,
           (linha->>'alunos_desvinculados')::numeric AS alunos_desvinculados,
           (linha->>'alunos_transferidos')::numeric AS alunos_transferidos,
           (linha->>'concluintes_total')::numeric AS concluintes_total,
           (linha->>'alunos_falecidos')::numeric AS alunos_falecidos,
           (linha->>'ingressantes_total')::numeric AS ingressantes_total
    FROM silver.linha_sedap
    WHERE consulta = 'alunos_coorte'
) AS c;
ALTER TABLE silver.evasao_coorte
    ALTER COLUMN co_curso SET NOT NULL,
    ADD UNIQUE NULLS NOT DISTINCT (ano, co_curso, ano_ingresso);

-- A categoria nula é mantida.
CREATE TABLE silver.evasao_perfil AS
SELECT p.*,
       silver.tem_negativo(p.vinculos_total, p.alunos_cursando, p.alunos_matricula_trancada,
                           p.alunos_desvinculados, p.alunos_transferidos, p.concluintes_total,
                           p.alunos_falecidos) AS flag_valor_negativo,
       silver.grupo_pequeno(p.vinculos_total, p.alunos_falecidos) AS flag_grupo_pequeno
FROM (
    SELECT s.ano,
           silver.codigo(s.linha->>'co_curso') AS co_curso,
           s.variavel,
           f.fator,
           silver.codigo(s.linha->>'categoria_perfil') AS codigo_categoria,
           (s.linha->>'vinculos_total')::numeric AS vinculos_total,
           (s.linha->>'alunos_cursando')::numeric AS alunos_cursando,
           (s.linha->>'alunos_matricula_trancada')::numeric AS alunos_matricula_trancada,
           (s.linha->>'alunos_desvinculados')::numeric AS alunos_desvinculados,
           (s.linha->>'alunos_transferidos')::numeric AS alunos_transferidos,
           (s.linha->>'concluintes_total')::numeric AS concluintes_total,
           (s.linha->>'alunos_falecidos')::numeric AS alunos_falecidos
    FROM silver.linha_sedap AS s
    LEFT JOIN (SELECT DISTINCT variavel, fator FROM silver.fator_perfil) AS f ON f.variavel = s.variavel
    WHERE s.fato = 'evasao_perfil'
) AS p;
ALTER TABLE silver.evasao_perfil
    ALTER COLUMN co_curso SET NOT NULL,
    ALTER COLUMN variavel SET NOT NULL,
    ADD UNIQUE NULLS NOT DISTINCT (ano, co_curso, variavel, codigo_categoria);

-- Docentes da instituição (ver silver.instituicao), uma linha por ano
CREATE TABLE silver.contexto_ies AS
SELECT d.*,
       silver.tem_negativo(d.docentes_total, d.docentes_doutores, d.docentes_tempo_integral)
           AS flag_valor_negativo
FROM (
    SELECT ano,
           silver.codigo(linha->>'co_mantenedora') AS co_mantenedora,
           silver.codigo(linha->>'tp_categoria_administrativa') AS tp_categoria_administrativa,
           silver.codigo(linha->>'tp_organizacao_academica') AS tp_organizacao_academica,
           silver.codigo(linha->>'co_municipio_ies') AS co_municipio_ies,
           (linha->>'docentes_total')::numeric AS docentes_total,
           (linha->>'docentes_doutores')::numeric AS docentes_doutores,
           (linha->>'docentes_tempo_integral')::numeric AS docentes_tempo_integral
    FROM silver.linha_sedap
    WHERE consulta = 'docentes_ies'
) AS d;
ALTER TABLE silver.contexto_ies
    ADD UNIQUE NULLS NOT DISTINCT (ano, co_mantenedora, tp_categoria_administrativa, tp_organizacao_academica,
                                   co_municipio_ies);
