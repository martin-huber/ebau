import pytest
from caluma.caluma_workflow.models import WorkItem
from django.urls import reverse
from pytest_lazy_fixtures import lf, lfc
from rest_framework import status

from camac.document import permissions
from camac.document.tests.data import django_file


@pytest.mark.parametrize("role__name", [("Applicant")])
def test_attachment_section_list(
    admin_user,
    admin_client,
    attachment_section_factory,
    role,
    mocker,
):
    # valid case: attachment section allowed by role acl
    attachment_section_role = attachment_section_factory(sort=1)

    # invalid case: attachment section without acl
    attachment_section_factory()

    # fix permissions
    mocker.patch(
        "camac.document.permissions.PERMISSIONS",
        {
            "test": {
                role.name.lower(): {
                    permissions.AdminPermission: [attachment_section_role.pk]
                }
            }
        },
    )

    url = reverse("attachmentsection-list")

    response = admin_client.get(url)
    assert response.status_code == status.HTTP_200_OK

    json = response.json()
    assert len(json["data"]) == 1
    assert json["data"][0]["id"] == str(attachment_section_role.pk)
    assert json["data"][0]["meta"]["permission-name"] == "admin"


@pytest.mark.parametrize("role__name", [("Applicant")])
def test_attachment_section_detail(admin_client, attachment_section, role, mocker):
    url = reverse("attachmentsection-detail", args=[attachment_section.pk])
    # fix permissions
    mocker.patch(
        "camac.document.permissions.PERMISSIONS",
        {
            "test": {
                role.name.lower(): {
                    permissions.AdminPermission: [attachment_section.pk]
                }
            }
        },
    )

    response = admin_client.get(url)
    assert response.status_code == status.HTTP_200_OK


@pytest.mark.parametrize(
    "role__name,group_key,section_id,expected",
    [
        (
            "trusted_service",
            "SACHBEARBEITUNG_AFJ",
            12000008,
            {12000008: permissions.AdminServicePermission},
        ),
        (
            "trusted_service",
            "SACHBEARBEITUNG_UND_KOORDINATION_AFJ",
            12000008,
            {12000008: permissions.AdminServicePermission},
        ),
        (
            "coordination",
            "KOOR_AFJ",
            12000008,
            {
                12000008: permissions.AdminServicePermission,
                12000007: permissions.AdminServicePermission,
            },
        ),
        (
            "coordination",
            "KOOR_AFU",
            12000007,
            {
                12000007: permissions.AdminServicePermission,
            },
        ),
        (
            "coordination",
            "KOOR_BD",
            12000007,
            {
                12000007: permissions.AdminServicePermission,
            },
        ),
        (
            "trusted_service",
            "LISAG",
            12000007,
            {12000007: permissions.AdminServicePermission},
        ),
        (
            "trusted_service",
            "LISAG",
            123,
            {
                123: permissions.AdminServicePermission,
                12000007: permissions.AdminServicePermission,
            },
        ),
        (
            "coordination",
            "KOOR_NP",
            123,
            {
                123: permissions.AdminServicePermission,
                12000007: permissions.AdminServicePermission,
                12000003: permissions.AdminServicePermission,
            },
        ),
        (
            "trusted_service",
            None,
            123,
            {123: permissions.AdminServicePermission},
        ),
        (
            "coordination",
            "KOOR_BG",
            12000003,
            {12000003: permissions.AdminServicePermission},
        ),
        (
            "trusted_service",
            "ARE",
            12000010,
            {
                12000010: permissions.AdminServicePermission,
            },
        ),
        (
            "trusted_service",
            "AFU",
            12000011,
            {
                12000011: permissions.AdminServicePermission,
            },
        ),
    ],
)
def test_attachment_section_special_permissions_ur(
    db,
    mocker,
    role,
    group_factory,
    set_application_ur,
    settings,
    section_id,
    expected,
    group_key,
):
    settings.APPLICATION_NAME = "kt_uri"
    # set some static group ID explicitly to avoid random collisions with
    # configured IDs
    group = group_factory(pk=99999, role=role)

    if group_key:
        mocker.patch(f"camac.constants.kt_uri.{group_key}_GROUP_ID", group.pk)
        if group_key == "ARE":
            mocker.patch("camac.constants.kt_uri.DOCUMENTS_ARE_GROUPS", [group.pk])
        if group_key == "AFU":
            mocker.patch("camac.constants.kt_uri.DOCUMENTS_AFU_GROUPS", [group.pk])

    mocker.patch(
        "camac.document.permissions.PERMISSIONS",
        {
            "kt_uri": {
                role.name.lower(): {permissions.AdminServicePermission: [section_id]}
            }
        },
    )

    assert permissions.section_permissions(group) == expected


@pytest.mark.parametrize(
    "data",
    [
        {
            "trusted_service": {
                permissions.ReadPermission: [1, 2, 3],
                permissions.AdminServicePermission: [23, 33],
                permissions.AdminInternalBusinessControlPermission: (
                    permissions._is_internal_instance,
                    [4],
                ),
                permissions.AdminPermission: (permissions._is_general_instance, [5]),
            }
        }
    ],
)
def test_rebuild_app_permissions(
    db, group, instance, data, application_settings, instance_state_factory
):
    application_settings["ATTACHMENT_INTERNAL_STATES"] = ["internal"]

    instance.instance_state = instance_state_factory(name="internal")
    instance.save()

    assert permissions.rebuild_app_permissions(data, group, instance) == {
        "trusted_service": {
            1: permissions.ReadPermission,
            2: permissions.ReadPermission,
            3: permissions.ReadPermission,
            23: permissions.AdminServicePermission,
            33: permissions.AdminServicePermission,
            4: permissions.AdminInternalBusinessControlPermission,
        }
    }


