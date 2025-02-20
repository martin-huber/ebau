import re
import xml.dom.minidom as minidom
from collections import namedtuple
from datetime import date, datetime

import pytest
from alexandria.core.factories import DocumentFactory, FileFactory, TagFactory
from caluma.caluma_form import models as caluma_form_models
from caluma.caluma_form.models import DynamicOption
from caluma.caluma_workflow import api as workflow_api, models as caluma_workflow_models
from django.core.management import call_command
from lxml import etree

from camac.instance.domain_logic import CreateInstanceLogic
from camac.instance.serializers import SUBMIT_DATE_FORMAT
from camac.tests.data import so_personal_row_factory


@pytest.fixture
def ech_instance_sz(
    attachment_factory,
    caluma_workflow_config_sz,
    ech_instance,
    sz_person_factory,
    form_factory,
    instance_factory,
    instance_with_case,
    instance_group,
    caluma_config_sz,
    work_item_factory,
    location,
    utils,
):
    ech_instance = instance_with_case(ech_instance)
    ech_instance.instance_group = instance_group

    for role in [
        "bauherrschaft",
        "vertreter-mit-vollmacht",
        "grundeigentumerschaft",
        "projektverfasser-planer",
    ]:
        title = None
        if role == "bauherrschaft":
            title = "Firma"
        sz_person_factory(ech_instance, role, title=title)

    ech_instance.identifier = CreateInstanceLogic.generate_identifier(
        ech_instance, prefix="TEST"
    )
    ech_instance.form = form_factory(name="application_type")
    attachment_factory(instance=ech_instance)
    call_command("loaddata", "/app/kt_schwyz/config/buildingauthority.json")
    ba_work_item = work_item_factory(
        task_id="building-authority", case=ech_instance.case
    )
    utils.add_table_answer(
        ba_work_item.document,
        "baukontrolle-realisierung-table",
        [{"baukontrolle-realisierung-baubeginn": datetime.now()}],
    )
    utils.add_answer(
        ba_work_item.document,
        "bewilligungsverfahren-gr-sitzung-bewilligungsdatum",
        datetime.now(),
    )
    ech_instance.location = location
    ech_instance.save()
    # create a linked instance identified by relation to the same instance_group
    instance = instance_with_case(instance_factory())
    instance.instance_group = ech_instance.instance_group
    instance.save()
    return ech_instance


@pytest.fixture
def ech_instance(
    db,
    admin_user,
    instance_service_factory,
    service_t_factory,
    instance_with_case,
    instance_factory,
    applicant_factory,
):
    instance = instance_factory(pk=2323)
    inst_serv = instance_service_factory(
        instance__user=admin_user,
        instance=instance,
        service__name=None,
        service__city=None,
        service__zip="3400",
        service__address="Teststrasse 23",
        service__email="burgdorf@example.com",
        service__trans=None,
        service__service_group__name="municipality",
        active=1,
    )

    service_t_factory(
        service=inst_serv.service,
        language="de",
        name="Leitbehörde Burgdorf",
        city="Burgdorf",
    )

    applicant_factory(invitee=admin_user, instance=instance)

    return instance


@pytest.fixture
def ech_instance_gr(
    ech_instance, instance_with_case, caluma_workflow_config_gr, utils, group
):
    ech_instance = instance_with_case(ech_instance)
    ech_instance.case.meta["dossier-number"] = "2020-1"

    municipality = ech_instance.instance_services.first().service
    municipality.name = "Testgemeinde"
    municipality.save()
    utils.add_answer(
        ech_instance.case.document,
        "gemeinde",
        str(municipality.pk),
    )
    ech_instance.case.document.dynamicoption_set.update(slug=str(municipality.pk))
    DynamicOption.objects.create(
        document=ech_instance.case.document,
        question_id="gemeinde",
        slug=str(municipality.pk),
        label=municipality.name,
    )

    utils.add_answer(
        ech_instance.case.document, "beschreibung-bauvorhaben", "Testvorhaben"
    )
    utils.add_table_answer(
        ech_instance.case.document,
        "parzelle",
        [
            {
                "parzellennummer": "1586",
                "e-grid-nr": "123",
            }
        ],
    )
    utils.add_answer(
        ech_instance.case.document, "street-and-housenumber", "Teststrasse 12a"
    )
    utils.add_answer(ech_instance.case.document, "ort-grundstueck", "Chur")
    utils.add_answer(ech_instance.case.document, "plz", "1234")
    utils.add_table_answer(
        ech_instance.case.document,
        "personalien-gesuchstellerin",
        [
            {
                "vorname-gesuchstellerin": "Testvorname",
                "name-gesuchstellerin": "Testname",
                "ort-gesuchstellerin": "Testort ",
                "plz-gesuchstellerin": 1234,
                "strasse-gesuchstellerin": "Teststrasse",
                "juristische-person-gesuchstellerin": "Nein",
                "telefon-oder-mobile-gesuchstellerin": int("0311234567"),
                "e-mail-gesuchstellerin": "a@b.ch",
            }
        ],
    )
    document = DocumentFactory(
        pk="f8380740-d73a-4683-8909-2ced929ddbc5",
        metainfo={"camac-instance-id": ech_instance.pk},
        title="Situationsplan",
        category__name="Beilagen zum Gesuch",
        category__metainfo={"access": {group.role.name: {"visibility": "all"}}},
    )
    document.tags.set([TagFactory(), TagFactory()])
    FileFactory(
        pk="57a93396-454c-4a55-b48f-d114ad264df9",
        variant="original",
        name="Situationsplan.pdf",
        document=document,
    )

    return ech_instance


