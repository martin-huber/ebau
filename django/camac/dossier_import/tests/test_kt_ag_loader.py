from camac.dossier_import.config.kt_ag.dossier_loader import KtAargauDossierLoader
from camac.dossier_import.dossier_classes import Dossier


def test_simple_mapping():
    result: Dossier = KtAargauDossierLoader._map(
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
    assert result._meta.target_state == "APPROVED"
    assert result.proposal == "EFH Muster"
    assert result.municipal_id == "2021-344"
    assert result.cantonal_id == "BVUAFB.21.104"
    assert result.submit_date == "20210629"
    assert result.responsible_municipality == "Möhlin"
    assert result.city == "Möhlin"
    assert result.street == "Musterstrasse"
    assert result.street_number == "1"


def test_missing_fields():
    result: Dossier = KtAargauDossierLoader._map({})
    assert result
    assert result.id is None
    assert result._meta.target_state is None


def test_mapping_for_multiple_locations():
    result: Dossier = KtAargauDossierLoader._map(
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
            ],
        }
    )

    assert result.responsible_municipality == "Möhlin"
    assert result.city == "Möhlin"
    assert result.street == "Musterstrasse"
    assert result.street_number == "1"
