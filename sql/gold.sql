-- Gold: reconstruída a cada execução a partir da silver.
-- O membro 0 ("Não informado") recebe o que não tem correspondência, para nada ser descartado.
-- As medidas ficam com o ruído como veio; as taxas são calculadas na visualização.

DROP SCHEMA IF EXISTS gold CASCADE;
CREATE SCHEMA gold;

CREATE TABLE gold.dim_tempo (
    id_tempo      integer PRIMARY KEY,
    ano_censo     integer UNIQUE,     -- também faz o papel de ano de ingresso na coorte
    ano_anterior  integer             -- usado na evasão anual pelo método Lobo
);
INSERT INTO gold.dim_tempo VALUES (0, NULL, NULL);
INSERT INTO gold.dim_tempo
SELECT row_number() OVER (ORDER BY ano), ano, ano - 1
FROM (
    SELECT ano FROM silver.evasao_curso
    UNION SELECT ano FROM silver.evasao_coorte
    UNION SELECT ano_ingresso FROM silver.evasao_coorte
    UNION SELECT ano FROM silver.evasao_perfil
    UNION SELECT ano FROM silver.contexto_ies
) AS anos
WHERE ano IS NOT NULL;

CREATE TABLE gold.dim_instituicao (
    id_instituicao            integer PRIMARY KEY,
    codigo_ies                text UNIQUE,
    nome_instituicao          text,
    sigla_instituicao         text,
    codigo_mantenedora        text,
    categoria_administrativa  text,
    organizacao_academica     text,
    municipio_sede            text,
    uf_sede                   text,
    regiao_sede               text,
    mesorregiao_sede          text,
    microrregiao_sede         text
);
INSERT INTO gold.dim_instituicao (id_instituicao, nome_instituicao) VALUES (0, 'Não informado');
-- Atributos do ano mais recente.
INSERT INTO gold.dim_instituicao
SELECT row_number() OVER (ORDER BY k.co_ies), k.co_ies, i.no_ies, i.sg_ies, i.co_mantenedora,
       i.categoria_administrativa, i.organizacao_academica, i.municipio_sede, i.uf_sede,
       i.regiao_sede, i.mesorregiao_sede, i.microrregiao_sede
FROM (
    SELECT co_ies FROM silver.evasao_curso
    UNION SELECT co_ies FROM silver.evasao_coorte
    UNION SELECT co_ies FROM silver.evasao_perfil
    UNION SELECT co_ies FROM silver.contexto_ies
) AS k
LEFT JOIN (SELECT DISTINCT ON (co_ies) * FROM silver.ies_ano ORDER BY co_ies, ano DESC) AS i
    ON i.co_ies = k.co_ies;

CREATE TABLE gold.dim_curso (
    id_curso                   integer PRIMARY KEY,
    codigo_curso               text UNIQUE,
    nome_curso                 text,
    grau_academico             text,
    nivel_academico            text,
    carga_horaria              numeric,
    situacao_curso             text,     -- situação de funcionamento no último ano observado
    prazo_integralizacao_anos  numeric,
    ano_inicio_funcionamento   integer
);
INSERT INTO gold.dim_curso (id_curso, nome_curso) VALUES (0, 'Não informado');
-- Atributos que mudam entre os anos ficam com o valor mais recente.
INSERT INTO gold.dim_curso
SELECT row_number() OVER (ORDER BY k.co_curso), k.co_curso, c.no_curso, c.grau_academico,
       c.nivel_academico, c.nu_carga_horaria, c.situacao_curso, c.prazo_integralizacao_anos,
       c.ano_inicio_funcionamento
FROM (
    SELECT co_curso FROM silver.evasao_curso
    UNION SELECT co_curso FROM silver.evasao_coorte
    UNION SELECT co_curso FROM silver.evasao_perfil
) AS k
LEFT JOIN (SELECT DISTINCT ON (co_curso) * FROM silver.curso_ano ORDER BY co_curso, ano DESC) AS c
    ON c.co_curso = k.co_curso;

