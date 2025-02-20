from typing import Dict

from jsonpath_ng.ext import parse

from camac.dossier_import.config.kt_ag.sap_access import SAPAccess
from camac.dossier_import.dossier_classes import Dossier
from camac.dossier_import.loaders import DossierLoader
from camac.dossier_import.models import DossierImport

DATE_FORMAT = "%d.%m.%Y"

MAPPING = {
    "id": "GESUCH_ID",
    "proposal": "BTITEL",
    "cantonal_id": "BVUAFBNR",
    "municipal_id": "GEMEINDE_BG",
    "submit_date": "EINDAT",
    "responsible_municipality": "CITY",
    "city": "CITY",
    "street": "STANDORTE[?(@.CITY == '{CITY}')].STRASSE",
    "street_number": "STANDORTE[?(@.CITY == '{CITY}')].STRASNR",
}

# SUBMITTED, APPROVED, REJECTED, WRITTEN_OFF, DONE

VALUE_MAPPING = {
    "target_state": {
        "Gesuch in Erfassung": "SUBMITTED",  # TODO should be DRAFT
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

    @staticmethod
    def _map(r: Dict) -> Dossier:
        # build the jsonpath expression for each field, substitude placeholders with toplevel dict values and search
        # for the value of the jsonpath expression in the dict
        mapped_values = {
            field: next(
                (m.value for m in parse(f"$.{jsonpath}".format(**r)).find(r)), None
            )
            for field, jsonpath in MAPPING.items()
        }

        dossier = Dossier(**mapped_values)
        dossier._meta = Dossier.Meta(
            target_state=VALUE_MAPPING["target_state"][r[TARGET_STATE_KEY]]
        )
        return dossier
