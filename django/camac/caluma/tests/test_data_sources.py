from collections import namedtuple

import pytest
from caluma.caluma_form import models as caluma_form_models
from caluma.caluma_form.factories import QuestionFactory
from django.core.cache import cache

from camac.tests.data import so_personal_row_factory

from ..extensions.countries import COUNTRIES
from ..extensions.data_sources import (
    Attachments,
    Authorities,
    Buildings,
    Countries,
    Landowners,
    Locations,
    Mitberichtsverfahren,
    Municipalities,
    PreliminaryClarificationTargets,
    Services,
    ServicesForFinalReport,
)


@pytest.mark.parametrize(
    "role,expected_count", [("Portal User", 1), ("Some internal role", 2)]
)
def test_locations(db, role, location_factory, expected_count):
    User = namedtuple("OIDCUser", "camac_role")
    user = User(camac_role=role)

    location_factory(name="Foo", zip=123)
    location_factory(name="Foo", zip=None)

    data = Locations().get_data(user, None, None)
    assert len(data) == expected_count


@pytest.mark.parametrize(
    "role,expected_count",
    [("Koordinationsstelle Baugesuche BG", 4), ("Something else", 0)],
)
def test_mitberichtsverfahren(db, role, location_factory, expected_count):
    User = namedtuple("OIDCUser", "camac_role")
    user = User(camac_role=role)

    location_factory(name="Foo", zip=123)
    location_factory(name="Foo", zip=None)

    data = Mitberichtsverfahren().get_data(user, None, None)
    assert len(data) == expected_count


@pytest.mark.parametrize(
    "test_class,expected,is_rsta",
    [
        (Authorities, [[1, "Baukommission Altdorf"]], False),
        (Municipalities, [[1, {"de": "Bern", "fr": "Berne", "it": "Bern"}]], False),
        (
            Municipalities,
            [
                [
                    2,
                    {
                        "de": "Biel (nicht aktiviert)",
                        "fr": "Bienne (non activé)",
                        "it": "Biel (nicht aktiviert)",
                    },
                ]
            ],
            True,
        ),
        (
            Services,
            [
                ["-1", {"de": "Andere", "fr": "Autres", "it": "altri"}],
                [
                    "1",
                    {
                        "de": "Gemeinde Bern",
                        "fr": "Municipalité Berne",
                        "it": "Gemeinde Bern",
                    },
                ],
                ["3", {"de": "service3", "fr": "service3", "it": "service3"}],
                ["4", {"de": "service4", "fr": "service4", "it": "service4"}],
            ],
            False,
        ),
        (
            Countries,
            list(COUNTRIES.keys()),
            False,
        ),
    ],
)
def test_data_sources(
    db,
    multilang,
    service_factory,
    service_t_factory,
    service_group_factory,
    test_class,
    expected,
    is_rsta,
    authority_factory,
):
    if is_rsta:
        service1 = service_factory(
            pk=1,
            trans__name="service1",
            trans__language="de",
            disabled=False,
            service_group__name="district",
        )
    else:
        service1 = service_factory(
            pk=1,
            trans__name="Leitbehörde Bern",
            trans__language="de",
            disabled=False,
            service_group__name="municipality",
        )
        service_t_factory(
            service=service1, name="Autorité directrice Berne", language="fr"
        )
        authority_factory(pk=1, name="Baukommission Altdorf")

    service2 = service_factory(
        pk=2,
        trans__name="Leitbehörde Biel",
        trans__language="de",
        disabled=True,
        service_group__name="municipality",
    )
    service_t_factory(
        service=service2, name="Autorité directrice Bienne", language="fr"
    )

    service_factory(
        pk=3,
        trans__name="service3",
        trans__language="de",
        disabled=False,
        service_group__name="district",
    )

    service_factory(
        pk=4,
        trans__name="service4",
        trans__language="de",
        disabled=False,
        service_group__name="service",
    )

    User = namedtuple("OIDCUser", "group")
    user = User(group=service1.pk)

    data = test_class().get_data(user, None, None)

    assert data == expected


@pytest.mark.parametrize(
    "has_instance,has_attachment_section,expected_count",
    [(False, False, 0), (True, False, 0), (False, True, 0), (True, True, 3)],
)
def test_attachments(
    db,
    attachment_attachment_section_factory,
    attachment_section_factory,
    caluma_admin_user,
    expected_count,
    has_attachment_section,
    has_instance,
    instance_factory,
):
    question = QuestionFactory()

    section1 = attachment_section_factory()
    section2 = attachment_section_factory()

    instance1 = instance_factory()
    instance2 = instance_factory()

    # attachments in section 1
    attachment_attachment_section_factory.create_batch(
        3, attachmentsection=section1, attachment__instance=instance1
    )
    attachment_attachment_section_factory.create_batch(
        2, attachmentsection=section1, attachment__instance=instance2
    )

    # attachments in section 2
    attachment_attachment_section_factory.create_batch(
        1, attachmentsection=section2, attachment__instance=instance1
    )
    attachment_attachment_section_factory.create_batch(
        2, attachmentsection=section2, attachment__instance=instance2
    )

    if has_attachment_section:
        question.meta["attachmentSection"] = section1.pk
        question.save()

    if has_instance:
        context = {"instanceId": instance1.pk}
    else:
        context = {}

    cache.clear()

    data = Attachments().get_data(caluma_admin_user, question, context)

    assert len(data) == expected_count