@pytest.fixture
def ech_instance_be(ech_instance, instance_with_case, caluma_workflow_config_be, utils):
    ech_instance = instance_with_case(ech_instance)
    ech_instance.case.meta["ebau-number"] = "2020-1"

    municipality = ech_instance.instance_services.first().service
    municipality.name = "Testgemeinde"
    municipality.save()
    utils.add_answer(
        ech_instance.case.document,
        "gemeinde",
        str(municipality.pk),
    )
    ech_instance.case.document.dynamicoption_set.update(slug=str(municipality.pk))
    DynamicOption.objects.create(
        document=ech_instance.case.document,
        question_id="gemeinde",
        slug=str(municipality.pk),
        label=municipality.name,
    )

    utils.add_answer(
        ech_instance.case.document, "beschreibung-bauvorhaben", "Testvorhaben"
    )
    utils.add_table_answer(
        ech_instance.case.document,
        "parzelle",
        [
            {
                "parzellennummer": "1586",
                "lagekoordinaten-nord": 1070000.0001,  # too many decimal places
                "lagekoordinaten-ost": 2480000.0,
            }
        ],
    )
    utils.add_answer(ech_instance.case.document, "strasse-flurname", "Teststrasse")
    utils.add_answer(ech_instance.case.document, "nr", "23b")
    utils.add_table_answer(
        ech_instance.case.document,
        "personalien-gesuchstellerin",
        [
            {
                "vorname-gesuchstellerin": "Testvorname",
                "name-gesuchstellerin": "Testname",
                "ort-gesuchstellerin": "Testort",
                "plz-gesuchstellerin": 1234,
                "strasse-gesuchstellerin": "Teststrasse",
                "juristische-person-gesuchstellerin": "Nein",
                "telefon-oder-mobile-gesuchstellerin": int("0311234567"),
                "e-mail-gesuchstellerin": "a@b.ch",
            }
        ],
    )
    utils.add_table_answer(
        ech_instance.case.document,
        "beschreibung-der-prozessart-tabelle",
        [
            {
                "prozessart": {
                    "value": "felssturz",
                    "options": ["felssturz", "fliesslawine"],
                }
            }
        ],
    )
    utils.add_answer(ech_instance.case.document, "gwr-egid", "1738778")
    utils.add_answer(
        ech_instance.case.document,
        "nutzungsart",
        ["wohnen"],
        options=["wohnen", "landwirtschaft"],
    )
    utils.add_answer(ech_instance.case.document, "sammelschutzraum", "Ja")
    utils.add_answer(ech_instance.case.document, "baukosten-in-chf", 42)
    utils.add_answer(ech_instance.case.document, "nutzungszone", "Testnutzungszone")
    utils.add_answer(ech_instance.case.document, "effektive-geschosszahl", 2)
    return ech_instance


@pytest.fixture
def ech_instance_case(ech_instance_be, caluma_admin_user):
    def wrapper(is_vorabklaerung=False):
        workflow_slug = (
            "preliminary-clarification" if is_vorabklaerung else "building-permit"
        )

        case = workflow_api.start_case(
            workflow=caluma_workflow_models.Workflow.objects.get(pk=workflow_slug),
            form=caluma_form_models.Form.objects.get(slug="main-form"),
            user=caluma_admin_user,
            meta={
                "submit-date": ech_instance_be.creation_date.strftime(
                    SUBMIT_DATE_FORMAT
                ),
                "paper-submit-date": ech_instance_be.creation_date.strftime(
                    SUBMIT_DATE_FORMAT
                ),
            },
        )

        ech_instance_be.case = case
        ech_instance_be.save()

        return case

    return wrapper


