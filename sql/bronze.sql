-- Cópia dos arquivos da bronze, recarregada a cada execução. Os arquivos nunca são alterados.

CREATE SCHEMA IF NOT EXISTS bronze;

CREATE TABLE IF NOT EXISTS bronze.sedap_resultado (
    arquivo         text PRIMARY KEY,   -- <ano>/<consulta>__<data e hora>.json
    consulta        text NOT NULL,
    ano             integer NOT NULL,
    fato            text NOT NULL,
    variavel        text,               -- fator de perfil, quando houver
    prioridade      integer NOT NULL,
    sql             text NOT NULL,
    executado_em    timestamptz NOT NULL,
    status          text NOT NULL,      -- ok | recusada | resposta_invalida
    status_http     integer,
    resposta        jsonb,              -- corpo da resposta ({"rows": [...]})
    resposta_texto  text,               -- corpo, quando não veio em JSON
    erro            text
);

CREATE TABLE IF NOT EXISTS bronze.referencia (
    arquivo  text NOT NULL,
    linha    integer NOT NULL,
    dados    jsonb NOT NULL,            -- CSV: {coluna: texto}; IBGE: o objeto do município
    PRIMARY KEY (arquivo, linha)
);

TRUNCATE bronze.sedap_resultado, bronze.referencia;
