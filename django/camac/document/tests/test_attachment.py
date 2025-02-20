import io
import json
from datetime import timedelta

import pytest
from caluma.caluma_workflow.models import WorkItem
from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from pytest_lazy_fixtures import lf, lfc
from rest_framework import status

from camac.document import models, permissions, serializers
from camac.permissions.conditions import Always
from camac.utils import build_url

from .data import django_file


def _configure_geometer_access(
    att, instance_acl_factory, service, permissions_settings, access_level
):
    """Configure documents access for Geometer users.

    Geometer users use the new permissions system, so we need a few things
    for their access to work:

    * Setup an appropriate access level
    * Grant access to the instance via an ACL
    * Mark the attachment as "for_geometer" in the context
    """
    att.context["for_geometer"] = True
    att.save()
    instance_acl_factory(
        instance=att.instance,
        grant_type="SERVICE",
        service=service,
        access_level=access_level,
    )
    permissions_settings["ACCESS_LEVELS"] = {
        access_level.pk: [
            ("documents-read", Always()),
        ]
    }


@pytest.mark.parametrize(
    "role__name,instance__user,num_queries",
    [
        ("Applicant", lf("admin_user"), 14),
        ("Reader", lf("user"), 14),
        ("Canton", lf("user"), 14),
        ("Municipality", lf("user"), 14),
        ("Service", lf("user"), 14),
        ("Geometer", lf("user"), 16),
    ],
)
@pytest.mark.parametrize(
    "attachment__path,is_docx",
    [
        ("attachments/files/852/example.jpg", False),
        ("attachments/files/852/important.docx", True),
    ],
)
@pytest.mark.parametrize(
    "mode",
    [
        permissions.AdminPermission,
        permissions.ReadPermission,
        permissions.ReadInternalPermission,
        permissions.AdminInternalPermission,
    ],
)
def test_attachment_list(
    admin_client,
    attachment_attachment_sections,
    num_queries,
    activation,
    django_assert_num_queries,
    instance_acl_factory,
    permissions_settings,
    service,
    access_level,
    role,
    mocker,
    is_docx,
    mode,
):
    url = reverse("attachment-list")

    if role.name == "Geometer":
        _configure_geometer_access(
            att=attachment_attachment_sections.attachment,
            instance_acl_factory=instance_acl_factory,
            service=service,
            permissions_settings=permissions_settings,
            access_level=access_level,
        )

    # fix permissions
    mocker.patch(
        "camac.document.permissions.PERMISSIONS",
        {
            "test": {
                role.name.lower(): {
                    mode: [attachment_attachment_sections.attachmentsection_id]
                }
            }
        },
    )

    included = serializers.AttachmentSerializer.included_serializers
    with django_assert_num_queries(num_queries):
        response = admin_client.get(
            url,
            data={
                "name": attachment_attachment_sections.attachment.name.split(".")[0],
                "include": ",".join(included.keys()),
            },
        )
    assert response.status_code == status.HTTP_200_OK

    json = response.json()
    data = json["data"]
    assert len(data) == 1
    assert data[0]["id"] == str(attachment_attachment_sections.attachment.pk)
    can_write = role.name not in ("Applicant", "Reader", "Geometer") and (
        mode == permissions.AdminPermission
        or (
            mode == permissions.AdminInternalPermission
            and attachment_attachment_sections.attachment.service
            == admin_client.user.get_default_group().service
        )
    )
    assert (data[0]["attributes"]["webdav-link"] is not None) == (can_write and is_docx)


@pytest.mark.parametrize("role__name,instance__user", [("Applicant", lf("admin_user"))])
@pytest.mark.parametrize(
    "filter,attachment__context,expect_count",
    [
        ({"key": "isDecision", "value": True}, {"isDecision": True}, 1),
        ({"key": "isDecision", "value": True}, {}, 0),
        (
            {"key": "foobar", "value": "blah", "lookup": "STARTSWITH"},
            {"foobar": "hello blah"},
            0,
        ),
        (
            {"key": "foobar", "value": "blah", "lookup": "CONTAINS"},
            {"foobar": "hello blah"},
            1,
        ),
        (
            [
                {"key": "foobar", "value": "blah", "lookup": "CONTAINS"},
                {"key": "isDecision", "value": True},
            ],
            {"foobar": "hello blah"},
            0,
        ),
        (
            [
                {"key": "foobar", "value": "blah", "lookup": "CONTAINS"},
                {"key": "isDecision", "value": True},
            ],
            {"foobar": "hello blah", "isDecision": True},
            1,
        ),
    ],
)
def test_attachment_context_filter(
    admin_client, attachment_attachment_sections, filter, expect_count, mocker
):
    # fix permissions
    mocker.patch(
        "camac.document.permissions.PERMISSIONS",
        {
            "test": {
                "applicant": {
                    permissions.AdminPermission: [
                        attachment_attachment_sections.attachmentsection_id
                    ]
                }
            }
        },
    )

    url = reverse("attachment-list")
    response = admin_client.get(url, data={"context": json.dumps(filter)})
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert len(data["data"]) == expect_count