@pytest.fixture
def ech_snapshot(snapshot):
    def wrapper(raw_xml):
        pretty_xml = minidom.parseString(
            etree.tostring(
                etree.fromstring(raw_xml),
                method="c14n",  # c14n forces attributes to be sorted
            )
        ).toprettyxml()

        for search, replace in [
            (
                r"(<ns\d+:dossierIdentification>).+(</ns\d+:dossierIdentification>)",
                r"\1<!-- INSTANCE_ID -->\2",
            ),
            (
                r"(<ns\d+:organisationId>).+(</ns\d+:organisationId>)",
                r"\1<!-- ORGANISATION_ID -->\2",
            ),
            (
                r"(<ns\d+:messageId>).+(</ns\d+:messageId>)",
                r"\1<!-- MESSAGE_ID -->\2",
            ),
            (
                r"(<ns\d+:productVersion>).+(</ns\d+:productVersion>)",
                r"\1<!-- VERSION -->\2",
            ),
            (
                r"(<ns\d+:pathFileName>)(.*attachments=|.+files/)[\w-]+(</ns\d+:pathFileName>)",
                r"\1\2<!-- ATTACHMENT_ID -->\3",
            ),
        ]:
            pretty_xml = re.sub(
                search,
                replace,
                pretty_xml,
            )

        return snapshot.assert_match(pretty_xml)

    return wrapper


@pytest.fixture
def mocked_request_object(admin_user, group, caluma_admin_user):
    Request = namedtuple(
        "Request", ["user", "group", "caluma_info", "query_params", "META", "COOKIES"]
    )
    CalumaInfo = namedtuple("CalumaInfo", "context")
    Context = namedtuple("Context", "user")
    request = Request(
        user=admin_user,
        group=group,
        caluma_info=CalumaInfo(Context(caluma_admin_user)),
        query_params={},
        META={},
        COOKIES={},
    )
    return request


@pytest.fixture
def ech_instance_so(
    ech_instance,
    instance_with_case,
    caluma_workflow_config_so,
    utils,
    decision_factory_so,
    work_item_factory,
    group,
    service_factory,
    so_alexandria_settings,
):
    ech_instance = instance_with_case(ech_instance)
    ech_instance.case.meta["dossier-number"] = "2106-2024-1"

    municipality = ech_instance.instance_services.first().service
    municipality.name = "Testgemeinde"
    municipality.save()
    utils.add_answer(
        ech_instance.case.document,
        "gemeinde",
        str(municipality.pk),
    )
    DynamicOption.objects.create(
        document=ech_instance.case.document,
        question_id="gemeinde",
        slug=str(municipality.pk),
        label=municipality.name,
    )

    utils.add_answer(
        ech_instance.case.document, "umschreibung-bauprojekt", "Testvorhaben"
    )
    utils.add_table_answer(
        ech_instance.case.document,
        "parzelle",
        [{"parzellennummer": "1586", "e-grid": "CH123456789"}],
    )
    utils.add_answer(ech_instance.case.document, "strasse-flurname", "Musterstrasse")
    utils.add_answer(ech_instance.case.document, "strasse-nummer", 4)
    utils.add_answer(ech_instance.case.document, "ort", "Solothurn")
    utils.add_answer(ech_instance.case.document, "plz", "4500")

    utils.add_answer(ech_instance.case.document, "dauer-in-monaten", 15)
    utils.add_answer(ech_instance.case.document, "geplanter-baustart", date(2025, 1, 1))
    utils.add_answer(ech_instance.case.document, "gesamtkosten", 12_000_000)

    utils.add_table_answer(
        ech_instance.case.document, "bauherrin", [so_personal_row_factory()]
    )
    utils.add_table_answer(
        ech_instance.case.document, "grundeigentuemerin", [so_personal_row_factory()]
    )
    utils.add_table_answer(
        ech_instance.case.document,
        "tiefbauten",
        [{"tiefbau-siedlung-art": "tiefbau-siedlung-art-parkplaetze"}],
    )

    document = DocumentFactory(
        pk="f8380740-d73a-4683-8909-2ced929ddbc5",
        metainfo={"camac-instance-id": ech_instance.pk},
        title="Situationsplan",
        category__name="Beilagen zum Gesuch",
        category__metainfo={"access": {group.role.name: {"visibility": "all"}}},
    )
    document.tags.set(
        [
            # Should be visible
            TagFactory(name="My tag", created_by_group=str(group.service_id)),
            # Should not be visible
            TagFactory(name="Other tag", created_by_group=str(service_factory().pk)),
        ]
    )
    FileFactory(
        pk="57a93396-454c-4a55-b48f-d114ad264df9",
        variant="original",
        name="Situationsplan.pdf",
        document=document,
    )

    # Document that should not be visible
    DocumentFactory(
        pk="7cacebba-8bf1-44bc-8654-0d0d4e925ee1",
        metainfo={"camac-instance-id": ech_instance.pk},
        title="Internes Dokument",
        category__name="Intern",
        category__metainfo={},
    )

    work_item_factory(task_id="decision", case=ech_instance.case, status="completed")
    decision_factory_so(ech_instance)

    return ech_instance


@pytest.fixture
def fake_request(rf, admin_user, group):
    request = rf.request()
    request.user = admin_user
    request.group = group

    return request
