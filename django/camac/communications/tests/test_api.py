"""
Basic checking of API behaviour.

Detailed checks for permissions / visibilities are done in
the corresponding test modules.
"""

import io
import json
import os

import pytest
from django.urls import reverse
from rest_framework import status

from camac.document.tests.data import django_file


@pytest.mark.parametrize(
    "role__name, expect_status",
    [
        ("Municipality", status.HTTP_201_CREATED),
        ("Applicant", status.HTTP_201_CREATED),
    ],
)
def test_create_topic(db, be_instance, admin_client, expect_status, role):
    if role.name == "Applicant":
        be_instance.involved_applicants.create(
            invitee=admin_client.user, user=admin_client.user
        )
        default_group = admin_client.user.get_default_group()
        default_group.service = None
        default_group.save()

    resp = admin_client.post(
        reverse("communications-topic-list"),
        {
            "data": {
                "type": "communications-topics",
                "id": None,
                "attributes": {
                    "subject": "bar",
                    "involved-entities": [],
                    # intentionally using a wrong entity, to see if
                    # serializer properly overwrites it
                    "initiated-by-entity": {"id": "someone"},
                },
                "relationships": {
                    "instance": {
                        "data": {"id": str(be_instance.pk), "type": "instances"}
                    },
                },
            }
        },
    )

    assert resp.status_code == expect_status

    # Check that initiator is added to involved as well as set as
    # initiator
    data = resp.json()
    assert data["data"]["relationships"]["initiated-by"] == {
        "data": {
            "type": "users",
            "id": str(admin_client.user.pk),
        }
    }

    if role.name == "Applicant":
        entity_id = {"id": "APPLICANT", "name": "Gesuchsteller/in"}
    else:
        entity_id = {
            "id": str(admin_client.user.get_default_group().service.pk),
            "name": admin_client.user.get_default_group().service.get_name(),
        }
    assert data["data"]["attributes"]["involved-entities"] == [entity_id]
    assert data["data"]["attributes"]["initiated-by-entity"] == entity_id


@pytest.mark.parametrize("role__name", ["Municipality"])
@pytest.mark.parametrize("with_file_attachments", [True, False])
@pytest.mark.parametrize("with_doc_attachments", [True, False])
def test_create_message(
    db,
    be_instance,
    admin_client,
    topic_with_admin_involved,
    tmpdir,
    with_doc_attachments,
    with_file_attachments,
    attachment_factory,
    notification_template,
    communications_settings,
):
    communications_settings["NOTIFICATIONS"]["APPLICANT"]["template_slug"] = (
        notification_template.slug
    )
    communications_settings["NOTIFICATIONS"]["INTERNAL_INVOLVED_ENTITIES"][
        "template_slug"
    ] = notification_template.slug
    communications_settings["ALLOWED_MIME_TYPES"] = ["text/plain"]

    attachments = []
    if with_file_attachments:
        for x in range(2):
            file = tmpdir / f"file_{x}.txt"
            file.open("w").write(f"hello {x}")
            attachments.append(file.open("r"))
    if with_doc_attachments:
        for x in range(2):
            attachments.append(
                json.dumps({"id": str(attachment_factory().pk), "type": "attachments"})
            )

    resp = admin_client.post(
        reverse("communications-message-list"),
        data={
            "body": "hello world",
            "topic": json.dumps(
                {
                    "id": str(topic_with_admin_involved.pk),
                    "type": "communications-topics",
                }
            ),
            "attachments": attachments,
        },
        format="multipart",
    )
    assert resp.status_code == status.HTTP_201_CREATED

    new_message = topic_with_admin_involved.messages.get(pk=resp.json()["data"]["id"])
    assert new_message.attachments.count() == len(attachments)
    for attachment in new_message.attachments.all():
        if attachment.file_attachment:
            assert attachment.file_attachment.read()
        else:
            assert attachment.document_attachment


