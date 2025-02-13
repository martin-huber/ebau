from typing import Dict

from camac.dossier_import.config.kt_ag.sap_access import SAPAccess
from camac.dossier_import.dossier_classes import Dossier
from camac.dossier_import.loaders import DossierLoader
from camac.dossier_import.models import DossierImport

DATE_FORMAT = "%d.%m.%Y"


class KtAargauDossierLoader(DossierLoader):
    def __init__(self):
        self._sap_access = SAPAccess()

    def load_dossiers(self, param: DossierImport):
        yield from (self._map(r) for r in self._sap_access.query_applications())

    def _map(self, r: Dict) -> Dossier:
        dossier = Dossier(id=r["GESUCH_ID"], proposal=r["BTITEL"])
        return dossier