CREATE TABLE gold.dim_area_conhecimento (
    id_area_conhecimento    integer PRIMARY KEY,
    codigo_rotulo_cine      text UNIQUE,
    nome_curso_cine         text,
    codigo_area_geral       text,
    area_geral              text,
    codigo_area_especifica  text,
    area_especifica         text,
    codigo_area_detalhada   text,
    area_detalhada          text
);
INSERT INTO gold.dim_area_conhecimento (id_area_conhecimento, nome_curso_cine) VALUES (0, 'Não informado');
INSERT INTO gold.dim_area_conhecimento
SELECT row_number() OVER (ORDER BY co_cine_rotulo), co_cine_rotulo, nome_curso_cine,
       codigo_area_geral, area_geral, codigo_area_especifica, area_especifica,
       codigo_area_detalhada, area_detalhada
FROM (
    SELECT DISTINCT ON (co_cine_rotulo) *
    FROM silver.curso_ano
    WHERE co_cine_rotulo IS NOT NULL
    ORDER BY co_cine_rotulo, ano DESC
) AS c;

-- Cursos EaD ou sem local apontam para o membro 0.
CREATE TABLE gold.dim_localidade (
    id_localidade     integer PRIMARY KEY,
    codigo_municipio  text UNIQUE,
    municipio         text,
    uf                text,
    nome_uf           text,
    regiao            text,
    eh_capital        boolean,
    mesorregiao       text,
    microrregiao      text
);
INSERT INTO gold.dim_localidade (id_localidade, municipio) VALUES (0, 'Não se aplica / não informado');
INSERT INTO gold.dim_localidade
SELECT row_number() OVER (ORDER BY co_municipio_curso), co_municipio_curso, municipio, uf, nome_uf,
       regiao, eh_capital, mesorregiao, microrregiao
FROM (
    SELECT DISTINCT ON (co_municipio_curso) *
    FROM silver.curso_ano
    WHERE co_municipio_curso IS NOT NULL
    ORDER BY co_municipio_curso, ano DESC
) AS c;

CREATE TABLE gold.dim_oferta (
    id_oferta                     integer PRIMARY KEY,
    modalidade_ensino             text,
    turno                         text,
    eh_gratuito                   boolean,
    possui_parte_ead              boolean,
    percentual_carga_horaria_ead  numeric,
    UNIQUE NULLS NOT DISTINCT (modalidade_ensino, turno, eh_gratuito, possui_parte_ead,
                               percentual_carga_horaria_ead)
);
INSERT INTO gold.dim_oferta (id_oferta, modalidade_ensino, turno) VALUES (0, 'Não informado', 'Não informado');
INSERT INTO gold.dim_oferta
SELECT row_number() OVER (ORDER BY modalidade_ensino, turno, eh_gratuito, possui_parte_ead,
                                   percentual_carga_horaria_ead),
       modalidade_ensino, turno, eh_gratuito, possui_parte_ead, percentual_carga_horaria_ead
FROM (
    SELECT DISTINCT modalidade_ensino, turno, eh_gratuito, possui_parte_ead, percentual_carga_horaria_ead
    FROM silver.curso_ano
) AS o
WHERE num_nonnulls(modalidade_ensino, turno, eh_gratuito, possui_parte_ead, percentual_carga_horaria_ead) > 0;

-- Código fora do catálogo vira uma categoria do próprio fator,
-- para as categorias continuarem somando o total.
CREATE TABLE gold.dim_perfil (
    id_perfil         integer PRIMARY KEY,
    grupo_fator       text,
    fator             text,
    categoria         text,
    codigo_categoria  text,              -- valor bruto no SEDAP+ (nulo = valor vazio)
    ordem_categoria   integer,
    variavel_origem   text,
    UNIQUE NULLS NOT DISTINCT (fator, codigo_categoria)
);
INSERT INTO gold.dim_perfil (id_perfil, grupo_fator, fator, categoria)
VALUES (0, 'Não informado', 'Não informado', 'Não informado');
INSERT INTO gold.dim_perfil
SELECT row_number() OVER (ORDER BY prioridade, grupo_fator, fator, ordem_categoria, codigo_categoria),
       grupo_fator, fator, categoria, codigo_categoria, ordem_categoria, variavel_origem
