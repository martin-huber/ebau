from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Type, TypeVar, Union

from jsonpath_ng.ext import parse

from camac.dossier_import.config.kt_ag.sap_access import SAPAccess
from camac.dossier_import.dossier_classes import Coordinates, Dossier, PlotData
from camac.dossier_import.loaders import DossierLoader
from camac.dossier_import.models import DossierImport

T = TypeVar("T")


@dataclass
class Mapping:
    data_path: str
    result_type: Type
    mappings: Dict[str, Union[str, "Mapping"]]


MAPPING = Mapping(
    "$",
    Dossier,
    {
        "id": "GESUCH_ID",
        "proposal": "BTITEL",
        "cantonal_id": "BVUAFBNR",
        "municipal_id": "GEMEINDE_BG",
        "submit_date": "EINDAT",
        "responsible_municipality": "CITY",
        "city": "CITY",
        "street": "STANDORTE[?(@.CITY == '{CITY}')].STRASSE",
        "street_number": "STANDORTE[?(@.CITY == '{CITY}')].STRASNR",
        "plot_data": Mapping(
            "PARZELLEN[*]",
            PlotData,
            {
                "number": "PARZNR",
                "municipality": "CITY",
                "egrid": "TODO",  # todo
            },
        ),
        "coordinates": Mapping(
            "STANDORTE[?(@.CITY == '{CITY}')]",
            Coordinates,
            {
                "n": "KOORDB",
                "e": "KOORDL",
            },
        ),
        "_meta": Mapping(
            "$",
            Dossier.Meta,
            {"target_state": "TXT30"},
        ),
    },
)


class KtAargauDossierLoader(DossierLoader):
    def __init__(self):
        self._sap_access = SAPAccess()

    def load_dossiers(self, param: DossierImport):
        yield from (self.map_data(data) for data in self._sap_access.query_dossiers())

    @classmethod
    def map_data(cls, data):
        return cls._map(MAPPING.mappings, MAPPING.result_type, data)

    @classmethod
    def _map(
        cls, mappings: Dict[str, Union[str, Mapping]], target_class: T, data: Dict
    ) -> T:
        mapped_values = {
            field: cls._extract_value(mapping, data)
            for field, mapping in mappings.items()
        }

        return target_class(**mapped_values)

    @classmethod
    def _extract_value(cls, mapping: Union[str, Mapping], data: Dict):
        if type(mapping) is str:
            # build the jsonpath expression for each field, substitude placeholders with toplevel dict values and search
            # for the value of the jsonpath expression in the dict
            return next(
                (
                    m.value
                    for m in parse(
                        f"$.{mapping}".format_map(defaultdict(lambda: None, data))
                    ).find(data)
                ),
                None,
            )
        else:
            mapping: Mapping
            if mapping.data_path and mapping.data_path != "$":
                subdata_list = parse(
                    f"$.{mapping.data_path}".format_map(defaultdict(lambda: None, data))
                ).find(data)
                result = []
                for d in subdata_list:
                    result.append(
                        cls._map(mapping.mappings, mapping.result_type, d.value)
                    )
                return result

            else:
                return cls._map(mapping.mappings, mapping.result_type, data)
