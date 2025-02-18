import json
import os
import timeit
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, Generator, Optional

from dotenv import load_dotenv
from hdbcli import dbapi
from hdbcli.dbapi import Cursor, Decimal

QUERY_DOSSIERS = """select ZEBP_GESUCH.*, ZEBP_ENTSCHEID.*, TJ30T.*, ZEZS_CITY.*
                        from ZEBP_GESUCH
                                 left join ZEBP_ENTSCHEID on ZEBP_GESUCH.GESUCH_ID = ZEBP_ENTSCHEID.EXTERN_ID
                                 left join TJ30T on TJ30T.ESTAT = ZEBP_GESUCH.ESTAT and TJ30T.STSMA = 'ZEBP'
                                 left join ZEZS_CITY on ZEZS_CITY.CITY_ID = ZEBP_GESUCH.GEMEINDE_ID
                     """
SUBQUERY_LOCATIONS = """select ZEBP_STORT.*, ZEZS_CITY.*
                        from ZEBP_STORT
                                 left join ZEZS_CITY on ZEBP_STORT.CITY_ID = ZEZS_CITY.CITY_ID
                        where ZEBP_STORT.GESUCH_ID = ?
                    """

SUBQUERY_PLOTS = """select ZEBP_PARZ.*, ZEZS_CITY.*
                        from ZEBP_PARZ
                                 left join ZEZS_CITY on ZEBP_STORT.CITY_ID = ZEZS_CITY.CITY_ID
                        where ZEBP_PARZ.GESUCH_ID = ?
                    """

SUBQUERY_CONTACTS = """select ZEBP_KONTAKT.*
                        from ZEBP_KONTAKT
                        where ZEBP_KONTAKT.GESUCH_ID = ?
                    """
SUBQUERY_DATES = """select ZEZS_DATES.*
                    from ZEZS_DATES
                    where process_id = 'ZEBP' and
                    ZEZS_DATES.EXTERN_ID = ?
                """
SUBQUERIES = {
    "STANDORTE": SUBQUERY_LOCATIONS,
    "PARZELLEN": SUBQUERY_PLOTS,
    "KONTAKTE": SUBQUERY_CONTACTS,
    "DATES": SUBQUERY_DATES,
}


class SAPAccess:
    def __init__(self):
        self._connection = None

        # Load environment variables
        load_dotenv()

        # Initialize SAP HANA connection parameters
        host = os.getenv("HANA_HOST")
        port = int(os.getenv("HANA_PORT"))
        user = os.getenv("HANA_USER")
        password = os.getenv("HANA_PASSWORD")
        dbname = os.getenv("HANA_DBNAME")
        self._schema = os.getenv("HANA_SCHEMA")

        try:
            self._connection = dbapi.connect(
                address=host,
                port=port,
                user=user,
                password=password,
                databaseName=dbname,
            )
            print("Connected to SAP HANA successfully.")
        except dbapi.Error as err:
            print(f"Error: {err}")

    def close_connection(self):
        if self._connection:
            self._connection.close()
            print("Connection closed.")

    @contextmanager
    def _managed_cursor(self):
        cursor = self._connection.cursor()
        try:
            yield cursor
        finally:
            cursor.close()

    def _run_query(
        self, query: str, subqueries=None, filter=None, batch_size=100, limit=None
    ) -> Generator[Dict, None, None]:
        if subqueries is None:
            subqueries = {}

        with self._managed_cursor() as cursor, self._managed_cursor() as sub_cursor:
            cursor.execute(f"SET SCHEMA {self._schema}")

            if filter:
                query = f"{query} WHERE {filter}"

            result = self._execute(cursor, query, batch_size=batch_size, limit=limit)
            for r in result:
                for k, v in subqueries.items():
                    r[k] = [
                        e
                        for e in self._execute(
                            sub_cursor, v, (r["GESUCH_ID"]), is_subquery=True
                        )
                    ]
                yield r

    def _execute(
        self,
        cursor: Cursor,
        query,
        params=None,
        batch_size=100,
        limit=None,
        is_subquery=False,
    ):
        if limit:
            query = f"{query} LIMIT {limit}"
        cursor.execute(query, params)

        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break
            if not is_subquery:
                print(".", end="")
            yield from (dict(zip(r.column_names, r.column_values)) for r in rows)

    @staticmethod
    def _encode(obj):
        if isinstance(obj, Decimal):
            return int(obj) if obj == obj.to_integral() else float(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return obj

    def _write_json_file(self, data: str, relative_path: str, filename):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        first_digit, second_digit = filename.split("-")[1][:2]
        target_dir = os.path.join(
            base_dir, f"{relative_path}/{first_digit}/{second_digit}"
        )
        os.makedirs(target_dir, exist_ok=True)

        file_path = os.path.join(target_dir, filename)
        with open(file_path, "w", encoding="utf-8") as file:
            file.write(data)

    def _write_dossiers_to_json(self):
        result = self.query_dossiers()
        i = 0
        for r in result:
            i += 1
            json_str = json.dumps(r, indent=4, default=self._encode)
            self._write_json_file(json_str, "database/json/", f"{r['GESUCH_ID']}.json")
        print(f"Read {i} Gesuche from database")

    def _read_dossiers_from_json(self):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        target_dir = Path(os.path.join(base_dir, "database/json/"))

        for json_file in target_dir.rglob("*.json"):
            with open(json_file, "r", encoding="utf-8") as file:
                yield json.load(file)

    def query_dossiers(
        self,
        filter: Optional[str] = None,
        batch_size: Optional[int] = 500,
        limit: Optional[int] = None,
    ) -> Generator[Dict, None, None]:
        if self._connection:
            return self._run_query(
                QUERY_DOSSIERS,
                SUBQUERIES,
                batch_size=batch_size,
                filter=filter,
                limit=limit,
            )
        else:
            return self._read_dossiers_from_json()


if __name__ == "__main__":
    db_client = SAPAccess()
    try:
        execution_time = timeit.timeit(db_client._write_dossiers_to_json, number=1)
        print(f"Writing Gesuche to JSON took {execution_time} seconds.")
    finally:
        db_client.close_connection()
