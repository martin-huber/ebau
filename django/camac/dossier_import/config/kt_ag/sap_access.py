import json
import os
from datetime import datetime

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
        return float(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj

def runQuery(query: str, subqueries=None):
    if subqueries is None:
        subqueries = {}
    # Create a cursor object
    cursor = connection.cursor()
    cursor.execute(f"SET SCHEMA {schema}")

    result = execute(cursor, query)
    if len(result) == 1:
        result = result[0]
        for k, v in subqueries.items():
            result[k] = execute(cursor, v)

    # Close the cursor and connection
    cursor.close()
    connection.close

    print(json.dumps(result, indent=4, default=encode))
    return result


def execute(cursor, query):
    cursor.execute(query)
    # Fetch the results
    result = [dict(zip(r.column_names, r.column_values)) for r in cursor.fetchall()]
    return result


# Establish a connection
try:
    connection = dbapi.connect(
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

    gesuchs_id = "EBPA-8318-3681"
    query = f"""select ZEBP_GESUCH.*, ZEBP_ENTSCHEID.*
                from ZEBP_GESUCH
                    left join ZEBP_ENTSCHEID on ZEBP_GESUCH.GESUCH_ID = ZEBP_ENTSCHEID.EXTERN_ID
                where ZEBP_GESUCH.GESUCH_ID = '{gesuchs_id}'"""

    stort_query = f"""select ZEBP_STORT.*, ZEZS_CITY.*, ZEBP_PARZ.*
                        from ZEBP_STORT
                                 left join ZEBP_PARZ on (ZEBP_STORT.city_id = ZEBP_PARZ.city_id and ZEBP_STORT.GESUCH_ID = ZEBP_PARZ.GESUCH_ID)
                                 left join ZEZS_CITY on ZEBP_STORT.CITY_ID = ZEZS_CITY.CITY_ID
                        where ZEBP_STORT.GESUCH_ID = '{gesuchs_id}'"""
    subqueries = {"STORT": stort_query}

    runQuery(query, subqueries)

    # Close the connection
    connection.close()

except dbapi.Error as err:
    print(f"Error: {err}")