@pytest.mark.parametrize("instance_state__name", ["nfd"])
@pytest.mark.parametrize(
    "filename,mime_type,role__name,instance__user,instance__location,activation__service,instance__group,instance__instance_state,case_status,acl_mode,status_code",
    [
        # applicant creates valid pdf attachment on a instance of its own in a
        # attachment section with admin permissions
        (
            "multiple-pages.pdf",  # filename
            "application/pdf",  # mime_type
            "Applicant",  # role__name
            lf("admin_user"),  # instance__user
            lf("location"),  # instance__location
            lf("service"),  # activation__service
            lf("group"),  # instance__group
            lf("instance_state"),  # instance__instance_state
            None,  # instance__case__status
            permissions.AdminPermission,  # mode
            status.HTTP_201_CREATED,  # status_code
        ),
        # user with role Municipality creates valid jpg attachment on an
        # instance of its location in a attachment section with admin
        # permissions
        (
            "test-thumbnail.jpg",
            "image/jpeg",
            "Municipality",
            lf("user"),
            lf("location"),
            lf("service"),
            lf("group"),
            lf("instance_state"),
            None,
            permissions.AdminPermission,
            status.HTTP_201_CREATED,
        ),
        # user with role Service creates valid jpg attachment on an
        # instance which is assigned to user in an activation in
        # an attachment section with admin permissions
        (
            "test-thumbnail.jpg",
            "image/jpeg",
            "Service",
            lf("user"),
            lfc("location_factory"),
            lf("service"),
            lf("group"),
            lf("instance_state"),
            None,
            permissions.AdminPermission,
            status.HTTP_201_CREATED,
        ),
        # user with role Service creates valid jpg attachment on an
        # instance which is assigned to user in an activation in
        # an attachment section with internal admin permissions
        (
            "test-thumbnail.jpg",
            "image/jpeg",
            "Service",
            lf("user"),
            lfc("location_factory"),
            lf("service"),
            lf("group"),
            lf("instance_state"),
            None,
            permissions.AdminInternalPermission,
            status.HTTP_201_CREATED,
        ),
        # user with role Service creates valid jpg attachment on an
        # instance which is assigned to user in an activation in
        # an attachment section with service admin permissions
        (
            "test-thumbnail.jpg",
            "image/jpeg",
            "Service",
            lf("user"),
            lfc("location_factory"),
            lf("service"),
            lf("group"),
            lf("instance_state"),
            None,
            permissions.AdminServicePermission,
            status.HTTP_201_CREATED,
        ),
        # user with role Canton creates valid jpg attachment on any
        # instance with attachment section with admin permissions
        (
            "test-thumbnail.jpg",
            "image/jpeg",
            "Canton",
            lf("user"),
            lfc("location_factory"),
            lf("service"),
            lf("group"),
            lf("instance_state"),
            None,
            permissions.AdminPermission,
            status.HTTP_201_CREATED,
        ),
        # user with role Applicant tries to create invalid gif attachment
        # on its own instance with attachment section with admin permissions
        (
            "invalid-attachment.gif",
            "image/gif",
            "Applicant",
            lf("admin_user"),
            lf("location"),
            lf("service"),
            lf("group"),
            lf("instance_state"),
            None,
            permissions.AdminPermission,
            status.HTTP_400_BAD_REQUEST,
        ),
        # user with role Applicant tries to create valid pdf attachment
        # on instance which doesn't belong to him with attachment section
        # with admin permissions
        (
            "multiple-pages.pdf",
            "application/pdf",
            "Applicant",
            lf("user"),
            lf("location"),
            lf("service"),
            lf("group"),
            lf("instance_state"),
            None,
            permissions.AdminPermission,
            status.HTTP_400_BAD_REQUEST,
        ),
        # user with role Applicant tries to create valid jpg attachment
        # on its own instance with attachment section with only read rights
        (
            "test-thumbnail.jpg",
            "image/jpeg",
            "Applicant",
            lf("admin_user"),
            lf("location"),
            lf("service"),
            lf("group"),
            lf("instance_state"),
            None,
            permissions.ReadPermission,
            status.HTTP_400_BAD_REQUEST,
        ),
        # user with role Municipality tries to create valid jpg attachment
        # on instance of a different location with attachment section
        # with admin rights
        (
            "test-thumbnail.jpg",
            "image/jpeg",
            "Municipality",
            lf("user"),
            lfc("location_factory"),
            lfc("service_factory"),
            lfc("group_factory"),
            lf("instance_state"),
            None,
            permissions.AdminPermission,
            status.HTTP_400_BAD_REQUEST,
        ),
        # user with role Municipality tries to create valid jpg attachment
        # on instance of a different location but with circulation activation
        # on attachment section with admin rights
        (
            "test-thumbnail.jpg",
            "image/jpeg",
            "Municipality",
            lf("user"),
            lfc("location_factory"),
            lf("service"),
            lf("group"),
            lf("instance_state"),
            None,
            permissions.AdminPermission,
            status.HTTP_201_CREATED,
        ),
        # user with role Municipality creates valid jpg attachment on an
        # instance of its location in a attachment section with admin
        # permissions but in a group user doesn't belong to
        (
            "test-thumbnail.jpg",
            "image/jpeg",
            "Municipality",
            lf("user"),
            lf("location"),
            lf("service"),
            lfc("group_factory"),
            lf("instance_state"),
            None,
            permissions.AdminPermission,
            status.HTTP_400_BAD_REQUEST,
        ),
        # user with role Service tries to create valid jpg attachment
        # on instance without activation with attachment section
        # with admin rights
        (
            "test-thumbnail.jpg",
            "image/jpeg",
            "Service",
            lf("admin_user"),
            lf("location"),
            lfc("service_factory"),
            lfc("group_factory"),
            lf("instance_state"),
            None,
            permissions.AdminPermission,
            status.HTTP_400_BAD_REQUEST,
        ),
        # reader can't create anything, not even with admin permissions
        (
            "test-thumbnail.jpg",
            "image/jpeg",
            "Reader",
            lf("admin_user"),
            lf("location"),
            lf("service"),
            lf("group"),
            lf("instance_state"),
            None,
            permissions.AdminPermission,
            status.HTTP_400_BAD_REQUEST,
        ),
        # support can upload attachments
        (
            "multiple-pages.pdf",
            "application/pdf",
            "Support",
            lf("admin_user"),
            lf("location"),
            lf("service"),
            lf("group"),
            lf("instance_state"),
            None,
            permissions.AdminPermission,
            status.HTTP_201_CREATED,
        ),
        # municipality can upload attachments to internal
        # business control instances
        (
            "multiple-pages.pdf",
            "application/pdf",
            "Municipality",
            lf("admin_user"),
            lf("location"),
            lf("service"),
            lf("group"),
            # In theory, this could be shortened to lfc("instance_state_factory", name="internal")
            # However, this doesn't work as the "name" argument is already being
            # used by pytest-lazy-fixtures internals
            lfc(
                lambda instance_state_factory: instance_state_factory(name="internal"),
                lf("instance_state_factory"),
            ),
            "running",
            permissions.AdminInternalBusinessControlPermission,
            status.HTTP_201_CREATED,
        ),
        # service can upload attachments to internal business
        #  control instances
        (
            "test-thumbnail.jpg",
            "image/jpeg",
            "Service",
            lf("admin_user"),
            lf("location"),
            lf("service"),
            lf("group"),
            lfc(
                lambda instance_state_factory: instance_state_factory(name="internal"),
                lf("instance_state_factory"),
            ),
            "running",
            permissions.AdminInternalBusinessControlPermission,
            status.HTTP_201_CREATED,
        ),
        # attachments can only uploaded to internal business control
        # instances in state "internal"
        (
            "multiple-pages.pdf",
            "image/jpeg",
            "Service",
            lf("admin_user"),
            lf("location"),
            lf("service"),
            lf("group"),
            lfc(
                lambda instance_state_factory: instance_state_factory(name="subm"),
                lf("instance_state_factory"),
            ),
            "running",
            permissions.AdminInternalBusinessControlPermission,
            status.HTTP_400_BAD_REQUEST,
        ),
        # attachments cannot be uploaded, if the target business control
        # instance case isn't running
        (
            "multiple-pages.pdf",
            "image/jpeg",
            "Service",
            lf("admin_user"),
            lf("location"),
            lf("service"),
            lf("group"),
            lfc(
                lambda instance_state_factory: instance_state_factory(name="internal"),
                lf("instance_state_factory"),
            ),
            "completed",
            permissions.AdminInternalBusinessControlPermission,
            status.HTTP_400_BAD_REQUEST,
        ),
        # applicant attempts to upload a too large file
        (
            "1MB.pdf",
            "application/pdf",
            "Applicant",
            lf("admin_user"),
            lf("location"),
            lf("service"),
            lf("group"),
            lf("instance_state"),
            None,
            permissions.AdminPermission,
            status.HTTP_400_BAD_REQUEST,
        ),
    ],
)
def test_attachment_create(
    admin_client,
    sz_instance,
    attachment_section,
    activation,
    mime_type,
    filename,
    case_status,
    status_code,
    mailoutbox,
    acl_mode,
    role,
    mocker,
    case_factory,
    application_settings,
):
    url = reverse("attachment-list")

    # fix permissions
    mocker.patch(
        "camac.document.permissions.PERMISSIONS",
        {"test": {role.name.lower(): {acl_mode: [attachment_section.pk]}}},
    )

    application_settings["ATTACHMENT_INTERNAL_STATES"] = ["internal"]
    application_settings["ATTACHMENT_MAX_SIZE"] = 0.9 * 1024 * 1024
    if case_status:
        sz_instance.case = case_factory(status=case_status)
        sz_instance.save()

    path = django_file(filename)
    data = {
        "instance": sz_instance.pk,
        "path": path.file,
        "group": sz_instance.group.pk,
    }
    response = admin_client.post(url, data=data, format="multipart")
    assert response.status_code == status_code

    if status_code == status.HTTP_201_CREATED:
        json = response.json()
        attributes = json["data"]["attributes"]
        assert attributes["size"] == path.size
        assert attributes["name"] == filename
        assert attributes["mime-type"] == mime_type
        relationships = json["data"]["relationships"]
        assert relationships["attachment-sections"]["data"][0]["id"] == str(
            attachment_section.pk
        )
        assert relationships["group"]["data"]["id"] == str(sz_instance.group.pk)

        # download uploaded attachment
        response = admin_client.get(attributes["path"])
        assert response.status_code == status.HTTP_200_OK
        assert response["Content-Disposition"] == (
            'attachment; filename="{0}"'.format(filename)
        )
        assert response["Content-Type"].startswith(mime_type)
        assert response["X-Accel-Redirect"] == "/attachments/files/%s/%s" % (
            sz_instance.pk,
            filename,
        )

        assert len(mailoutbox) == 1


