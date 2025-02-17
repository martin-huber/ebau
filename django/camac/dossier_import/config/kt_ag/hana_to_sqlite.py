import sqlite3

import hdbcli.dbapi


def fetch_table_data(cursor, table_name):
    cursor.execute(f"SELECT * FROM {table_name}")
    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()
    return columns, rows


def load_into_sqlite_file(data_dict, db_path="data.db"):
    """Lädt Tabellen in eine SQLite File-Datenbank."""
    conn = sqlite3.connect(db_path)  # Verbindung zur SQLite-Datei herstellen
    cur = conn.cursor()

    for table_name, (columns, rows) in data_dict.items():
        # Tabelle erstellen
        column_def = ", ".join(f"{col} TEXT" for col in columns)
        create_query = f"CREATE TABLE IF NOT EXISTS {table_name} ({column_def})"
        cur.execute(create_query)

        # Daten einfügen
        placeholders = ", ".join("?" * len(columns))
        insert_query = f"INSERT INTO {table_name} VALUES ({placeholders})"
        cur.executemany(insert_query, rows)

    conn.commit()
    conn.close()


# Verbindung zu SAP HANA herstellen und Tabellen laden
sap_conn = hdbcli.dbapi.connect(
    address="your-db-host", port=30015, user="your-username", password="your-password"
)
sap_cursor = sap_conn.cursor()

# Liste der Tabellen, die exportiert werden sollen
tables = ["your_table1", "your_table2"]
data_dict = {}

for table in tables:
    columns, rows = fetch_table_data(sap_cursor, table)
    data_dict[table] = (columns, rows)

sap_cursor.close()
sap_conn.close()

# Daten in SQLite-Datei schreiben
sqlite_file_path = "exported_data.sqlite"
load_into_sqlite_file(data_dict, sqlite_file_path)

print(f"Daten wurden erfolgreich nach {sqlite_file_path} exportiert.")