def test_landowners_be(
    db,
    caluma_admin_user,
    be_instance,
    utils,
    be_master_data_settings,
    master_data_is_visible_mock,
):
    question = QuestionFactory(
        slug="personalien-grundeigentumerin",
        type=caluma_form_models.Question.TYPE_TABLE,
    )

    utils.add_table_answer(
        be_instance.case.document,
        question,
        [
            {
                "juristische-person-grundeigentuemerin": "juristische-person-grundeigentuemerin-nein",
                "vorname-grundeigentuemerin": "Foo",
                "name-grundeigentuemerin": "Bar",
            },
            {
                "juristische-person-grundeigentuemerin": "juristische-person-grundeigentuemerin-ja",
                "name-juristische-person-grundeigentuemerin": "Foobar AG",
            },
        ],
    )

    context = {"instanceId": be_instance.pk}
    data = Landowners().get_data(caluma_admin_user, question, context)

    names = [item[1] for item in data]

    assert len(data) == 2
    assert "Foobar AG" in names
    assert "Foo Bar" in names


def test_landowners_so(
    db,
    caluma_admin_user,
    so_instance,
    utils,
    so_master_data_settings,
    master_data_is_visible_mock,
    snapshot,
    settings,
):
    settings.APPLICATION_NAME = "kt_so"

    utils.add_table_answer(
        so_instance.case.document,
        "bauherrin",
        [so_personal_row_factory(True), so_personal_row_factory(False)],
    )
    utils.add_table_answer(
        so_instance.case.document,
        "grundeigentuemerin",
        [so_personal_row_factory(True), so_personal_row_factory(False)],
    )

    data = Landowners().get_data(
        caluma_admin_user, None, {"instanceId": so_instance.pk}
    )

    names = [item[1] for item in data]

    assert len(names) == 4
    assert names == snapshot


def test_municipalities_so(db, service_factory, service_t_factory):
    service = service_factory(service_group__name="municipality")
    service_t_factory(service=service, name="Gemeinde Solothurn")

    User = namedtuple("OIDCUser", "group")
    user = User(group=service.pk)

    data = Municipalities().get_data(user, None, None)

    assert len(data) == 1
    assert data[0][0] == service.pk
    assert data[0][1]["de"] == "Solothurn"


def test_preliminary_clarfication_targets(db, caluma_admin_user, service_factory):
    service_factory(
        trans__name="AfU",
        trans__language="de",
        service_group__name="service-cantonal",
    )
    service_factory(
        trans__name="Procap",
        trans__language="de",
        service_group__name="service-extra-cantonal",
    )
    service_factory(
        trans__name="ARP",
        trans__language="de",
        service_group__name="service-bab",
    )

    data = PreliminaryClarificationTargets().get_data(caluma_admin_user, None, None)

    assert data[0][1]["de"] == "Andere"
    assert data[1][1]["de"] == "Örtliche Baubehörde"
    assert data[2][1]["de"] == "AfU"
    assert data[3][1]["de"] == "ARP"
    assert data[4][1]["de"] == "Procap"


def test_buildings(db, caluma_admin_user, question_factory, so_instance, utils):
    question = question_factory(
        slug="gebaeude",
        type=caluma_form_models.Question.TYPE_TABLE,
    )

    utils.add_table_answer(
        so_instance.case.document,
        question,
        [
            {"gebaeude-bezeichnung": "MFH 1"},
            {"gebaeude-bezeichnung": "MFH 2"},
            {"gebaeude-bezeichnung": "EFH 1"},
        ],
    )

    data = Buildings().get_data(
        caluma_admin_user,
        question,
        {"instanceId": so_instance.pk},
    )

    names = set([item[1] for item in data])

    assert len(data) == 3
    assert names == {"MFH 1", "MFH 2", "EFH 1"}


def test_services_for_final_report(
    db,
    caluma_admin_user,
    question_factory,
    utils,
    ur_instance,
    work_item_factory,
    service_factory,
    ur_distribution_settings,
):
    services_that_wants_to_be_invited = service_factory()

    distribution = work_item_factory(
        task_id="distribution",
        case=ur_instance.case,
    )

    inquiry_1 = work_item_factory(
        task_id="inquiry",
        case=distribution.child_case,
        addressed_groups=[str(services_that_wants_to_be_invited.pk)],
    )

    utils.add_answer(
        inquiry_1.child_case.document,
        "inquiry-answer-invite-service",
        "inquiry-answer-invite-service-yes",
    )

    data = ServicesForFinalReport().get_data(
        caluma_admin_user,
        question_factory(),
        {"instanceId": ur_instance.pk},
    )

    service_names = set([service[1] for service in data])

    assert len(data) == 1
    assert service_names == {services_that_wants_to_be_invited.name}
