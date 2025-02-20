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
    assert result.city == "Möhlin"
    assert result.street == "Musterstrasse"
    assert result.street_number == "1"
