import io
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from pypdf import PdfReader

from camac.constants import kt_uri as uri_constants
from camac.instance.master_data import MasterData
from camac.parashift.parashift import ParashiftImporter
from camac.utils import build_url

DATA_DIR = Path(settings.ROOT_DIR) / "camac" / "parashift" / "tests" / "data"


def test_import(
    db,
    parashift_mock,
):
    expected = {
        "gesuchsteller": "Kanton Uri, v.d. Baudirektion Uri",
        "gesuchsteller-backup": "Kanton Uri, v.d. Baudirektion Uri",
        "erfassungsjahr": 1992,
        "parzelle-nr": 11,
        "baurecht-nr": None,
        "gemeinde": "Gurtnellen 1209",
        "vorhaben": "Erschliessungsstrasse",
        "vorhaben-backup": "Erschliessungsstrasse",
        "ort": "Platti, Amsteg",
        "ort-backup": "Platti, Amsteg",
        "external-id": "138866",
        "barcodes": [
            {"type": "Gurtnellen 1209", "page": 0},
            {"type": "Fachstellen", "page": 1},
            {"type": "Fachstellen", "page": 3},
            {"type": "Fachstellen", "page": 4},
            {"type": "Gesuchsteller", "page": 6},
        ],
        "document": io.BytesIO((DATA_DIR / "dossier.pdf").open("br").read()).getvalue(),
    }

    client = ParashiftImporter("KOOR_BG")
    record = client.fetch_data("138866")
    record["document"] = record["document"].getvalue()

    assert record == expected


def test_import_validation_error(requests_mock, capsys):
    broken_data = {
        "data": {
            "id": "138866",
            "attributes": {
                "status": "done",
            },
        },
        "included": [
            {
                "attributes": {
                    "identifier": "parzelle-nr",
                    "value": "string not int",
                },
            }
        ],
    }

    requests_mock.register_uri(
        "GET",
        build_url(
            settings.PARASHIFT["BASE_URI"],
            "/documents/138866/?include="
            "document_fields&extra_fields[document_fields]=extraction_candidates",
        ),
        json=broken_data,
    )

    client = ParashiftImporter("KOOR_BG")
    record = client.fetch_data("138866")
    assert record is None
    assert capsys.readouterr().out == "138866: parzelle-nr: Must be an integer!\n"


@pytest.mark.parametrize(
    "bfs_nr,run_again",
    [
        ("1214", True),
        ("KOOR_BG", False),
    ],
)
def test_command(
    parashift_data,
    parashift_mock,
    application_settings,
    master_data_is_visible_mock,
    workflow_item_factory,
    group_factory,
    bfs_nr,
    run_again,
    ur_master_data_settings,
    settings,
):
    koor_bg = group_factory()
    gbb_seedorf = group_factory()
    settings.PARASHIFT["1214"]["CAMAC_GROUP_ID"] = gbb_seedorf.pk
    settings.PARASHIFT["KOOR_BG"]["CAMAC_GROUP_ID"] = koor_bg.pk

    workflow_item_factory(pk=uri_constants.WORKFLOW_ITEM_DOSSIER_ERFASST)

    client = ParashiftImporter(bfs_nr=bfs_nr)
    instances = client.run("138866", "138867")
    instance = instances[0]
    master_data = MasterData(instance.case)

    assert master_data.applicants[0]["last_name"] == "Kanton Uri, v.d. Baudirektion Uri"
    assert master_data.proposal == "Erschliessungsstrasse"
    assert master_data.plot_data[0]["plot_number"] == "11"
    assert master_data.street == "Platti, Amsteg"

    answers = instance.case.document.answers.all()
    assert answers.get(question_id="form-type").value == "form-type-archiv"

    # master data doesn't work here because DynamicOption record is missing
    # when creating answers through caluma python api
    assert answers.get(question_id="municipality").value == str(
        instance.location.communal_federal_number
    )

    # one PDF, split into 4 pieces
    assert instance.attachments.count() == 4
    attachment = instance.attachments.order_by("name").first()
    assert attachment.path.size == 91784

    if run_again:
        instances = client.run("138866", "138867")
        instance = instances[0]
        master_data = MasterData(instance.case)

        assert (
            master_data.applicants[0]["last_name"]
            == "Kanton Uri, v.d. Baudirektion Uri"
        )
        assert master_data.proposal == "Erschliessungsstrasse"
        assert master_data.plot_data[0]["plot_number"] == "11"
        assert master_data.street == "Platti, Amsteg"

        answers = instance.case.document.answers.all()
        assert answers.get(question_id="form-type").value == "form-type-archiv"

        assert answers.get(question_id="municipality").value == str(
            instance.location.communal_federal_number
        )

        assert instance.attachments.count() == 4
        attachment = instance.attachments.order_by("name").first()
        assert attachment.path.size == 91784