def test_attachment_download_404(admin_client, attachment):
    url = reverse("attachment-download", args=[attachment.path])
    response = admin_client.get(url)
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.parametrize(
    "role__name,instance__user,instance_state__name,acl_mode",
    [("Applicant", lf("admin_user"), "new", permissions.AdminPermission)],
)
@pytest.mark.parametrize("multi", [False, True])
@pytest.mark.parametrize("document", ["multiple-pages.pdf", "important.docx"])
def test_attachment_download(
    admin_client,
    service,
    instance,
    attachment_factory,
    attachment_section,
    acl_mode,
    document,
    multi,
    mocker,
):
    if multi:
        expected_name = "attachments.zip"
    else:
        expected_name = document
    attachment1 = attachment_factory(
        instance=instance, service=service, path=django_file(document)
    )

    # fix permissions
    mocker.patch(
        "camac.document.permissions.PERMISSIONS",
        {"test": {"applicant": {acl_mode: [attachment_section.pk]}}},
    )

    test_path = "/".join(str(attachment1.path).split("/")[3:])
    attachment1.path = test_path
    attachment1.path.name = test_path
    attachment1.name = document
    attachment1.save()
    attachments = [attachment1]

    attachment2 = attachment_factory(
        instance=instance, service=service, path=django_file(document)
    )
    attachment2.path = test_path
    attachment2.path.name = test_path
    attachment2.name = document
    attachment2.save()

    if multi:
        attachments.append(attachment2)

    attachment_section.attachments.set(attachments)

    filter = ",".join([str(a.pk) for a in attachments])

    url = f"{reverse('multi-attachment-download')}?attachments={filter}"
    response = admin_client.get(url)
    assert response.status_code == status.HTTP_200_OK
    assert (
        response.headers["content-disposition"]
        == f'attachment; filename="{expected_name}"'
    )


