import io
import math

import requests
from django.conf import settings
from pypdf import PdfReader, PdfWriter

from camac.constants import kt_uri as uri_constants
from camac.core.import_dossiers import import_dossiers
from camac.utils import build_url


class PdfWriterWithStreamAttribute(PdfWriter):
    def __init__(self):
        super().__init__()
        self.stream = io.BytesIO()


class ParashiftValidationError(Exception):
    def __init__(self, msg, original_exception=None):
        super().__init__(msg)
        self.original_exception = original_exception


class ParashiftDataError(Exception):
    pass


class ParashiftImporter:
    """Import dossier from parashift."""

    def __init__(self, bfs_nr):
        super().__init__()
        self.bfs_nr = bfs_nr

    DATA_URI_FORMAT = build_url(
        settings.PARASHIFT["BASE_URI"],
        "/documents/{document_id}/?include="
        "document_fields&extra_fields[document_fields]=extraction_candidates",
    )

    @property
    def LINK_URI_FORMAT(self):
        tenant_id = settings.PARASHIFT[self.bfs_nr]["TENANT_ID"]
        return build_url(
            settings.PARASHIFT["SOURCE_FILES_URI"],
            f"{tenant_id}/documents/{{document_id}}?include=source_files",
        )

    LIST_URI_FORMAT = build_url(
        settings.PARASHIFT["BASE_URI"],
        "/documents?page[size]=100&page[number]={page_number}&sort=id&filter[id_lte]={to_id}&filter[id_gte]={from_id}",
    )
    TOTAL_RECORDS_URI = build_url(
        settings.PARASHIFT["BASE_URI"],
        "/documents?stats[total]=count&filter[id_lte]={to_id}&filter[id_gte]={from_id}",
    )

    schema = {
        "gesuchsteller": None,
        "gesuchsteller-backup": None,
        "erfassungsjahr": None,
        "parzelle-nr": None,
        "baurecht-nr": None,
        "gemeinde": None,
        "ort": None,
        "ort-backup": None,
        "vorhaben": None,
        "vorhaben-backup": None,
        "external-id": None,
        "barcodes": [],
        "document": None,
    }

    post_process = {
        "erfassungsjahr": (
            lambda a: int(a) + 1900 if int(a) > 70 else int(a) + 2000,
            "Must be an integer!",
        ),
        "parzelle-nr": (lambda a: int(a), "Must be an integer!"),
        "baurecht-nr": (lambda a: int(a), "Must be an integer!"),
    }

    @property
    def _record_blueprint(self):
        return dict(self.schema)

    def _get(self, *args, auth=True, **kwargs):
        return self._request(requests.get, *args, auth=auth, **kwargs)

    def _request(self, method, *args, auth=True, **kwargs):
        headers = {}
        if auth:
            headers = {
                "Authorization": f"Bearer {settings.PARASHIFT[self.bfs_nr]['API_KEY']}"
            }

        response = method(*args, **kwargs, headers=headers)
        response.raise_for_status()
        return response

    def crop_pdf(self, record):
        pdf = PdfReader(record["document"])

        try:
            record["barcodes"].pop(0)
        except IndexError:  # pragma: no cover
            print(f"{record['external-id']}: no barcode in record")
            pass

        documents = []
        for index, code in enumerate(record["barcodes"], start=1):
            section = uri_constants.PARASHIFT_ATTACHMENT_SECTION_MAPPING.get(
                code["type"]
            )
            if not section:  # pragma: no cover
                print(
                    f"{record['external-id']}: unexpected barcode type {code['type']}, skipping"
                )
                continue
            # Move the pages from the second page until the next barcode into the "Gesuchsteller" attachment section.
            # We do this because in Seedorf they forgot to add a barcode to the every second document in most dossiers.
            start = 1 if code["type"] == "BK Seedorf 1214" else code["page"]
            stop = None
            for c in record["barcodes"]:
                if c["page"] > start:
                    stop = c["page"] - 1
                    break

            if stop is None:
                stop = len(pdf.pages) - 1

            output = PdfWriterWithStreamAttribute()

            for page in range(start, stop + 1):
                output.add_page(pdf.pages[page])

            bytes_file = io.BytesIO()
            output.write(bytes_file)
            documents.append(
                {
                    "section": uri_constants.PARASHIFT_ATTACHMENT_SECTION_MAPPING[
                        code["type"]
                    ],
                    "name": f"{index}.pdf",
                    "data": bytes_file,
                }
            )

        return documents

    def run(self, from_id, to_id):
        total_records = self._get(
            self.TOTAL_RECORDS_URI.format(to_id=to_id, from_id=from_id)
        ).json()["meta"]["stats"]["total"]["count"]

        total_pages = math.ceil(total_records / 100)

        dossiers = []
        for page_number in range(1, (total_pages + 1)):
            result = self._get(
                self.LIST_URI_FORMAT.format(
                    to_id=to_id, from_id=from_id, page_number=page_number
                )
            ).json()

            for rec in result["data"]:
                data = self.fetch_data(rec["id"])

                if data:
                    data["documents"] = self.crop_pdf(data)
                    dossiers += import_dossiers([data], self.bfs_nr)

        return dossiers

    def _post_process_value(self, identifier, value, parashift_id):
        post_process = self.post_process.get(identifier)

        if post_process is None:
            return value

        try:
            return post_process[0](value)
        except Exception as e:  # noqa: B902
            raise ParashiftValidationError(
                f"{parashift_id}: {identifier}: {post_process[1]}",
                original_exception=e,
            )

    def _fetch_document(self, para_id):
        result = self._get(self.LINK_URI_FORMAT.format(document_id=para_id)).json()

        try:
            url = result["included"][0]["attributes"]["url"]
        except (KeyError, IndexError):
            raise ParashiftDataError("Couldn't fetch original PDF.")

        file_resp = self._get(url, auth=False)
        file = io.BytesIO(file_resp.content)

        return file

    overrides = {
        "201726": {"gemeinde": "Hospental 1210"},
        "203966": {"baurecht-nr": 807},
        "336690": {"gemeinde": "Unterschächen 1219"},
        "336581": {"erfassungsjahr": 2001},
        "336603": {"erfassungsjahr": 2000},
        "336625": {"erfassungsjahr": 2001},
        "336710": {"parzelle-nr": 60},
        "336747": {"erfassungsjahr": 2008},
        "336756": {"parzelle-nr": 600},
        "336782": {"gemeinde": "Spiringen 1218"},
        "336901": {"erfassungsjahr": 2005},
        "336905": {"erfassungsjahr": 2007},
        "4530658": {"gemeinde": "Gurtnellen 1209"},
        "5076182": {"parzelle-nr": 793},
        "6286188": {"erfassungsjahr": 1980},
        "6286189": {"erfassungsjahr": 1980},
    }

    def _set_location(self, value, identifier, record):
        if identifier == "ort-backup":
            record["ort-backup"] = value

        if identifier == "nähere ortsbezeichnung":
            record["ort"] = value

    def _set_intent(self, value, identifier, record):
        if identifier == "vorhaben-backup":
            record["vorhaben-backup"] = value

        if identifier == "vorhaben":
            record["vorhaben"] = value

    def _set_applicant(self, value, identifier, record):
        if identifier == "gesuchsteller-backup":
            record["gesuchsteller-backup"] = value

        if identifier == "gesuchsteller":
            record["gesuchsteller"] = value

    def fetch_data(self, para_id):
        json_doc = self._get(self.DATA_URI_FORMAT.format(document_id=para_id)).json()
        if json_doc["data"]["attributes"]["status"] == "failed":  # pragma: no cover
            print(
                f"The record with id {para_id} could not be imported. The state of the record is {json_doc['data']['attributes']['status']}."
            )
            return None

        record = self._record_blueprint

        record["external-id"] = json_doc["data"]["id"]

        for field in json_doc["included"]:
            identifier = field["attributes"]["identifier"]
            value = field["attributes"]["value"]

            if identifier == "barcodes":
                record["barcodes"] = sorted(
                    [
                        {
                            "type": code["prediction_value"],
                            "page": code["page_number"],
                        }
                        for code in field["attributes"]["extraction_candidates"]
                    ],
                    key=lambda k: k["page"],
                )
                record["gemeinde"] = (
                    self.overrides.get(record["external-id"], {}).get("gemeinde")
                    or value
                )

                continue

            self._set_location(value, identifier, record)
            self._set_intent(value, identifier, record)
            self._set_applicant(value, identifier, record)

            if identifier not in record or value is None:
                continue

            try:
                value = self.overrides.get(record["external-id"], {}).get(
                    identifier
                ) or self._post_process_value(identifier, value, record["external-id"])
            except Exception as e:  # noqa: B902
                print(e)
                return None

            record[identifier] = value

        record["document"] = self._fetch_document(para_id)

        return record
