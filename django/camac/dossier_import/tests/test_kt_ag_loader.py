from camac.dossier_import.config.kt_ag.dossier_loader import KtAargauDossierLoader
from camac.dossier_import.dossier_classes import Dossier


def test_simple_mapping():
    result: Dossier = KtAargauDossierLoader.map_data(
        {
            "GESUCH_ID": "EBPA-0001-6924",
            "TXT30": "Verfügung erstellt",
            "BTITEL": "EFH Muster",
            "GEMEINDE_BG": "2021-344",
            "BVUAFBNR": "BVUAFB.21.104",
            "EINDAT": "20210629",
            "CITY": "Möhlin",
            "STANDORTE": [
                {
                    "STRASSE": "Musterstrasse",
                    "STRASNR": "1",
                    "KOORDB": "1300000",
                    "KOORDL": "2480000",
                    "POSTAL_CODE": "4663",
                    "CITY": "Möhlin",
                }
            ],
        }
    )

    assert result.id == "EBPA-0001-6924"
    assert result._meta.target_state == "Verfügung erstellt"
    assert result.proposal == "EFH Muster"
    assert result.municipal_id == "2021-344"
    assert result.cantonal_id == "BVUAFB.21.104"
    assert result.submit_date == "20210629"
    assert result.responsible_municipality == "Möhlin"
    assert result.city == "Möhlin"
    assert result.street == "Musterstrasse"
    assert result.street_number == "1"


def test_missing_fields():
    result: Dossier = KtAargauDossierLoader.map_data({})
    assert result
    assert result.id is None
    assert result._meta.target_state is None


def test_mapping_for_multiple_locations():
    result: Dossier = KtAargauDossierLoader.map_data(
        {
            "CITY": "Möhlin",
            "STANDORTE": [
                {
                    "STRASSE": "Andere Strasse",
                    "STRASNR": "5",
                    "CITY": "Aarburg",
                },
                {
                    "STRASSE": "Musterstrasse",
                    "STRASNR": "1",
                    "CITY": "Möhlin",
                },
                {
                    "STRASSE": "Nicht gematchte Strasse",
                    "STRASNR": "11",
                    "CITY": "Möhlin",
                },
            ],
        }
    )

    assert result.responsible_municipality == "Möhlin"
    assert result.city == "Möhlin"
    # should map the first location where CITY is equal to the parent CITY
    assert result.street == "Musterstrasse"
    assert result.street_number == "1"


def test_mapping_of_nested_lists():
    result: Dossier = KtAargauDossierLoader.map_data(
        {
            "GESUCH_ID": "EBPA-0001-6924",
            "TXT30": "Verfügung erstellt",
            "BTITEL": "EFH Muster",
            "GEMEINDE_BG": "2021-344",
            "BVUAFBNR": "BVUAFB.21.104",
            "EINDAT": "20210629",
            "CITY": "Aarau",
            "STANDORTE": [
                {
                    "STRASSE": "Aarburg-Mapping-Str. 5",
                    "STRASNR": "",
                    "EGID": "",
                    "KOORDB": "1111",
                    "KOORDL": "1111",
                    "CITY": "Aarburg",
                },
                {
                    "STRASSE": "Aarau-Mapping-Str. 1",
                    "STRASNR": "",
                    "EGID": "",
                    "KOORDB": "b2222",
                    "KOORDL": "l2222",
                    "CITY": "Aarau",
                },
            ],
            "PARZELLEN": [
                {
                    "SGUID": "e1a5f3d1-7126-4a7d-b355-b3ac8ad9",
                    "PGUID": "f32b7723-87ed-4010-8c2a-38ad3e09",
                    "PARZNR": "123123",
                    "PARZM2": "",
                    "CITY": "Aarburg",
                },
                {
                    "SGUID": "3ed4fbbd-e6a5-4daf-a5dc-177fc47e",
                    "PGUID": "6e0dbfb8-62f0-4f86-a597-d9e0edc5",
                    "PARZNR": "234234",
                    "PARZM2": "",
                    "CITY": "Aarau",
                },
            ],
        }
    )

    assert len(result.coordinates) == 1
    assert result.coordinates[0].n == "b2222"
    assert result.coordinates[0].e == "l2222"

    assert len(result.plot_data) == 2
    assert result.plot_data[0].number == "123123"
    assert result.plot_data[0].municipality == "Aarburg"
    assert result.plot_data[0].egrid is None
    assert result.plot_data[1].number == "234234"
    assert result.plot_data[1].municipality == "Aarau"
    assert result.plot_data[1].egrid is None
