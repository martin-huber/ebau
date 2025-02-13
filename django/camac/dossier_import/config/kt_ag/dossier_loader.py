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
    "submit_date": "EINDAT",
}

VALUE_MAPPING = {
    "target_state": {
        "Gesuch in Erfassung": "new",
        "Gesuch übermittelt": "subm",
        "Gesuch storniert": "rejected",
        "Gesuch in Bearbeitung": "subm",
        "Anfrage / Stellungnahme offen": "circulation",
        "In öffentlicher Auflage": "circulation",
        "Verfügung erstellt": "decision",
        "Gesuch zurückgezogen": "withdrawn",
        "Gesuch abgeschrieben": "finished",
        "Gesuch archiviert": "finished",
        "Gesuch Offline erfasst": "subm",
        "Rückbau bestätigt": "construction-acceptance",
        "An Kanton gesendet": "circulation",
    }
}

TARGET_STATE_KEY = "TXT30"


class KtAargauDossierLoader(DossierLoader):
    def __init__(self):
        self._sap_access = SAPAccess()

    def load_dossiers(self, param: DossierImport):
        yield from (self._map(r) for r in self._sap_access.query_applications())

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