@pytest.mark.parametrize(
    "filter,status_code",
    [
        ("?attachments=", status.HTTP_400_BAD_REQUEST),
        ("?attachments=,", status.HTTP_400_BAD_REQUEST),
        ("?attachments=777", status.HTTP_404_NOT_FOUND),
        ("?attachments=somestring", status.HTTP_400_BAD_REQUEST),
        ("", status.HTTP_400_BAD_REQUEST),
    ],
)
def test_invalid_attachment_download(admin_client, filter, status_code):
    url = f"{reverse('multi-attachment-download')}{filter}"
    response = admin_client.get(url)
    assert response.status_code == status_code


@pytest.mark.parametrize("thumbnail_size", [("x300"), ("x500")])
@pytest.mark.parametrize("role__name,instance__user", [("Applicant", lf("admin_user"))])
@pytest.mark.parametrize(
    "attachment__path,status_code",
    [
        (django_file("multiple-pages.pdf"), status.HTTP_200_OK),
        (django_file("test-thumbnail.jpg"), status.HTTP_200_OK),
        (django_file("no-thumbnail.txt"), status.HTTP_404_NOT_FOUND),
    ],
)
def test_attachment_thumbnail(
    admin_client,
    attachment_attachment_sections,
    attachment_attachment_section_factory,
    status_code,
    mocker,
    thumbnail_size,
    application_settings,
):
    application_settings["THUMBNAIL_SIZE"] = thumbnail_size

    aasa = attachment_attachment_sections.attachment
    attachment_attachment_section_factory(attachment=aasa)
    url = reverse("attachment-thumbnail", args=[aasa.pk])

    # fix permissions
    mocker.patch(
        "camac.document.permissions.PERMISSIONS",
        {
            "test": {
                "applicant": {
                    permissions.AdminPermission: [
                        section.pk for section in models.AttachmentSection.objects.all()
                    ]
                }
            }
        },
    )

    response = admin_client.get(url)
    assert response.status_code == status_code
    if status_code == status.HTTP_200_OK:
        assert response["Content-Type"] == "image/jpeg"
        image = Image.open(io.BytesIO(response.content))
        assert image.height == int(thumbnail_size.replace("x", ""))


@pytest.mark.parametrize(
    "role__name,instance__user,attachment_section__allowed_mime_types",
    [("Canton", lf("admin_user"), [])],
)
@pytest.mark.parametrize(
    "send_path,status_code",
    [(True, status.HTTP_400_BAD_REQUEST), (False, status.HTTP_200_OK)],
)
def test_attachment_update(
    admin_client,
    attachment_section,
    attachment_attachment_sections,
    attachment_attachment_section_factory,
    status_code,
    send_path,
    mocker,
):
    aasa = attachment_attachment_sections.attachment
    attachment_attachment_section_factory(attachment=aasa)
    url = reverse("attachment-detail", args=[aasa.pk])
    # fix permissions
    mocker.patch(
        "camac.document.permissions.PERMISSIONS",
        {
            "test": {
                "canton": {
                    permissions.AdminPermission: [
                        section.pk for section in models.AttachmentSection.objects.all()
                    ]
                }
            }
        },
    )

    format = ""
    data = {
        "data": {
            "type": "attachments",
            "id": aasa.pk,
            "relationships": {
                "attachment-sections": {
                    "data": [
                        {"type": "attachment-sections", "id": attachment_section.pk}
                    ]
                }
            },
        }
    }
    if send_path:
        format = "multipart"
        data = {"path": aasa.path}

    response = admin_client.patch(url, data=data, format=format)
    assert response.status_code == status_code