@pytest.mark.parametrize("role__name", ["municipality-lead"])
@pytest.mark.parametrize(
    "is_involved,expected_permission", [(True, "admin"), (False, "read")]
)
def test_attachment_section_permissions_kt_bern(
    db,
    mocker,
    admin_client,
    instance,
    instance_service_factory,
    group,
    attachment_section,
    is_involved,
    expected_permission,
    use_instance_service,
):
    if is_involved:
        instance_service_factory(instance=instance, service=group.service)

    mocker.patch(
        "camac.document.permissions.PERMISSIONS",
        {
            "test": {
                "municipality-lead": {
                    permissions.AdminPermission: [attachment_section.pk]
                },
                "service-lead": {permissions.ReadPermission: [attachment_section.pk]},
            }
        },
    )

    url = reverse("attachmentsection-detail", args=[attachment_section.pk])
    response = admin_client.get(url, {"instance": instance.pk})

    assert response.json()["data"]["meta"]["permission-name"] == expected_permission


@pytest.mark.parametrize("role__name", ["municipality-lead"])
def test_attachment_modification_by_activation_involvement(
    db,
    settings,
    application_settings,
    be_instance,
    mocker,
    active_inquiry_factory,
    be_distribution_settings,
    admin_client,
    attachment_section,
    instance_service_factory,
    group_factory,
    role,
    group,
):
    """
    Ensure that the backend api respects has_running_activation permission.

    domain: kt_bern

    Make an instance belonging to the lead group with role municipality-lead (e. g. RSTA) and
    a service that is also municipality-lead (e. g. Leitbehörde) that is then involved via activation.

    Let the group add some attachment to the attachment_section for involved groups. After answering the
    activation and termination of the respective circulation all write and delete permissions should be
    effectively revoked from that attachment_section.
    """

    settings.APPLICATION = settings.APPLICATIONS["kt_bern"]
    settings.APPLICATION_NAME = "kt_bern"
    application_settings["ATTACHMENT_AFTER_DECISION_STATES"] = ["finished"]

    # make lead group responsible and ensure subordinate group is not involved
    # as instance_service:
    group.service.instance_set.clear()
    lead_group = group_factory(role=role, name="Lead group")
    instance_service_factory(instance=be_instance, service=lead_group.service)

    # activate subordinate group by inquiry
    inquiry = active_inquiry_factory(
        for_instance=be_instance,
        addressed_service=group.service,
        status=WorkItem.STATUS_READY,
    )

    path = django_file("multiple-pages.pdf")
    create_data = {
        "instance": be_instance.pk,
        "path": path.file,
        "group": group.pk,
        "attachment_sections": attachment_section.pk,
    }

    # fix permissions
    mocker.patch(
        "camac.document.permissions.PERMISSIONS",
        {
            "kt_bern": {
                "municipality-lead": {
                    permissions.ReadPermission: [
                        attachment_section.pk
                    ]  # disallow the regular lead permission to ensure the problematic fallback does not kick in
                },
                "service-lead": {
                    permissions.AdminServiceRunningInquiryPermission: [
                        attachment_section.pk
                    ]
                },
            }
        },
    )
    create_res = admin_client.post(
        reverse("attachment-list"), data=create_data, format="multipart"
    )
    # creation of attachment should be allowed for group.service by activation involvement
    assert create_res.status_code == status.HTTP_201_CREATED

    # finish the current circulation to revoke group's involvement
    inquiry.status = WorkItem.STATUS_COMPLETED
    inquiry.save()

    # ensure that deletion of the attachment fails
    del_resp = admin_client.delete(
        f'{reverse("attachment-detail", args=[create_res.json()["data"]["id"]])}?instance={be_instance.pk}'
    )
    assert del_resp.status_code == status.HTTP_403_FORBIDDEN

    # ensure that creation of another attachment fails, too
    create_res = admin_client.post(
        reverse("attachment-list"), data=create_data, format="multipart"
    )
    assert create_res.status_code == status.HTTP_400_BAD_REQUEST


_admin_service = lfc(lambda user: user.get_default_group().service, lf("admin_user"))
_other_service = lfc("service_factory")


@pytest.mark.parametrize(
    "instance_state__name, attachment__service, expect_can_write, expect_can_destroy",
    [
        # before decision: nothing allowed at all
        ["new", _admin_service, False, False],
        ["done", _admin_service, False, False],
        ["old", _admin_service, False, False],
        # after decision: destroy own (but not others)
        ["rejected", _admin_service, False, False],
        ["rejected", _other_service, False, False],
        ["correction", _admin_service, False, False],
        ["sb1", _admin_service, True, True],
        ["sb1", _other_service, True, False],
        ["sb2", _admin_service, False, False],
        ["conclusion", _admin_service, False, False],
        ["finished", _admin_service, False, False],
        ["finished_internal", _admin_service, False, False],
        ["evaluated", _admin_service, False, False],
    ],
)
def test_read_during_sb1(
    db,
    attachment,
    be_instance,
    set_application_be,
    admin_user,
    expect_can_write,
    expect_can_destroy,
):
    group = admin_user.get_default_group()

    can_write = permissions.ReadWriteDuringSB1.can_write(attachment, group, be_instance)

    can_destroy = permissions.ReadWriteDuringSB1.can_destroy(attachment, group)

    assert can_write == expect_can_write
    assert can_destroy == expect_can_destroy