FROM (
    SELECT prioridade, grupo_fator, fator, categoria, codigo_categoria, ordem_categoria, variavel_origem
    FROM silver.fator_perfil
    UNION ALL
    SELECT DISTINCT f.prioridade, f.grupo_fator, f.fator,
           CASE WHEN p.codigo_categoria IS NULL THEN 'Vazio (sem significado no catálogo)'
                ELSE 'Código ' || p.codigo_categoria || ' não previsto no catálogo' END,
           p.codigo_categoria, 99, f.variavel_origem
    FROM silver.evasao_perfil AS p
    JOIN (SELECT DISTINCT prioridade, grupo_fator, fator, variavel, variavel_origem
          FROM silver.fator_perfil) AS f ON f.variavel = p.variavel
    WHERE NOT EXISTS (
        SELECT 1 FROM silver.fator_perfil AS s
        WHERE s.variavel = p.variavel AND s.codigo_categoria IS NOT DISTINCT FROM p.codigo_categoria)
) AS categorias;

-- As três fatos de alunos usam as mesmas chaves.
DROP TABLE IF EXISTS pg_temp.chave_curso;
CREATE TEMPORARY TABLE chave_curso AS
SELECT k.ano, k.co_ies, k.co_curso,
       coalesce(t.id_tempo, 0) AS id_tempo,
       coalesce(i.id_instituicao, 0) AS id_instituicao,
       coalesce(c.id_curso, 0) AS id_curso,
       coalesce(a.id_area_conhecimento, 0) AS id_area_conhecimento,
       coalesce(l.id_localidade, 0) AS id_localidade,
       coalesce(o.id_oferta, 0) AS id_oferta
FROM (
    SELECT ano, co_ies, co_curso FROM silver.evasao_curso
    UNION SELECT ano, co_ies, co_curso FROM silver.evasao_coorte
    UNION SELECT ano, co_ies, co_curso FROM silver.evasao_perfil
) AS k
LEFT JOIN gold.dim_tempo AS t ON t.ano_censo = k.ano
LEFT JOIN gold.dim_instituicao AS i ON i.codigo_ies = k.co_ies
LEFT JOIN gold.dim_curso AS c ON c.codigo_curso = k.co_curso
LEFT JOIN silver.curso_ano AS ca ON ca.ano = k.ano AND ca.co_curso = k.co_curso
LEFT JOIN gold.dim_area_conhecimento AS a ON a.codigo_rotulo_cine = ca.co_cine_rotulo
LEFT JOIN gold.dim_localidade AS l ON l.codigo_municipio = ca.co_municipio_curso
LEFT JOIN gold.dim_oferta AS o
    ON o.id_oferta <> 0
   AND (o.modalidade_ensino, o.turno, o.eh_gratuito, o.possui_parte_ead, o.percentual_carga_horaria_ead)
       IS NOT DISTINCT FROM
       (ca.modalidade_ensino, ca.turno, ca.eh_gratuito, ca.possui_parte_ead, ca.percentual_carga_horaria_ead);