@pytest.mark.parametrize("instance_state__name", [("finished")])
@pytest.mark.parametrize(
    "attachment__context,new_context,permission,status_code,is_active_service,role__name,attachment_section__allowed_mime_types",
    [
        # attachment is in no writable section: ok
        (
            {"foo": "bar"},
            None,
            permissions.ReadPermission,
            status.HTTP_200_OK,
            False,
            "Canton",
            [],
        ),
        # change context, but not active service: forbidden
        (
            {"foo": "bar"},
            {"asdf": "xyz"},
            permissions.AdminPermission,
            status.HTTP_403_FORBIDDEN,
            False,
            "Canton",
            [],
        ),
        # change context as active service: ok
        (
            {"foo": "bar"},
            {"asdf": "xyz"},
            permissions.AdminPermission,
            status.HTTP_200_OK,
            True,
            "Canton",
            [],
        ),
        # no change (field not filled): ok
        (
            {"foo": "bar"},
            None,
            permissions.AdminPermission,
            status.HTTP_200_OK,
            False,
            "Canton",
            [],
        ),
        # no change (field filled but with same value): ok
        (
            {"foo": "bar"},
            {"foo": "bar"},
            permissions.AdminPermission,
            status.HTTP_200_OK,
            False,
            "Canton",
            [],
        ),
        # change of isDecision after instance's decision has been enacted
        (
            {"isDecision": True},
            {"isDecision": False},
            permissions.WritePermission,
            status.HTTP_400_BAD_REQUEST,
            True,
            "Canton",
            [],
        ),
        # change of isDecision after instance's decision has been enacted with support role
        (
            {"isDecision": False, "isPublished": False},
            {"isDecision": True, "isPublished": True},
            permissions.WritePermission,
            status.HTTP_200_OK,
            False,
            "Support",
            [],
        ),
        # change of document name for attachment in internal attachment section, not active service
        (
            {"displayName": "testdocument.pdf"},
            {"displayName": "testdocument_1.pdf"},
            permissions.AdminInternalPermission,
            status.HTTP_200_OK,
            False,
            "Canton",
            [],
        ),
    ],
)
def test_attachment_update_context(
    admin_client,
    admin_user,
    application_settings,
    instance_state,
    attachment_section,
    attachment_attachment_sections,
    group_factory,
    status_code,
    is_active_service,
    new_context,
    mocker,
    permission,
):
    aasa = attachment_attachment_sections.attachment
    url = reverse("attachment-detail", args=[aasa.pk])

    if "displayName" in aasa.context.keys():
        application_settings["ATTACHMENT_SECTION_INTERNAL"] = (
            aasa.attachment_sections.first().attachment_section_id
        )
    else:
        application_settings["ATTACHMENT_SECTION_INTERNAL"] = None

    finished_state_name = "finished"
    application_settings["ATTACHMENT_AFTER_DECISION_STATES"] = [finished_state_name]

    aasa.instance.instance_state = instance_state

    mocker.patch(
        "camac.instance.models.Instance.responsible_service",
        return_value=(
            admin_user.groups.first().service
            if is_active_service
            else group_factory().service
        ),
    )

    aasa.instance.save()

    # fix permissions
    mocker.patch(
        "camac.document.permissions.PERMISSIONS",
        {
            "test": {
                admin_user.groups.first().role.name.lower(): {
                    permission: [attachment_section.pk]
                }
            }
        },
    )

    data = {
        "data": {
            "type": "attachments",
            "id": aasa.pk,
            "relationships": {
                "attachment-sections": {
                    "data": [
                        {"type": "attachment-sections", "id": attachment_section.pk}
                    ]
                }
            },
        }
    }
    if new_context:
        data["data"]["attributes"] = {"context": new_context}

    response = admin_client.patch(url, data=data)
    assert response.status_code == status_code


@pytest.mark.parametrize(
    "role__name,instance__user",
    [("Applicant", lf("admin_user")), ("Reader", lf("admin_user"))],
)
def test_attachment_detail(
    admin_client,
    attachment_attachment_sections,
    role,
    mocker,
):
    url = reverse(
        "attachment-detail", args=[attachment_attachment_sections.attachment.pk]
    )
    # fix permissions
    mocker.patch(
        "camac.document.permissions.PERMISSIONS",
        {
            "test": {
                role.name.lower(): {
                    permissions.AdminPermission: [
                        attachment_attachment_sections.attachmentsection_id
                    ]
                }
            }
        },
    )

    response = admin_client.get(url)
    assert response.status_code == status.HTTP_200_OK


@pytest.mark.parametrize("role__name,instance__user", [("Applicant", lf("admin_user"))])
def test_attachment_loosen_filter(
    admin_client, role, mocker, instance, attachment_section, attachment
):
    url = reverse("attachment-list")

    # permissons: Our user has no permission
    mocker.patch(
        "camac.document.permissions.PERMISSIONS",
        {"test": {role.name.lower(): {permissions.AdminPermission: []}}},
    )

    # First test in here: attachment was not marked and thus not visible
    response = admin_client.get(url)
    assert response.status_code == status.HTTP_200_OK
    json = response.json()
    assert response.status_code == status.HTTP_200_OK
    assert len(json["data"]) == 0

    # Now, mark the attachment as decision
    attachment.context["isDecision"] = True
    attachment.save()

    # After marking the attachment, it should be visible to the applicant
    response = admin_client.get(url)
    assert response.status_code == status.HTTP_200_OK
    json = response.json()
    assert response.status_code == status.HTTP_200_OK
    assert len(json["data"]) == 1
    assert json["data"][0]["id"] == str(attachment.pk)


