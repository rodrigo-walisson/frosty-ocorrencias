from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from supabase import create_client
from pydantic import BaseModel
from typing import List, Optional
import pyodbc
import os

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_KEY")
)

def get_sql_connection():
    conn = pyodbc.connect(
        f"DRIVER={{ODBC Driver 17 for SQL Server}};"
        f"SERVER={os.getenv('SQL_SERVER')};"
        f"DATABASE={os.getenv('SQL_DATABASE')};"
        f"UID={os.getenv('SQL_USERNAME')};"
        f"PWD={os.getenv('SQL_PASSWORD')};"
    )
    return conn

@app.get("/")
def health_check():
    return {"status": "API Frosty rodando!"}

@app.get("/roteiro/{codigo}")
def buscar_roteiro(codigo: str):
    try:
        conn = get_sql_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT
                T0."Code"           AS roteiro,
                T0."Name"           AS nome_roteiro,
                T6."U_NomeFant"     AS filial,
                T6."BPLId"          AS id_filial,
                T1."U_CardCode"     AS cod_cliente,
                T1."U_NomeFantasia" AS nome_cliente
            FROM SBO_FROSTY_RAW.raws.[@FLX_ROTEIRO] T0
            LEFT JOIN SBO_FROSTY_RAW.raws.[@FLX_ROTEIROPEDIDO] T1
                   ON T0."Code" = T1."Code"
            LEFT JOIN (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY "BPLId" ORDER BY (SELECT 1)) AS RN
                FROM SBO_FROSTY_RAW.raws.[OBPL]
            ) T6
                   ON TRY_CAST(REPLACE(TRIM(T0."U_BPLId"), '''', '') AS INTEGER) = T6."BPLId"
                  AND T6.RN = 1
            WHERE T0."Code" = ?
              AND T0."Canceled" = 'N'
              AND T1."U_CardCode" IS NOT NULL
              AND T1."U_NomeFantasia" IS NOT NULL
              AND T1."U_DocEntry" IS NOT NULL
              AND REPLACE(TRIM(T0."U_BPLId"), '''', '') <> ''
              AND T0."U_BPLId" NOT LIKE '%.%'
              AND T0."U_BPLId" NOT LIKE '%,%'
            ORDER BY T1."U_NomeFantasia"
        """, codigo)
        rows = cursor.fetchall()
        if not rows:
            raise HTTPException(status_code=404, detail="Roteiro não encontrado")
        clientes = []
        for row in rows:
            clientes.append({
                "roteiro": row[0],
                "nome_roteiro": row[1],
                "filial": row[2],
                "id_filial": row[3],
                "cod_cliente": row[4],
                "nome_cliente": row[5]
            })
        return {"roteiro": clientes[0]["roteiro"], "nome_roteiro": clientes[0]["nome_roteiro"], "filial": clientes[0]["filial"], "clientes": clientes}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/pedidos/{codigo_roteiro}/{cod_cliente}")
def buscar_pedidos(codigo_roteiro: str, cod_cliente: str):
    try:
        conn = get_sql_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                T1."U_DocEntry" AS nro_pedido,
                T1."U_status"   AS status
            FROM SBO_FROSTY_RAW.raws.[@FLX_ROTEIRO] T0
            LEFT JOIN SBO_FROSTY_RAW.raws.[@FLX_ROTEIROPEDIDO] T1
                   ON T0."Code" = T1."Code"
            WHERE T0."Code"       = ?
              AND T1."U_CardCode" = ?
              AND T0."Canceled"   = 'N'
              AND T1."U_DocEntry" IS NOT NULL
            ORDER BY T1."U_DocEntry"
        """, codigo_roteiro, cod_cliente)
        rows = cursor.fetchall()
        pedidos = [{"nro_pedido": row[0], "status": row[1]} for row in rows]
        return {"pedidos": pedidos}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/produtos/{nro_pedido}")
def buscar_produtos(nro_pedido: str):
    try:
        conn = get_sql_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                R1."DocEntry"                               AS nro_pedido,
                R1."ItemCode"                               AS cod_produto,
                R1."Dscription"                             AS produto,
                R1."UomCode"                                AS unidade,
                CAST(R1."Quantity" AS INT)                  AS qtd_caixas,
                CAST(R1."NumPerMsr" AS INT)                 AS un_por_caixa,
                CAST(R1."Quantity" * R1."NumPerMsr" AS INT) AS qtd_unidades
            FROM SBO_FROSTY_RAW.raws.[RDR1] R1
            INNER JOIN SBO_FROSTY_RAW.raws.[ORDR] T2
                    ON R1."DocEntry" = T2."DocEntry"
            WHERE R1."DocEntry" = ?
              AND T2."Canceled" = 'N'
            ORDER BY R1."Dscription"
        """, nro_pedido)
        rows = cursor.fetchall()
        produtos = [{
            "nro_pedido": row[0],
            "cod_produto": row[1],
            "produto": row[2],
            "unidade": row[3],
            "qtd_caixas": row[4],
            "un_por_caixa": row[5],
            "qtd_unidades": row[6]
        } for row in rows]
        return {"produtos": produtos}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class ItemOcorrencia(BaseModel):
    cod_produto: str
    produto: str
    unidade: Optional[str] = None
    qtd_pedida_cx: Optional[int] = None
    un_por_caixa: Optional[int] = None
    qtd_pedida_un: Optional[int] = None
    qtd_ocorrencia: int

class OcorrenciaRequest(BaseModel):
    filial: str
    id_filial: int
    nome_motorista: str
    roteiro: str
    nome_roteiro: str
    cod_cliente: str
    nome_cliente: str
    nro_pedido: int
    tipo: str
    observacao: Optional[str] = None
    itens: List[ItemOcorrencia]

@app.post("/ocorrencia")
def registrar_ocorrencia(ocorrencia: OcorrenciaRequest):
    try:
        resultado = supabase.table("ocorrencias").insert({
            "filial": ocorrencia.filial,
            "id_filial": ocorrencia.id_filial,
            "nome_motorista": ocorrencia.nome_motorista,
            "roteiro": ocorrencia.roteiro,
            "nome_roteiro": ocorrencia.nome_roteiro,
            "cod_cliente": ocorrencia.cod_cliente,
            "nome_cliente": ocorrencia.nome_cliente,
            "nro_pedido": ocorrencia.nro_pedido,
            "tipo": ocorrencia.tipo,
            "observacao": ocorrencia.observacao,
        }).execute()

        ocorrencia_id = resultado.data[0]["id"]

        itens = [{
            "ocorrencia_id": ocorrencia_id,
            "cod_produto": item.cod_produto,
            "produto": item.produto,
            "unidade": item.unidade,
            "qtd_pedida_cx": item.qtd_pedida_cx,
            "un_por_caixa": item.un_por_caixa,
            "qtd_pedida_un": item.qtd_pedida_un,
            "qtd_ocorrencia": item.qtd_ocorrencia,
        } for item in ocorrencia.itens]

        supabase.table("itens_ocorrencia").insert(itens).execute()

        return {"status": "sucesso", "ocorrencia_id": ocorrencia_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))