CREATE TABLE gold.fato_evasao_curso (
    id_tempo                   integer NOT NULL REFERENCES gold.dim_tempo,
    id_instituicao             integer NOT NULL REFERENCES gold.dim_instituicao,
    id_curso                   integer NOT NULL REFERENCES gold.dim_curso,
    id_area_conhecimento       integer NOT NULL REFERENCES gold.dim_area_conhecimento,
    id_localidade              integer NOT NULL REFERENCES gold.dim_localidade,
    id_oferta                  integer NOT NULL REFERENCES gold.dim_oferta,
    codigo_ies                 text NOT NULL,
    codigo_curso               text NOT NULL,
    vinculos_total             numeric,
    alunos_cursando            numeric,
    alunos_matricula_trancada  numeric,
    alunos_desvinculados       numeric,
    alunos_transferidos        numeric,
    concluintes_total          numeric,
    alunos_falecidos           numeric,
    ingressantes_total         numeric,
    matriculas_total           numeric,
    vagas_totais               numeric,
    vagas_novas                numeric,
    inscritos_total            numeric,
    docentes_total             numeric,
    docentes_doutores          numeric,
    docentes_tempo_integral    numeric,
    flag_valor_negativo        boolean,
    flag_grupo_pequeno         boolean,
    PRIMARY KEY (id_tempo, id_curso)
);
INSERT INTO gold.fato_evasao_curso
SELECT k.id_tempo, k.id_instituicao, k.id_curso, k.id_area_conhecimento, k.id_localidade, k.id_oferta,
       f.co_ies, f.co_curso,
       f.vinculos_total, f.alunos_cursando, f.alunos_matricula_trancada, f.alunos_desvinculados,
       f.alunos_transferidos, f.concluintes_total, f.alunos_falecidos,
       f.ingressantes_total, f.matriculas_total, f.vagas_totais, f.vagas_novas, f.inscritos_total,
       f.docentes_total, f.docentes_doutores, f.docentes_tempo_integral,
       f.flag_valor_negativo, f.flag_grupo_pequeno
FROM silver.evasao_curso AS f
JOIN chave_curso AS k USING (ano, co_ies, co_curso);

CREATE TABLE gold.fato_evasao_coorte (
    id_tempo                   integer NOT NULL REFERENCES gold.dim_tempo,
    id_tempo_ingresso          integer NOT NULL REFERENCES gold.dim_tempo,
    id_instituicao             integer NOT NULL REFERENCES gold.dim_instituicao,
    id_curso                   integer NOT NULL REFERENCES gold.dim_curso,
    id_area_conhecimento       integer NOT NULL REFERENCES gold.dim_area_conhecimento,
    id_localidade              integer NOT NULL REFERENCES gold.dim_localidade,
    id_oferta                  integer NOT NULL REFERENCES gold.dim_oferta,
    codigo_ies                 text NOT NULL,
    codigo_curso               text NOT NULL,
    vinculos_total             numeric,
    alunos_cursando            numeric,
    alunos_matricula_trancada  numeric,
    alunos_desvinculados       numeric,
    alunos_transferidos        numeric,
    concluintes_total          numeric,
    alunos_falecidos           numeric,
    ingressantes_total         numeric,
    flag_valor_negativo        boolean,
    flag_grupo_pequeno         boolean,
    PRIMARY KEY (id_tempo, id_tempo_ingresso, id_curso)
);
INSERT INTO gold.fato_evasao_coorte
SELECT k.id_tempo, coalesce(ti.id_tempo, 0), k.id_instituicao, k.id_curso, k.id_area_conhecimento,
       k.id_localidade, k.id_oferta, f.co_ies, f.co_curso,
       f.vinculos_total, f.alunos_cursando, f.alunos_matricula_trancada, f.alunos_desvinculados,
       f.alunos_transferidos, f.concluintes_total, f.alunos_falecidos, f.ingressantes_total,
       f.flag_valor_negativo, f.flag_grupo_pequeno
FROM silver.evasao_coorte AS f
JOIN chave_curso AS k USING (ano, co_ies, co_curso)
LEFT JOIN gold.dim_tempo AS ti ON ti.ano_censo = f.ano_ingresso;