@pytest.mark.parametrize(
    "instance_state__name,role__name,instance__user,instance__location,activation__service,instance__group,acl_mode",
    [
        (
            "nfd",
            "Applicant",  # role__name
            lf("admin_user"),  # instance__user
            lf("location"),  # instance__location
            lf("service"),  # activation__service
            lf("group"),  # instance__group
            permissions.AdminPermission,  # mode
        )
    ],
)
@pytest.mark.parametrize(
    "attachment_section__allowed_mime_types,filename,status_code",
    [
        ([], "invalid-attachment.gif", status.HTTP_201_CREATED),
        (["application/pdf"], "invalid-attachment.gif", status.HTTP_400_BAD_REQUEST),
        (
            ["application/pdf", "image/jpeg"],
            "test-thumbnail.jpg",
            status.HTTP_201_CREATED,
        ),
    ],
)
def test_attachment_mime_type(
    admin_client,
    sz_instance,
    attachment_section,
    activation,
    filename,
    status_code,
    acl_mode,
    role,
    mocker,
):
    url = reverse("attachment-list")

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

    path = django_file(filename)
    data = {
        "instance": sz_instance.pk,
        "path": path.file,
        "group": sz_instance.group.pk,
    }
    response = admin_client.post(url, data=data, format="multipart")
    assert response.status_code == status_code


@pytest.mark.parametrize("role__name,instance__user", [("Applicant", lf("admin_user"))])
def test_attachment_section_filters(
    admin_client, role, mocker, instance, attachment_section_factory, attachment_factory
):
    url = reverse("attachment-list")

    section_visible_1 = attachment_section_factory(name="visible_1")
    section_visible_2 = attachment_section_factory(name="visible_2")
    section_forbidden = attachment_section_factory(name="forbidden")

    docs = attachment_factory.create_batch(3, instance=instance)
    for doc, section in zip(
        docs, [section_visible_1, section_visible_2, section_forbidden]
    ):
        doc.attachment_sections.add(section)

    # Verify assumptions
    assert models.AttachmentSection.objects.count() == 3
    assert models.Attachment.objects.count() == 3

    # permissons: visible sections are visible
    mocker.patch(
        "camac.document.permissions.PERMISSIONS",
        {
            "test": {
                role.name.lower(): {
                    permissions.AdminPermission: [
                        section_visible_1.pk,
                        section_visible_2.pk,
                    ]
                }
            }
        },
    )

    # First test in here: first, no filtering. should return two documents,
    # one for each visible section.
    response = admin_client.get(url)
    assert response.status_code == status.HTTP_200_OK
    json = response.json()
    assert response.status_code == status.HTTP_200_OK
    assert len(json["data"]) == 2
    assert set([docs[0].pk, docs[1].pk]) == set(
        int(result["id"]) for result in json["data"]
    )

    # Second test: include filter. Include the forbidden seciton,
    # but its corresponding document should not be returned
    response = admin_client.get(
        url,
        data={
            "attachment_sections": ",".join(
                [str(section_visible_2.pk), str(section_forbidden.pk)]
            )
        },
    )
    assert response.status_code == status.HTTP_200_OK
    json = response.json()
    assert response.status_code == status.HTTP_200_OK
    assert len(json["data"]) == 1
    assert json["data"][0]["id"] == str(docs[1].pk)

    # Third test: exclude filter. exclude a visible seciton.
    # Expect the other visible section's doument, but not the
    # forbidden section's document
    response = admin_client.get(url, data={"exclude_sections": [section_visible_2.pk]})
    assert response.status_code == status.HTTP_200_OK
    json = response.json()
    assert response.status_code == status.HTTP_200_OK
    assert len(json["data"]) == 1
    assert json["data"][0]["id"] == str(docs[0].pk)

    # Final test: exclude filter. exclude both visible secitons.
    # Expecting zero results
    response = admin_client.get(
        url,
        data={
            "exclude_sections": ",".join(
                [str(section_visible_2.pk), str(section_visible_1.pk)]
            )
        },
    )
    assert response.status_code == status.HTTP_200_OK
    json = response.json()
    assert response.status_code == status.HTTP_200_OK
    assert len(json["data"]) == 0