def test_command_validation_error(requests_mock, capsys):
    data = {
        "data": {
            "id": "138866",
            "attributes": {
                "status": "done",
            },
        },
        "included": [
            {
                "attributes": {
                    "identifier": "parzelle-nr",
                    "value": "string not int",
                },
            }
        ],
        "meta": {"stats": {"total": {"count": 1}}},
    }

    broken_data = {
        "data": [
            {
                "id": "138866",
            }
        ],
        "included": [
            {
                "attributes": {
                    "identifier": "parzelle-nr",
                    "value": "string not int",
                },
            }
        ],
        "meta": {"stats": {"total": {"count": 1}}},
    }

    requests_mock.register_uri(
        "GET",
        build_url(
            settings.PARASHIFT["BASE_URI"],
            "/documents?filter[id_lte]=138867&filter[id_gte]=138866",
        ),
        json=broken_data,
    )
    requests_mock.register_uri(
        "GET",
        build_url(
            settings.PARASHIFT["BASE_URI"],
            "/documents/138866/?include="
            "document_fields&extra_fields[document_fields]=extraction_candidates",
        ),
        json=data,
    )

    call_command("parashift_import", "138866", "138867", "KOOR_BG")
    out = capsys.readouterr().out
    assert out.rsplit("\n")[0] == "138866: parzelle-nr: Must be an integer!"


def test_command_data_error(parashift_mock, requests_mock):
    broken_data = {
        "data": [
            {
                "id": "138866",
                "type": "documents",
            }
        ],
        "included": [],
        "meta": {"stats": {"total": {"count": 1}}},
    }

    tenant_id = settings.PARASHIFT["KOOR_BG"]["TENANT_ID"]
    requests_mock.register_uri(
        "GET",
        build_url(
            settings.PARASHIFT["SOURCE_FILES_URI"],
            f"/{tenant_id}/documents/138866?include=source_files",
        ),
        json=broken_data,
    )
    requests_mock.register_uri(
        "GET",
        build_url(
            settings.PARASHIFT["BASE_URI"],
            "/documents?filter[id_lte]=138867&filter[id_gte]=138866",
        ),
        json=broken_data,
    )

    out = io.StringIO()
    call_command("parashift_import", "138866", "138867", "KOOR_BG", stderr=out)
    assert out.getvalue() == "Couldn't import dossiers: Couldn't fetch original PDF.\n"


def test_pdf_cropping():
    record = {
        "gesuchsteller": "Kanton Uri, v.d. Baudirektion Uri",
        "erfassungsjahr": 1992,
        "parzelle-nr": 11,
        "baurecht-nr": None,
        "gemeinde": "Gurtnellen 1209",
        "ort": "Platti, Amsteg",
        "vorhaben": "Erschliessungsstrasse",
        "external-id": "138866",
        "barcodes": [
            {"type": "Gurtnellen 1209", "page": 0},
            {"type": "Fachstellen", "page": 1},
            {"type": "Fachstellen", "page": 3},
            {"type": "Fachstellen", "page": 4},
            {"type": "Gesuchsteller", "page": 8},
        ],
        "document": io.BytesIO((DATA_DIR / "dossier.pdf").open("br").read()),
    }
    documents = ParashiftImporter("KOOR_BG").crop_pdf(record)
    assert len(documents) == 4
    for counter, doc in enumerate(documents, 1):
        assert doc["name"] == f"{counter}.pdf"

    doc1 = documents[0]["data"]
    pdf = PdfReader(doc1)
    assert len(pdf.pages) == 2

    doc4 = documents[-1]["data"]
    pdf = PdfReader(doc4)
    assert len(pdf.pages) == 4
