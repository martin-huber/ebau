from typing import Dict

from jsonpath_ng import parse

from camac.dossier_import.config.kt_ag.sap_access import SAPAccess
from camac.dossier_import.dossier_classes import Dossier
from camac.dossier_import.loaders import DossierLoader
from camac.dossier_import.models import DossierImport

DATE_FORMAT = "%d.%m.%Y"

MAPPING = {
    "id": "GESUCH_ID",
    "proposal": "BTITEL",
    "cantonal_id": "GEMEINDE_BG",
    "municipal_id": "BVUAFBNR",
    "submit_date": "EINDAT",
    "city": "CITY",
    "street": "STANDORTE[0].STRASSE",
    "street_number": "STANDORTE[0].STRASNR",
}

# SUBMITTED, APPROVED, REJECTED, WRITTEN_OFF, DONE

VALUE_MAPPING = {
    "target_state": {
        "Gesuch in Erfassung": "DRAFT",
        "Gesuch übermittelt": "SUBMITTED",
        "Gesuch storniert": "DONE",
        "Gesuch in Bearbeitung": "SUBMITTED",
        "Anfrage / Stellungnahme offen": "SUBMITTED",
        "In öffentlicher Auflage": "SUBMITTED",
        "Verfügung erstellt": "APPROVED",
        "Gesuch zurückgezogen": "DONE",
        "Gesuch abgeschrieben": "DONE",
        "Gesuch archiviert": "DONE",
        "Gesuch Offline erfasst": "SUBMITTED",
        "Rückbau bestätigt": "DONE",
        "An Kanton gesendet": "SUBMITTED",
    }
}

TARGET_STATE_KEY = "TXT30"


class KtAargauDossierLoader(DossierLoader):
    def __init__(self):
        self._sap_access = SAPAccess()

    def load_dossiers(self, param: DossierImport):
        yield from (self._map(r) for r in self._sap_access.query_dossiers())

    def _map(self, r: Dict) -> Dossier:
        mapped_values = {
            field: parse(f"$.{jsonpath}").find(r)[0].value
            for field, jsonpath in MAPPING.items()
        }

        dossier = Dossier(**mapped_values)
        dossier._meta = Dossier.Meta(
            target_state=VALUE_MAPPING["target_state"][r[TARGET_STATE_KEY]]
        )
        return dossier