@pytest.mark.freeze_time("2021-03-09")
def test_attachment_public_access(
    db,
    client,
    instance_factory,
    attachment_attachment_section_factory,
    publication_entry_factory,
    publication_settings,
    settings,
):
    """Test unauthenticated, public access to publicated attachments."""
    pub_instance = publication_entry_factory(
        publication_date=timezone.now() - timedelta(days=1),
        publication_end_date=timezone.now() + timedelta(days=10),
        is_published=True,
    ).instance

    aas, aas2, aas3, *_ = attachment_attachment_section_factory.create_batch(
        5, attachment__instance=pub_instance
    )
    aasa = aas.attachment
    aasa.context = {"isPublished": True}
    aasa.save()

    url = reverse("attachment-list")

    # nothing is visible without publication backend
    settings.APPLICATION_NAME = "kt_uri"
    publication_settings["BACKEND"] = None
    res = client.get(url, HTTP_X_CAMAC_PUBLIC_ACCESS=True)
    assert res.status_code == status.HTTP_200_OK
    assert len(res.json()["data"]) == 0

    publication_settings["BACKEND"] = "camac-ng"

    # published attachments are visible
    res = client.get(url, HTTP_X_CAMAC_PUBLIC_ACCESS=True)
    assert res.status_code == status.HTTP_200_OK
    assert len(res.json()["data"]) == 1
    data = res.json()["data"][0]
    assert data["id"] == str(aasa.pk)

    # Kt. SZ doesn't have public access to documents
    settings.APPLICATION_NAME = "kt_schwyz"
    res = client.get(url, HTTP_X_CAMAC_PUBLIC_ACCESS=True)
    assert res.status_code == status.HTTP_401_UNAUTHORIZED

    settings.APPLICATION_NAME = "kt_uri"

    url = reverse("attachment-detail", args=[aasa.pk])
    res = client.get(url, HTTP_X_CAMAC_PUBLIC_ACCESS=True)
    assert res.status_code == status.HTTP_200_OK

    res = client.delete(url, HTTP_X_CAMAC_PUBLIC_ACCESS=True)
    assert res.status_code == status.HTTP_401_UNAUTHORIZED

    data["attributes"]["name"] = "some other value"
    res = client.patch(url, data, HTTP_X_CAMAC_PUBLIC_ACCESS=True)
    assert res.status_code == status.HTTP_401_UNAUTHORIZED

    url = reverse("attachment-download", args=[aasa.path])
    response = client.get(url, HTTP_X_CAMAC_PUBLIC_ACCESS=True)
    assert response.status_code == status.HTTP_200_OK

    assert aasa.download_history.count() == 1
    assert not aasa.download_history.first().user

    aas2.attachment.context = {"isPublished": True}
    aas2.attachment.save()
    url = reverse("attachment-list")
    res = client.get(url, HTTP_X_CAMAC_PUBLIC_ACCESS=True)
    assert res.status_code == status.HTTP_200_OK
    data = res.json()["data"]
    assert len(data) == 2
    assert set(d["id"] for d in data) == set([str(aasa.pk), str(aas2.attachment_id)])

    res = client.get(
        url,
        {"attachment_sections": aas2.attachmentsection.pk},
        HTTP_X_CAMAC_PUBLIC_ACCESS=True,
    )
    assert res.status_code == status.HTTP_200_OK
    data = res.json()["data"]
    assert len(data) == 1
    assert data[0]["id"] == str(aas2.attachment_id)

    # other endpoints eg. attachment sections are still forbidden
    url = reverse("attachmentsection-list")
    response = client.get(url, HTTP_X_CAMAC_PUBLIC_ACCESS=True)
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.parametrize("role__name", ["Municipality"])
@pytest.mark.parametrize(
    "instance_state__name,attachment__path,attachment__service,acl_mode,has_running_inquiry,status_code",
    [
        (
            "circulation",
            django_file("no-thumbnail.txt"),
            lfc("service_factory"),
            permissions.AdminBeforeDecisionPermission,
            False,
            status.HTTP_204_NO_CONTENT,
        ),
        (
            "finished",
            django_file("no-thumbnail.txt"),
            lfc("service_factory"),
            permissions.AdminBeforeDecisionPermission,
            False,
            status.HTTP_403_FORBIDDEN,
        ),
        (
            "finished",
            django_file("no-thumbnail.txt"),
            lfc("service_factory"),
            permissions.AdminServiceBeforeDecisionPermission,
            False,
            status.HTTP_403_FORBIDDEN,
        ),
        (
            "finished",
            django_file("no-thumbnail.txt"),
            lf("service"),
            permissions.AdminServiceBeforeDecisionPermission,
            False,
            status.HTTP_403_FORBIDDEN,
        ),
        (
            "finished",
            django_file("no-thumbnail.txt"),
            lf("service"),
            permissions.AdminServiceBeforeDecisionPermission,
            False,
            status.HTTP_403_FORBIDDEN,
        ),
        (
            "circulation",
            django_file("no-thumbnail.txt"),
            lf("service"),
            permissions.AdminServiceRunningInquiryPermission,
            True,
            status.HTTP_204_NO_CONTENT,
        ),
        (
            "circulation",
            django_file("no-thumbnail.txt"),
            lf("service"),
            permissions.AdminServiceRunningInquiryPermission,
            False,
            status.HTTP_403_FORBIDDEN,
        ),
    ],
)
def test_attachment_delete_custom_admin_modes(
    db,
    acl_mode,
    active_inquiry_factory,
    admin_client,
    application_settings,
    attachment_attachment_sections,
    be_distribution_settings,
    be_instance,
    has_running_inquiry,
    mocker,
    status_code,
    use_instance_service,
):
    application_settings["ATTACHMENT_AFTER_DECISION_STATES"] = ["finished"]

    if has_running_inquiry:
        active_inquiry_factory(status=WorkItem.STATUS_READY)

    url = reverse(
        "attachment-detail", args=[attachment_attachment_sections.attachment.pk]
    )
    # fix permissions
    mocker.patch(
        "camac.document.permissions.PERMISSIONS",
        {
            "test": {
                "municipality": {
                    acl_mode: [
                        section.pk for section in models.AttachmentSection.objects.all()
                    ]
                }
            }
        },
    )

    response = admin_client.delete(url)
    assert response.status_code == status_code