-- Cada análise deve filtrar um único fator.
CREATE TABLE gold.fato_evasao_perfil (
    id_tempo                   integer NOT NULL REFERENCES gold.dim_tempo,
    id_instituicao             integer NOT NULL REFERENCES gold.dim_instituicao,
    id_curso                   integer NOT NULL REFERENCES gold.dim_curso,
    id_area_conhecimento       integer NOT NULL REFERENCES gold.dim_area_conhecimento,
    id_localidade              integer NOT NULL REFERENCES gold.dim_localidade,
    id_oferta                  integer NOT NULL REFERENCES gold.dim_oferta,
    id_perfil                  integer NOT NULL REFERENCES gold.dim_perfil,
    codigo_ies                 text NOT NULL,
    codigo_curso               text NOT NULL,
    vinculos_total             numeric,
    alunos_cursando            numeric,
    alunos_matricula_trancada  numeric,
    alunos_desvinculados       numeric,
    alunos_transferidos        numeric,
    concluintes_total          numeric,
    alunos_falecidos           numeric,
    flag_valor_negativo        boolean,
    flag_grupo_pequeno         boolean,
    PRIMARY KEY (id_tempo, id_curso, id_perfil)
);
INSERT INTO gold.fato_evasao_perfil
SELECT k.id_tempo, k.id_instituicao, k.id_curso, k.id_area_conhecimento, k.id_localidade, k.id_oferta,
       coalesce(p.id_perfil, 0), f.co_ies, f.co_curso,
       f.vinculos_total, f.alunos_cursando, f.alunos_matricula_trancada, f.alunos_desvinculados,
       f.alunos_transferidos, f.concluintes_total, f.alunos_falecidos,
       f.flag_valor_negativo, f.flag_grupo_pequeno
FROM silver.evasao_perfil AS f
JOIN chave_curso AS k USING (ano, co_ies, co_curso)
LEFT JOIN gold.dim_perfil AS p
    ON p.id_perfil <> 0 AND p.fator = f.fator AND p.codigo_categoria IS NOT DISTINCT FROM f.codigo_categoria;

CREATE TABLE gold.fato_contexto_ies (
    id_tempo                 integer NOT NULL REFERENCES gold.dim_tempo,
    id_instituicao           integer NOT NULL REFERENCES gold.dim_instituicao,
    codigo_ies               text NOT NULL,
    referencia_financeira    text,       -- dado financeiro da IES ou da mantenedora
    tecnicos_total           numeric,
    receita_propria          numeric,
    receita_transferencia    numeric,
    receita_outra            numeric,
    despesa_pessoal          numeric,
    despesa_custeio          numeric,
    despesa_investimento     numeric,
    despesa_pesquisa         numeric,
    despesa_outra            numeric,
    docentes_total           numeric,
    docentes_doutores        numeric,
    docentes_tempo_integral  numeric,
    flag_valor_negativo      boolean,
    PRIMARY KEY (id_tempo, id_instituicao)
);
INSERT INTO gold.fato_contexto_ies
SELECT coalesce(t.id_tempo, 0), coalesce(i.id_instituicao, 0), f.co_ies, f.referencia_financeira,
       f.tecnicos_total, f.receita_propria, f.receita_transferencia, f.receita_outra,
       f.despesa_pessoal, f.despesa_custeio, f.despesa_investimento, f.despesa_pesquisa, f.despesa_outra,
       f.docentes_total, f.docentes_doutores, f.docentes_tempo_integral, f.flag_valor_negativo
FROM silver.contexto_ies AS f
LEFT JOIN gold.dim_tempo AS t ON t.ano_censo = f.ano
LEFT JOIN gold.dim_instituicao AS i ON i.codigo_ies = f.co_ies;

-- Cada linha da silver tem que virar exatamente uma linha na fato.
DO $$
BEGIN
    IF (SELECT count(*) FROM silver.evasao_curso) <> (SELECT count(*) FROM gold.fato_evasao_curso)
       OR (SELECT count(*) FROM silver.evasao_coorte) <> (SELECT count(*) FROM gold.fato_evasao_coorte)
       OR (SELECT count(*) FROM silver.evasao_perfil) <> (SELECT count(*) FROM gold.fato_evasao_perfil)
       OR (SELECT count(*) FROM silver.contexto_ies) <> (SELECT count(*) FROM gold.fato_contexto_ies) THEN
        RAISE EXCEPTION 'A gold não tem o mesmo número de linhas da silver: registros perdidos ou duplicados.';
    END IF;
END $$;
