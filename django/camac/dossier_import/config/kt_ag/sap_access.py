import json
import os
import timeit
from contextlib import contextmanager
from datetime import datetime

import hdbcli
from dotenv import load_dotenv
from hdbcli import dbapi
from hdbcli.dbapi import Decimal

# Load environment variables from .env file
load_dotenv()

# SAP HANA connection parameters
host = os.getenv("HANA_HOST")
port = int(os.getenv("HANA_PORT"))
user = os.getenv("HANA_USER")
password = os.getenv("HANA_PASSWORD")
dbname = os.getenv("HANA_DBNAME")
schema = os.getenv("HANA_SCHEMA")

def encode(obj):
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral() else float(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj

@contextmanager
def managed_cursor(cursor):
    try:
        yield cursor
    finally:
        cursor.close()

def runQuery(query: str, subqueries=None, filter=None, batch_size=100, limit=None):
    if subqueries is None:
        subqueries = {}

    with managed_cursor(connection.cursor()) as cursor, managed_cursor(connection.cursor()) as sub_cursor:

        cursor.execute(f"SET SCHEMA {schema}")

        if filter:
            query = f"{query} WHERE {filter}"

        result = execute(cursor, query, batch_size=batch_size, limit=limit)
        for r in result:
            for k, v in subqueries.items():
                r[k] = [e for e in execute(sub_cursor, v, (r['GESUCH_ID']), is_subquery=True)]
            # print(f"Added subquery results to {r['GESUCH_ID']}")
            yield r

def execute(cursor: hdbcli.dbapi.Cursor, query, params=None, batch_size=100, limit=None, is_subquery=False):
    if limit:
        query = f"{query} LIMIT {limit}"
    cursor.execute(query, params)

    while True:
        # Fetch the results
        rows = cursor.fetchmany(batch_size)
        if not rows:
            print()
            break
        if not is_subquery:
            print (".", end="")
        yield from (dict(zip(r.column_names, r.column_values)) for r in rows)


def write_json_file(data: str, relative_path: str, filename):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    # Extract the first and second digits after "EBPA-"
    first_digit, second_digit = filename.split("-")[1][:2]

    target_dir = os.path.join(base_dir, f"{relative_path}/{first_digit}/{second_digit}")

    # Zielverzeichnis erstellen, falls es nicht existiert
    os.makedirs(target_dir, exist_ok=True)

    # Datei im Zielverzeichnis speichern
    file_path = os.path.join(target_dir, filename)
    with open(file_path, "w", encoding="utf-8") as file:
        file.write(data)


def query_gesuche(filter, batch_size, limit):
    global result
    query = f"""select ZEBP_GESUCH.*, ZEBP_ENTSCHEID.*
                from ZEBP_GESUCH
                    left join ZEBP_ENTSCHEID on ZEBP_GESUCH.GESUCH_ID = ZEBP_ENTSCHEID.EXTERN_ID
            """
    stort_query = f"""select ZEBP_STORT.*, ZEZS_CITY.*, ZEBP_PARZ.*
                        from ZEBP_STORT
                                 left join ZEBP_PARZ on (ZEBP_STORT.city_id = ZEBP_PARZ.city_id and ZEBP_STORT.GESUCH_ID = ZEBP_PARZ.GESUCH_ID)
                                 left join ZEZS_CITY on ZEBP_STORT.CITY_ID = ZEZS_CITY.CITY_ID
                        where ZEBP_STORT.GESUCH_ID = ?"""
    subqueries = {"STORT": stort_query}
    return runQuery(query, subqueries, batch_size=batch_size, filter=filter, limit=limit)


def write_gesuche_to_json():
    global result
    result = query_gesuche(filter=None, batch_size=500, limit=None)
    i = 0
    for r in result:
        i+=1
        # print(f"Writing json file for {r['GESUCH_ID']}")
        json_str = json.dumps(r, indent=4, default=encode)
        write_json_file(json_str, "database/json/", f"{r['GESUCH_ID']}.json")
    print(f"Read {i} Gesuche from database")

# Establish a connection
try:
    connection: hdbcli.dbapi.Connection = dbapi.connect(
        address=host,
        port=port,
        user=user,
        password=password,
        databaseName=dbname
    )
    print("Connected to SAP HANA successfully.")

    # IMPORTANT:
    # 1) Only one schmea SAPABAP1 is needed
    # 2) There are three prefixes for SAPABAP1 tables, which are relevant for eBau: ZEBP (eBau Aargau), ZEB2 (eBau Extended), ZEZS (Zentrale Services)
    # 3) ZEB2 DB model: https://confluence.ag.ch/pages/viewpage.action?pageId=7734763 (not updated since 2019!)
    #
    # select all requests from ZEBP_GESUCH
    # runQuery("SELECT * FROM SAPABAP1.ZEBP_GESUCH LIMIT 10" );
    #
    # list all eBau Aargau tables ZEBP_xxx (26 tables)
    # runQuery("select table_name from tables where schema_name='SAPABAP1' and table_name  like 'ZEBP_%'" )
    #
    # list all eBau Extended tables ZEB2_xxx (145 tables - multiple tables not in use!)
    # runQuery("select table_name from tables where schema_name='SAPABAP1' and table_name  like 'ZEB2_%'" )
    #
    # following ZEZS_xxx tables contain eBau Aargau relevant information
    # ZEZS_ADM_AUTH/AUTHT: eBau Aargau permission types
    # ZEZS_ADM_DWFTYP/DWFTYPT: "Document Workflow" types. Stellungnahme, Ergänzungsanfrage, Einsichtsfreigabe etc.
    # ZEZS_ADM_PTRMTYP/TRMTYP/TRMTYPT: Deadline type (Auflagefrist, Sistierung, Standard-Frist)
    # ZEZS_ADM_SIGNAT: Municipality signature
    # ZEZS_ADM_USRAUTH: Permission assigment to users
    # ZEZS_CITY: Municipalities
    # ZEZS_CITY_CON: Shows which cities are using eBau Aargau and Meldungshub
    # ZEZS_DATES: Deadlines
    # ZEZS_DOC_TYPES/TYPEST: Document types
    # ZEZS_DWFLOW: "Document Workflow" entries
    # ZEZS_DWFLOW_DOC: Documents referenced in a document workflow
    # ZEZS_DWFLOW_REC: Document workflow recipient (requestor, specialist office etc.)
    # ZEZS_EMAIL_ADDR: Global and local (on municipity level) recipient entries (specialist offices)
    # ZEZS_FIELDLINK: Links and info texts for eBau Aargau customer facing user interfaces
    # ZEZS_INF/INFT/LINK/LINKT: See above
    # ZEZS_VERFSTAND: Proceeding history for eBau requests
    #
    # standard SAP table for status codes:
    # TJ30T
    # runQuery("SELECT * FROM sapabap1.tj30t where stsma='ZEB2_REQ' or stsma='ZEB2_TS' or stsma='ZEBP' order by stsma");

    # runQuery("SELECT * FROM TABLES WHERE TABLE_NAME = 'ZEBP_GESUCH'")
    # runQuery("SELECT * FROM TABLE_COLUMNS WHERE TABLE_NAME = 'ZEBP_GESUCH'")

    # filter = f"ZEBP_GESUCH.GESUCH_ID = 'EBPA-8318-3681'"

    execution_time = timeit.timeit(write_gesuche_to_json, number=1)
    print(f"Writing Gesuche to JSON took {execution_time} seconds.")
    # Close the connection
    connection.close()

except dbapi.Error as err:
    print(f"Error: {err}")