@pytest.mark.parametrize("role__name", ["Municipality"])
@pytest.mark.parametrize(
    "instance_state__name,attachment__service,status_code,error,mime_type",
    [
        (
            "circulation",
            lfc("service_factory"),
            status.HTTP_400_BAD_REQUEST,
            "Nicht ausreichend Berechtigungen um eine Datei in den Ordner 'new' hochzuladen.",
            [],
        ),
        (
            "circulation",
            lf("service"),
            status.HTTP_400_BAD_REQUEST,
            "Der aktuelle Dateityp kann nicht als Dokument hochgeladen werden. Erlaubte Dateitypen für Abschnitt existing sind: pdf",
            ["application/pdf"],
        ),
        (
            "finished",
            lf("service"),
            status.HTTP_400_BAD_REQUEST,
            "Nicht ausreichend Berechtigungen um eine Datei aus dem Ordner 'delete' zu löschen.",
            [],
        ),
        ("circulation", lf("service"), status.HTTP_200_OK, None, []),
    ],
)
def test_attachment_update_section(
    db,
    admin_client,
    application_settings,
    mocker,
    instance_state,
    attachment,
    attachment_section_factory,
    attachment_attachment_section_factory,
    status_code,
    error,
    mime_type,
):
    application_settings["ATTACHMENT_AFTER_DECISION_STATES"] = ["finished"]

    section_existing = attachment_section_factory(
        name="existing", allowed_mime_types=mime_type
    )
    section_new = attachment_section_factory(name="new", allowed_mime_types=mime_type)
    section_delete = attachment_section_factory(
        name="delete", allowed_mime_types=mime_type
    )

    attachment_attachment_section_factory(
        attachment=attachment, attachmentsection=section_existing
    )
    attachment_attachment_section_factory(
        attachment=attachment, attachmentsection=section_delete
    )

    url = reverse("attachment-detail", args=[attachment.pk])
    # fix permissions
    mocker.patch(
        "camac.document.permissions.PERMISSIONS",
        {
            "test": {
                "municipality": {
                    permissions.AdminPermission: [section_existing.pk],
                    permissions.AdminInternalPermission: [section_new.pk],
                    permissions.AdminBeforeDecisionPermission: [section_delete.pk],
                }
            }
        },
    )

    data = {
        "data": {
            "type": "attachments",
            "id": attachment.pk,
            "relationships": {
                "attachment-sections": {
                    "data": [
                        {"type": "attachment-sections", "id": section_existing.pk},
                        {"type": "attachment-sections", "id": section_new.pk},
                    ]
                }
            },
        }
    }

    response = admin_client.patch(url, data=data)
    assert response.status_code == status_code

    if status_code == status.HTTP_400_BAD_REQUEST:
        assert response.json()["errors"][0]["detail"] == error


@pytest.mark.parametrize("role__name", ["Municipality"])
def test_attachment_delete_multiple_sections(
    db,
    admin_client,
    attachment_attachment_section_factory,
    attachment,
    mocker,
):
    sections = [
        aas.attachmentsection.pk
        for aas in attachment_attachment_section_factory.create_batch(
            2, attachment=attachment
        )
    ]

    # fix permissions
    mocker.patch(
        "camac.document.permissions.PERMISSIONS",
        {"test": {"municipality": {permissions.AdminPermission: sections}}},
    )

    url = reverse("attachment-detail", args=[attachment.pk])

    response = admin_client.delete(url)
    assert response.status_code == status.HTTP_204_NO_CONTENT


@pytest.mark.parametrize("role__name", ["Service"])
@pytest.mark.parametrize(
    "inquiry_status,status_code",
    [
        (WorkItem.STATUS_READY, status.HTTP_200_OK),
        (WorkItem.STATUS_COMPLETED, status.HTTP_400_BAD_REQUEST),
    ],
)
def test_attachment_update_custom_permissions(
    db,
    active_inquiry_factory,
    admin_client,
    attachment_attachment_section_factory,
    attachment_section_factory,
    attachment,
    be_distribution_settings,
    be_instance,
    inquiry_status,
    mocker,
    status_code,
    use_instance_service,
):
    active_inquiry_factory(for_instance=be_instance, status=inquiry_status)

    existing_section = attachment_section_factory(
        name="existing", allowed_mime_types=[]
    )
    new_section = attachment_section_factory(name="new", allowed_mime_types=[])

    attachment_attachment_section_factory(
        attachment=attachment, attachmentsection=existing_section
    )

    url = reverse("attachment-detail", args=[attachment.pk])

    # fix permissions
    mocker.patch(
        "camac.document.permissions.PERMISSIONS",
        {
            "test": {
                "service": {
                    permissions.AdminPermission: [existing_section.pk],
                    permissions.AdminServiceRunningInquiryPermission: [new_section.pk],
                }
            }
        },
    )

    data = {
        "data": {
            "type": "attachments",
            "id": attachment.pk,
            "relationships": {
                "attachment-sections": {
                    "data": [
                        {"type": "attachment-sections", "id": existing_section.pk},
                        {"type": "attachment-sections", "id": new_section.pk},
                    ]
                }
            },
        }
    }

    response = admin_client.patch(url, data=data)
    assert response.status_code == status_code


@pytest.mark.parametrize(
    "role__name,instance__user,instance_state__name,acl_mode",
    [("Applicant", lf("admin_user"), "new", permissions.AdminPermission)],
)
def test_convert_docx_to_word(
    admin_client,
    service,
    instance,
    attachment_factory,
    attachment_section,
    acl_mode,
    mocker,
    requests_mock,
):
    # fix permissions
    mocker.patch(
        "camac.document.permissions.PERMISSIONS",
        {"test": {"applicant": {acl_mode: [attachment_section.pk]}}},
    )

    attachments = []

    for filename in ["important.docx", "important.pdf"]:
        attachment = attachment_factory(
            instance=instance, service=service, path=django_file(filename)
        )

        test_path = "/".join(str(attachment.path).split("/")[3:])

        attachment.path = test_path
        attachment.path.name = test_path
        attachment.name = filename
        attachment.save()
        attachments.append(attachment)

    attachment_section.attachments.set(attachments)

    url = reverse("attachment-convert", args=[attachments[0].pk])

    requests_mock.register_uri(
        "POST",
        build_url(settings.DOCUMENT_MERGE_SERVICE_URL, "/convert"),
        content=b"A pdf",
    )

    response = admin_client.post(url)

    assert response.status_code == status.HTTP_201_CREATED
