from django.camac.dossier_import.config.kt_ag import DATE_FORMAT


class KtAargauDossierLoader(DossierLoader):
    def load_dossiers(self, param: DossierImport):
        dossier = Dossier(
            id="EBPA-1088-5651",  # EBPA-Nr. , Dossier-ID/Stichworte [keywords]
            proposal="Testgesuch für Schulung 11.11.24 & 25.11.25",  # Titel , Titel des Vorhabens [beschreibung-bauvorhaben]
            submit_date=datetime.strptime("06.11.2024", DATE_FORMAT),
        )
        dossier._meta = Dossier.Meta(target_state="SUBMITTED")
        print(f"Importing {dossier.id}")
        yield dossier

        dossier = Dossier(
            id="EBPA-1716-3966",  # EBPA-Nr. , Dossier-ID/Stichworte [keywords]
            proposal="Testgesuch für Dokumentation",  # Titel , Titel des Vorhabens [beschreibung-bauvorhaben]
            submit_date=datetime.strptime("26.08.2024", DATE_FORMAT),
        )
        dossier._meta = Dossier.Meta(target_state="SUBMITTED")
        print(f"Importing {dossier.id}")
        yield dossier