@pytest.mark.parametrize("role__name", ["Municipality", "Applicant"])
@pytest.mark.parametrize(
    "has_document, has_file, expect_status",
    [
        [False, False, status.HTTP_404_NOT_FOUND],
        [False, True, status.HTTP_200_OK],
        [True, False, status.HTTP_200_OK],
        [True, True, status.HTTP_200_OK],
    ],
)
def test_attachment_download(
    db,
    be_instance,
    role,
    admin_client,
    communications_message,
    communications_attachment,
    attachment_factory,
    has_document,
    has_file,
    expect_status,
):
    expected_file_content = None
    communications_message.topic.involved_entities = [
        admin_client.user.get_default_group().service_id,
        "APPLICANT",
    ]
    communications_message.topic.save()

    if role.name == "Applicant":
        be_instance.involved_applicants.create(
            invitee=admin_client.user, user=admin_client.user
        )

    if has_file:
        communications_attachment.file_attachment.save(
            "foo.txt", io.BytesIO(b"asdfasdf")
        )
        expected_file_content = communications_attachment.file_attachment.read()
    else:
        communications_attachment.file_attachment = None

    if has_document:
        communications_attachment.document_attachment = attachment_factory()
        expected_file_content = (
            communications_attachment.document_attachment.path.read()
        )
    else:
        communications_attachment.document_attachment = None

    communications_attachment.save()

    get_response = admin_client.get(
        reverse("communications-attachment-detail", args=[communications_attachment.pk])
    )

    url = get_response.json()["data"]["attributes"]["download-url"]

    resp = admin_client.get(url)

    assert resp.status_code == expect_status
    if expect_status == status.HTTP_200_OK:
        assert os.path.exists(resp.headers["X-Sendfile"])
        with open(resp.headers["X-Sendfile"], "rb") as fh_download:
            assert fh_download.read() == expected_file_content


@pytest.mark.parametrize("role__name", ["Municipality"])
def test_included_dossier_number(
    db,
    be_instance,
    admin_client,
    communications_topic,
):
    be_instance.case.meta["ebau-number"] = "2022-1299"
    be_instance.case.save()
    communications_topic.involved_entities = [
        admin_client.user.get_default_group().service_id,
        "APPLICANT",
    ]
    communications_topic.save()

    resp = admin_client.get(reverse("communications-topic-list"))

    assert be_instance.case.meta["ebau-number"]

    assert (
        resp.json()["data"][0]["attributes"]["dossier-number"]
        == be_instance.case.meta["ebau-number"]
    )


@pytest.mark.parametrize("role__name", ["Municipality", "Applicant"])
@pytest.mark.parametrize("notifications_enabled", [1, 0])
def test_notification_email(
    db,
    admin_client,
    communications_topic,
    be_instance,
    admin_user,
    mailoutbox,
    notification_template,
    communications_settings,
    service_factory,
    role,
    notifications_enabled,
):
    communications_settings["NOTIFICATIONS"]["APPLICANT"]["template_slug"] = (
        notification_template.slug
    )
    communications_settings["NOTIFICATIONS"]["INTERNAL_INVOLVED_ENTITIES"][
        "template_slug"
    ] = notification_template.slug

    other_service = service_factory(notification=notifications_enabled)
    communications_topic.involved_entities = [
        admin_user.get_default_group().service_id,
        other_service.pk,
        "APPLICANT",
    ]
    communications_topic.save()

    if role.name == "Applicant":
        be_instance.involved_applicants.update(invitee=admin_user)
        default_group = admin_client.user.get_default_group()
        default_group.service = None
        default_group.save()

    resp = admin_client.post(
        reverse("communications-message-list"),
        data={
            "body": "hello world",
            "topic": json.dumps(
                {
                    "id": str(communications_topic.pk),
                    "type": "communications-topics",
                }
            ),
            "attachments": [],
        },
        format="multipart",
    )
    assert resp.status_code == status.HTTP_201_CREATED
    if notifications_enabled:
        assert len(mailoutbox) == 2
        recipient_emails = [email.recipients()[0] for email in mailoutbox]
        assert other_service.email in recipient_emails
        assert notification_template.subject in mailoutbox[0].subject
        assert notification_template.subject in mailoutbox[1].subject
    else:
        assert len(mailoutbox) == 1
        recipient_emails = [email.recipients()[0] for email in mailoutbox]
        assert other_service.email not in recipient_emails
        assert notification_template.subject in mailoutbox[0].subject


@pytest.mark.parametrize("role__name", ["Municipality"])
@pytest.mark.parametrize("error_type", ["extension", "content", "unallowed"])
def test_mime_type_validation(
    db,
    admin_client,
    topic_with_admin_involved,
    tmpdir,
    communications_settings,
    mocker,
    error_type,
):
    mocker.patch("camac.notification.utils.send_mail")

    communications_settings["ALLOWED_MIME_TYPES"] = ["text/plain"]
    file = django_file("no-thumbnail.txt")

    if error_type == "unallowed":
        communications_settings["ALLOWED_MIME_TYPES"] = ["application/pdf"]
    elif error_type == "extension":
        file.name = "test.pdf"
    elif error_type == "content":
        file = django_file("test-thumbnail.jpg")

    response = admin_client.post(
        reverse("communications-message-list"),
        data={
            "body": "hello world",
            "topic": json.dumps(
                {
                    "id": str(topic_with_admin_involved.pk),
                    "type": "communications-topics",
                }
            ),
            "attachments": [file],
        },
        format="multipart",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
