import re
from collections import namedtuple
from datetime import date, datetime, timedelta
from html import escape
from itertools import chain
from logging import getLogger

import inflection
import jinja2
from caluma.caluma_form import models as caluma_form_models
from caluma.caluma_workflow import models as caluma_workflow_models
from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.core.mail import EmailMessage, get_connection
from django.db.models import (
    Case,
    CharField,
    Exists,
    F,
    IntegerField,
    OuterRef,
    Q,
    Subquery,
    Sum,
    When,
)
from django.db.models.functions import Cast
from django.utils import timezone, translation
from django.utils.text import slugify
from jinja2.sandbox import SandboxedEnvironment
from rest_framework import exceptions
from rest_framework_json_api import serializers

from camac.billing.models import BillingV2Entry
from camac.caluma.api import CalumaApi
from camac.caluma.utils import find_answer, get_answer_display_value
from camac.communications.models import CommunicationsMessage
from camac.constants import kt_uri as uri_constants
from camac.core.models import (
    Activation,
    Circulation,
    HistoryActionConfig,
    WorkflowEntry,
)
from camac.core.utils import create_history_entry
from camac.instance.master_data import MasterData
from camac.instance.mixins import InstanceEditableMixin
from camac.instance.models import Instance
from camac.instance.placeholders import fields
from camac.instance.utils import (
    geometer_cadastral_survey_is_necessary,
    geometer_cadastral_survey_necessary_answer,
)
from camac.instance.validators import transform_coordinates
from camac.lookups import Any
from camac.permissions.models import InstanceACL
from camac.user.models import Group, Role, Service, User
from camac.user.utils import unpack_service_emails
from camac.utils import build_url, clean_join, flatten, get_responsible_koor_service_id

from ..core import models as core_models
from . import models

logger = getLogger(__name__)


RECIPIENT_TYPE_NAMES = {
    "applicant": translation.gettext_noop("Applicant"),
    "unregistered_applicant": translation.gettext_noop("Unregistered Applicant"),
    "caluma_municipality": translation.gettext_noop("Municipality (from Caluma)"),
    "construction_control": translation.gettext_noop("Construction control"),
    "inactive_municipality": translation.gettext_noop(
        "Municipality (if district active)"
    ),
    "internal_involved_entities": translation.gettext_noop(
        "Internal Message Recipients"
    ),
    "inquiry_addressed": translation.gettext_noop("Addressed service of inquiry"),
    "inquiry_controlling": translation.gettext_noop("Controlling service of inquiry"),
    "involved_in_distribution": translation.gettext_noop("Involved services"),
    "involved_in_districution_except_gvg": translation.gettext_noop(
        "Involved services"
    ),
    "services_with_incomplete_inquiries": translation.gettext_noop(
        "Services which have incomplete inquiries"
    ),
    "leitbehoerde": translation.gettext_noop("Authority"),
    "municipality": translation.gettext_noop("Municipality"),
    "unanswered_inquiries": translation.gettext_noop(
        "Services with unanswered inquiries"
    ),
    "work_item_addressed": translation.gettext_noop("Addressed service of work item"),
    "work_item_controlling": translation.gettext_noop(
        "Controlling service of work item"
    ),
    "additional_demand_inviter": translation.gettext_noop(
        "Inviter of additional demand creator"
    ),
    "geometer_acl_services": translation.gettext_noop(
        "Geometer (via permissions module)"
    ),
    "acl_authorized": translation.gettext_noop(
        "Authorized entity (via permissions module)"
    ),
    "immissionsschutz": translation.gettext_noop(
        "Office for Environment and Energy of the Canton of Bern: Immission control"
    ),
}


class InquiryMergeSerializer(serializers.Serializer):
    deadline_date = serializers.DateTimeField(
        source="deadline", format=settings.MERGE_DATE_FORMAT
    )
    start_date = serializers.DateTimeField(
        source="created_at", format=settings.MERGE_DATE_FORMAT
    )
    end_date = serializers.DateTimeField(
        source="closed_at", format=settings.MERGE_DATE_FORMAT
    )
    circulation_state = serializers.SerializerMethodField()
    service = serializers.SerializerMethodField()
    reason = serializers.SerializerMethodField()
    circulation_answer = serializers.SerializerMethodField()
    notices = serializers.SerializerMethodField()

    def get_circulation_state(self, inquiry):
        if inquiry.status == caluma_workflow_models.WorkItem.STATUS_READY:
            return (
                "REVIEW"
                if inquiry.child_case.work_items.filter(
                    status=caluma_workflow_models.WorkItem.STATUS_READY,
                    task_id=settings.DISTRIBUTION.get("INQUIRY_ANSWER_CHECK_TASK", ""),
                ).exists()
                else "RUN"
            )

        return (
            "OK"
            if inquiry.case.parent_work_item.status
            == caluma_workflow_models.WorkItem.STATUS_READY
            else "DONE"
        )

    def get_service(self, inquiry):
        return Service.objects.get(pk=inquiry.addressed_groups[0]).get_name()

    def get_reason(self, inquiry):
        return find_answer(
            inquiry.document, settings.DISTRIBUTION["QUESTIONS"]["REMARK"]
        )

    def get_circulation_answer(self, inquiry):
        return find_answer(
            inquiry.child_case.document,
            settings.DISTRIBUTION["QUESTIONS"]["STATUS"],
        )

    def get_notices(self, inquiry):
        return [
            {
                "notice_type": str(answer.question.label),
                "content": answer.value,
            }
            for answer in (
                inquiry.child_case.document.answers.select_related("question")
                .filter(question__type=caluma_form_models.Question.TYPE_TEXTAREA)
                .order_by("-question__formquestion__sort")
            )
        ]


class BillingEntryMergeSerializer(serializers.Serializer):
    amount = serializers.FloatField()
    service = serializers.StringRelatedField()
    created = serializers.DateTimeField(format=settings.MERGE_DATE_FORMAT)
    account = serializers.SerializerMethodField()
    account_number = serializers.SerializerMethodField()

    def get_account(self, billing_entry):
        billing_account = billing_entry.billing_account
        return "{0} / {1}".format(billing_account.department, billing_account.name)

    def get_account_number(self, billing_entry):
        return billing_entry.billing_account.account_number


class InstanceMergeSerializer(InstanceEditableMixin, serializers.Serializer):
    """Converts instance into a dict to be used with template merging."""

    # TODO: document.Template and notification.NotificationTemplate should
    # be moved to its own app template including this serializer.

    location = serializers.StringRelatedField()
    identifier = serializers.CharField()
    activations = serializers.SerializerMethodField()
    billing_entries = BillingEntryMergeSerializer(many=True)
    answer_period_date = serializers.SerializerMethodField()
    publication_date = serializers.SerializerMethodField()
    publications = serializers.SerializerMethodField()
    instance_id = serializers.IntegerField()
    public_dossier_link = serializers.SerializerMethodField()
    internal_dossier_link = serializers.SerializerMethodField()
    distribution_link = serializers.SerializerMethodField()
    registration_link = serializers.SerializerMethodField()
    dossier_nr = serializers.SerializerMethodField()
    leitbehoerde_name_de = serializers.SerializerMethodField()
    leitbehoerde_name_fr = serializers.SerializerMethodField()
    leitbehoerde_name_it = serializers.SerializerMethodField()
    municipality_de = serializers.SerializerMethodField()
    municipality_fr = serializers.SerializerMethodField()
    municipality_it = serializers.SerializerMethodField()
    form_name_de = serializers.SerializerMethodField()
    form_name_fr = serializers.SerializerMethodField()
    form_name_it = serializers.SerializerMethodField()
    ebau_number = serializers.SerializerMethodField()
    base_url = serializers.SerializerMethodField()
    rejection_feedback = serializers.SerializerMethodField()
    current_service = serializers.SerializerMethodField()
    current_service_de = serializers.SerializerMethodField()
    current_service_fr = serializers.SerializerMethodField()
    current_service_it = serializers.SerializerMethodField()
    current_service_description = serializers.SerializerMethodField()
    date_dossiervollstandig = serializers.SerializerMethodField()
    date_dossiereingang = serializers.SerializerMethodField()
    date_start_zirkulation = serializers.SerializerMethodField()
    date_bau_einspracheentscheid = serializers.SerializerMethodField()
    billing_total_kommunal = serializers.SerializerMethodField()
    billing_total_kanton = serializers.SerializerMethodField()
    billing_total = serializers.SerializerMethodField()
    billing_total_uncharged = serializers.SerializerMethodField()
    billing_total_uncharged_kommunal = serializers.SerializerMethodField()
    billing_total_uncharged_kanton = serializers.SerializerMethodField()
    my_activations = serializers.SerializerMethodField()
    objections = serializers.SerializerMethodField()
    bauverwaltung = serializers.SerializerMethodField()
    responsible_person = serializers.SerializerMethodField()
    schlussabnahme_uhrzeit = serializers.SerializerMethodField()
    schlussabnahme_datum = serializers.SerializerMethodField()

    vorhaben = serializers.SerializerMethodField()
    parzelle = serializers.SerializerMethodField()
    street = serializers.SerializerMethodField()
    gesuchsteller = fields.MasterDataPersonField(
        source="applicants",
        only_first=True,
        fields="__all__",
    )

    # TODO: these is currently bern specific, as it depends on instance state
    # identifiers. This will likely need some client-specific switch logic
    # some time in the future
    distribution_status_de = serializers.SerializerMethodField()
    distribution_status_fr = serializers.SerializerMethodField()
    distribution_status_it = serializers.SerializerMethodField()
    inquiry_answer_de = serializers.SerializerMethodField()
    inquiry_answer_fr = serializers.SerializerMethodField()
    inquiry_remark = serializers.SerializerMethodField()
    inquiry_link = serializers.SerializerMethodField()

    decision_de = serializers.SerializerMethodField()
    decision_fr = serializers.SerializerMethodField()
    decision_it = serializers.SerializerMethodField()

    current_user_name = serializers.SerializerMethodField()
    work_item_name_de = serializers.SerializerMethodField()
    work_item_name_fr = serializers.SerializerMethodField()

    def __init__(
        self,
        instance=None,
        inquiry=None,
        work_item=None,
        escape=False,
        used_placeholders=[],
        *args,
        **kwargs,
    ):
        self.escape = escape
        self.inquiry = inquiry
        self.work_item = work_item

        super().__init__(instance=instance, *args, **kwargs)

        if instance:
            instance._master_data = MasterData(instance.case)
        self.service = (
            self.context["request"].group.service if "request" in self.context else None
        )

        # This part here is more or less copy pasted from the
        # SparseFieldsetsMixion of DRF-JSON-API but using a passed argument
        # instead of query params.
        if used_placeholders:
            for field_name, _ in self.fields.fields.copy().items():
                if field_name not in used_placeholders:
                    self.fields.pop(field_name)

    def _escape(self, data):
        result = data
        if isinstance(data, str):
            result = escape(data)
        elif isinstance(data, list):
            result = [self._escape(value) for value in data]
        elif isinstance(data, dict):
            result = {key: self._escape(value) for key, value in data.items()}

        return result

    def _clean_none(self, data):
        result = data if data is not None else ""
        if isinstance(data, list):
            result = [self._clean_none(value) for value in data]
        elif isinstance(data, dict):
            result = {key: self._clean_none(value) for key, value in data.items()}

        return result

    def format_date(self, date):
        current_tz = timezone.get_current_timezone()
        return date.astimezone(current_tz).strftime(settings.MERGE_DATE_FORMAT)

    def get_vorhaben(self, instance):
        description_slugs = [
            "proposal-description",
            "beschreibung-zu-mbv",
            "bezeichnung",
            "vorhaben-proposal-description",
            "veranstaltung-beschrieb",
            "beschrieb-verfahren",
        ]
        descriptions = [
            CalumaApi().get_answer_value(slug, instance) for slug in description_slugs
        ]
        return clean_join(*descriptions, separator=", ")

    def _get_row_answer_value(self, row, slug, fallback=None):
        try:
            return row.answers.get(question_id=slug).value
        except caluma_form_models.Answer.DoesNotExist:
            return fallback

    def get_parzelle(self, instance):
        rows = CalumaApi().get_table_answer("parcels", instance)
        if rows:
            numbers = [self._get_row_answer_value(row, "parcel-number") for row in rows]
            return ", ".join([str(n) for n in numbers if n is not None])
        return None

    def get_street(self, instance):
        return CalumaApi().get_answer_value("parcel-street", instance)

    def get_rejection_feedback(self, instance):
        return instance.rejection_feedback or ""

    def get_answer_period_date(self, instace):
        answer_period_date = date.today() + timedelta(days=settings.MERGE_ANSWER_PERIOD)
        return answer_period_date.strftime(settings.MERGE_DATE_FORMAT)

    def get_publication_date(self, instance):
        publication_entry = instance.publication_entries.first()

        return (
            publication_entry
            and self.format_date(publication_entry.publication_date)
            or ""
        )

    def get_publications(self, instance):
        publications = []

        for publication in instance.publication_entries.filter(is_published=1).order_by(
            "publication_date"
        ):
            publications.append(
                {
                    "date": self.format_date(publication.publication_date),
                    "end_date": self.format_date(publication.publication_end_date),
                    "calendar_week": publication.publication_date.isocalendar()[1],
                }
            )

        return publications

    def _get_leitbehoerde_name(self, instance, language):
        service = instance.responsible_service(filter_type="municipality")

        return service.get_name(language) if service else "-"

    def get_leitbehoerde_name_de(self, instance):
        """Return current active service of the instance in german."""
        return self._get_leitbehoerde_name(instance, "de")

    def get_leitbehoerde_name_fr(self, instance):
        """Return current active service of the instance in french."""
        return self._get_leitbehoerde_name(instance, "fr")

    def get_leitbehoerde_name_it(self, instance):
        """Return current active service of the instance in italian."""
        return self._get_leitbehoerde_name(instance, "it")

    def get_municipality_de(self, instance):
        """Return municipality in german."""
        return self.get_municipality(instance, "de")

    def get_municipality_fr(self, instance):
        """Return municipality in french."""
        return self.get_municipality(instance, "fr")

    def get_municipality_it(self, instance):
        """Return municipality in italian."""
        return self.get_municipality(instance, "it")

    def get_municipality(self, instance, language):
        try:
            service = Service.objects.get(pk=CalumaApi().get_municipality(instance))
            name = service.get_name(language)

            return name.replace("Leitbehörde", "Gemeinde").replace(
                "Autorité directrice", "Municipalité"
            )
        except Service.DoesNotExist:
            return ""

    def get_current_service(self, instance):
        """Return current service of the active user."""
        return self.service.get_name() if self.service else "-"

    def get_current_service_de(self, instance):
        """Return current service of the active user in german."""
        return self.service.get_name("de") if self.service else "-"

    def get_current_service_fr(self, instance):
        """Return current service of the active user in french."""
        return self.service.get_name("fr") if self.service else "-"

    def get_current_service_it(self, instance):
        """Return current service of the active user in italian."""
        return self.service.get_name("it") if self.service else "-"

    def get_current_service_description(self, instance):
        """Return description of the current service of the active user."""
        return (
            self.service.get_trans_attr("description") or self.service.get_name()
            if self.service
            else "-"
        )

    def get_distribution_status_de(self, instance):
        return self._get_distribution_status(instance, "de")

    def get_distribution_status_fr(self, instance):
        return self._get_distribution_status(instance, "fr")

    def get_distribution_status_it(self, instance):
        return self._get_distribution_status(instance, "it")

    def _get_distribution_status(self, instance, language):
        if not settings.DISTRIBUTION or not self.inquiry:
            return ""

        all_inquiries = caluma_workflow_models.WorkItem.objects.filter(
            task_id=settings.DISTRIBUTION["INQUIRY_TASK"],
            case__family__instance=instance,
            controlling_groups=self.inquiry.controlling_groups,
        ).exclude(
            status__in=[
                caluma_workflow_models.WorkItem.STATUS_SUSPENDED,
                caluma_workflow_models.WorkItem.STATUS_CANCELED,
                caluma_workflow_models.WorkItem.STATUS_SKIPPED,
                caluma_workflow_models.WorkItem.STATUS_REDO,
            ]
        )
        all_inquiries_count = all_inquiries.count()
        pending_inquiries_count = all_inquiries.filter(
            status=caluma_workflow_models.WorkItem.STATUS_READY
        ).count()

        if not all_inquiries.exists():  # pragma: no cover (this should never happen)
            return ""

        with translation.override(language):
            return (
                translation.gettext(
                    "%(pending)d of %(total)d inquries are still pending."
                )
                if pending_inquiries_count > 0
                else translation.gettext("All %(total)d inquries were received.")
            ) % {"total": all_inquiries_count, "pending": pending_inquiries_count}

    def get_inquiry_answer_de(self, instance):
        return self._get_inquiry_answer("de")

    def get_inquiry_answer_fr(self, instance):
        return self._get_inquiry_answer("fr")

    def _get_inquiry_answer(self, language):
        if not self.inquiry or not settings.DISTRIBUTION:
            return ""

        return find_answer(
            self.inquiry.child_case.document,
            settings.DISTRIBUTION["QUESTIONS"]["STATUS"],
            language=language,
        )

    def get_inquiry_remark(self, instance):
        if not self.inquiry or not settings.DISTRIBUTION:
            return ""

        return find_answer(
            self.inquiry.document, settings.DISTRIBUTION["QUESTIONS"]["REMARK"]
        )

    def get_inquiry_link(self, instance):
        if not self.inquiry:
            return ""

        if settings.APPLICATION["INTERNAL_FRONTEND"] == "camac":
            return build_url(
                settings.INTERNAL_BASE_URL,
                "/index/redirect-to-instance-resource/instance-id/",
                instance.pk,
                "?instance-resource-name=distribution&ember-hash=/distribution/",
                self.inquiry.case.pk,
                "from",
                self.inquiry.controlling_groups[0],
                "to",
                self.inquiry.addressed_groups[0],
                self.inquiry.pk,
                "answer",
            )

        return build_url(
            instance.get_internal_url(),
            "/distribution/",
            self.inquiry.case.pk,
            "from",
            self.inquiry.controlling_groups[0],
            "to",
            self.inquiry.addressed_groups[0],
            self.inquiry.pk,
            "answer",
        )

    def get_form_name_de(self, instance):
        return CalumaApi().get_form_name(instance).de or ""

    def get_form_name_fr(self, instance):
        return CalumaApi().get_form_name(instance).fr or ""

    def get_form_name_it(self, instance):
        return CalumaApi().get_form_name(instance).it or ""

    def get_ebau_number(self, instance):
        """Dossier number - Kanton Bern."""
        if settings.APPLICATION["FORM_BACKEND"] != "caluma":
            return "-"

        return CalumaApi().get_ebau_number(instance) or "-"

    def get_dossier_nr(self, instance):
        """Dossier number - Kanton Uri."""
        return CalumaApi().get_dossier_number(instance) or "-"

    def get_internal_dossier_link(self, instance):
        return instance.get_internal_url()

    def get_distribution_link(self, instance):
        return build_url(
            instance.get_internal_url(),
            "/distribution",
        )

    def get_public_dossier_link(self, instance):
        return settings.PUBLIC_INSTANCE_URL_TEMPLATE.format(instance_id=instance.pk)

    def get_registration_link(self, instance):
        return settings.REGISTRATION_URL

    def get_base_url(self, instance):
        return settings.INTERNAL_BASE_URL

    def _get_workflow_entry_date(self, instance, item_id):
        entry = WorkflowEntry.objects.filter(
            instance=instance, workflow_item=item_id
        ).first()
        if entry:
            return self.format_date(entry.workflow_date)
        return "---"

    def get_date_dossiervollstandig(self, instance):
        return self._get_workflow_entry_date(
            instance,
            settings.APPLICATION.get("WORKFLOW_ITEMS", {}).get("INSTANCE_COMPLETE"),
        )

    def get_date_dossiereingang(self, instance):
        return self._get_workflow_entry_date(
            instance, settings.APPLICATION.get("WORKFLOW_ITEMS", {}).get("SUBMIT")
        )

    def get_date_start_zirkulation(self, instance):
        if not settings.DISTRIBUTION:
            return "---"

        distribution_closed_at = (
            caluma_workflow_models.WorkItem.objects.filter(
                case__family__instance=instance,
                task_id=settings.DISTRIBUTION["DISTRIBUTION_INIT_TASK"],
                status=caluma_workflow_models.WorkItem.STATUS_COMPLETED,
                closed_at__isnull=False,
            )
            .values_list("closed_at", flat=True)
            .first()
        )

        if distribution_closed_at:
            return self.format_date(distribution_closed_at)

        return "---"

    def get_date_bau_einspracheentscheid(self, instance):
        work_item = instance.case.work_items.filter(
            task_id="building-authority"
        ).first()

        if work_item:
            answer = caluma_form_models.Answer.objects.filter(
                question_id="bewilligungsverfahren-gr-sitzung-bewilligungsdatum",
                document=work_item.document,
            ).first()

            return (
                self.format_date(datetime.combine(answer.date, datetime.min.time()))
                if answer and answer.date
                else "---"
            )
        return "---"

    def get_billing_total_kommunal(self, instance):
        return (
            BillingV2Entry.objects.filter(
                instance=instance, organization=BillingV2Entry.MUNICIPAL
            ).aggregate(total=Sum("final_rate"))["total"]
            or "0.00"
        )

    def get_billing_total_kanton(self, instance):
        return (
            BillingV2Entry.objects.filter(
                instance=instance, organization=BillingV2Entry.CANTONAL
            ).aggregate(total=Sum("final_rate"))["total"]
            or "0.00"
        )

    def get_billing_total(self, instance):
        return (
            BillingV2Entry.objects.filter(instance=instance).aggregate(
                total=Sum("final_rate")
            )["total"]
            or "0.00"
        )

    def get_billing_total_uncharged(self, instance):
        return (
            BillingV2Entry.objects.filter(
                instance=instance, date_charged__isnull=True
            ).aggregate(total=Sum("final_rate"))["total"]
            or "0.00"
        )

    def get_billing_total_uncharged_kommunal(self, instance):
        return (
            BillingV2Entry.objects.filter(
                instance=instance,
                organization=BillingV2Entry.MUNICIPAL,
                date_charged__isnull=True,
            ).aggregate(total=Sum("final_rate"))["total"]
            or "0.00"
        )

    def get_billing_total_uncharged_kanton(self, instance):
        return (
            BillingV2Entry.objects.filter(
                instance=instance,
                organization=BillingV2Entry.CANTONAL,
                date_charged__isnull=True,
            ).aggregate(total=Sum("final_rate"))["total"]
            or "0.00"
        )

    def _get_inquiries(self, instance):
        if not settings.DISTRIBUTION:
            return caluma_workflow_models.WorkItem.objects.none()

        service_subquery = Service.objects.filter(
            Any(
                F("pk"),
                Cast(
                    OuterRef("addressed_groups"),
                    output_field=ArrayField(IntegerField()),
                ),
            )
        )

        return (
            caluma_workflow_models.WorkItem.objects.filter(
                task_id=settings.DISTRIBUTION["INQUIRY_TASK"],
                case__family__instance=instance,
            )
            .exclude(
                status__in=[
                    caluma_workflow_models.WorkItem.STATUS_CANCELED,
                    caluma_workflow_models.WorkItem.STATUS_SUSPENDED,
                ]
            )
            .annotate(
                service_group_id=Subquery(
                    service_subquery.values("service_group_id")[:1]
                ),
                service_group_sort=Subquery(
                    service_subquery.values("service_group__sort")[:1]
                ),
                service_sort=Subquery(service_subquery.values("sort")[:1]),
                parent_service_sort=Case(
                    When(
                        Exists(
                            service_subquery.filter(service_parent_id__isnull=False)
                        ),
                        then=Subquery(
                            service_subquery.values("service_parent__sort")[:1],
                            output_field=CharField(),
                        ),
                    ),
                    default=Subquery(
                        service_subquery.values("sort")[:1], output_field=CharField()
                    ),
                ),
                service_grouping=Case(
                    When(
                        Exists(
                            service_subquery.filter(service_parent_id__isnull=False)
                        ),
                        then=Subquery(
                            service_subquery.values("service_parent_id")[:1],
                            output_field=CharField(),
                        ),
                    ),
                    default=Subquery(
                        service_subquery.values("pk")[:1], output_field=CharField()
                    ),
                ),
                is_subservice=Exists(
                    service_subquery.filter(service_parent_id__isnull=False)
                ),
            )
            .order_by(
                "service_group_sort",
                "parent_service_sort",
                "service_grouping",
                "is_subservice",
                "service_sort",
                "controlling_groups",
                F("closed_at").desc(nulls_first=True),
                "-deadline",
            )
        )

    def get_activations(self, instance):
        inquiries = self._get_inquiries(instance)

        visibility_config = settings.APPLICATION.get("INTER_SERVICE_GROUP_VISIBILITIES")
        if visibility_config:
            inquiries = (
                inquiries.filter(
                    service_group_id__in=visibility_config.get(
                        self.service.service_group_id, []
                    )
                )
                if self.service
                else inquiries.none()
            )

        return InquiryMergeSerializer(inquiries, many=True).data

    def get_my_activations(self, instance):
        inquiries = self._get_inquiries(instance)

        return InquiryMergeSerializer(
            (
                inquiries.filter(addressed_groups__contains=[str(self.service.pk)])
                if self.service
                else inquiries.none()
            ),
            many=True,
        ).data

    def get_objections(self, instance):
        objections = instance.objections.all()

        for objection in objections:
            objection.participants = objection.objection_participants.all()

            if objection.objection_participants.filter(representative=1).exists():
                objection.participants = objection.objection_participants.filter(
                    representative=1
                )

            objection.creation_date = objection.creation_date.strftime(
                settings.MERGE_DATE_FORMAT
            )

        return objections

    def get_bauverwaltung(self, instance):
        if not settings.APPLICATION.get("INSTANCE_MERGE_CONFIG"):
            return {}

        work_item_qs = instance.case.work_items.filter(
            task_id=settings.APPLICATION["INSTANCE_MERGE_CONFIG"]["BAUVERWALTUNG"][
                "TASK_SLUG"
            ]
        )

        if not work_item_qs.exists():  # pragma: no cover
            return {}

        document = work_item_qs.first().document
        answers = {
            inflection.underscore(answer.question.slug): get_answer_display_value(
                answer, option_separator="\n"
            )
            for answer in document.answers.filter(
                Q(value__isnull=False) | Q(date__isnull=False)
            )
        }

        # loop over table questions in sub form questions
        for question in caluma_form_models.Question.objects.filter(
            type=caluma_form_models.Question.TYPE_TABLE,
            forms__in=document.form.questions.filter(
                type=caluma_form_models.Question.TYPE_FORM
            ).values("sub_form"),
        ):
            question_answers = []
            # loop over table rows in table question
            for row in caluma_form_models.AnswerDocument.objects.filter(
                answer__question_id=question.slug, document__family=document
            ):
                row_answers = {}
                # loop over answers in table row to format the answer
                for answer in row.document.answers.all():
                    row_answers[inflection.underscore(answer.question.slug)] = (
                        get_answer_display_value(answer, option_separator="\n")
                    )
                question_answers.append(row_answers)
            answers[inflection.underscore(question.slug)] = question_answers

        return answers

    def get_responsible_person(self, instance):
        responsible_service = instance.responsible_services.filter(
            service=self.service
        ).first()
        return (
            responsible_service.responsible_user.get_full_name()
            if responsible_service
            else ""
        )

    def get_current_user_name(self, instance):
        return (
            self.context["request"].user.get_full_name()
            if "request" in self.context
            else None
        )

    def get_work_item_name_de(self, instance):
        return self.work_item.name.get("de") if self.work_item else None

    def get_work_item_name_fr(self, instance):
        return self.work_item.name.get("fr") if self.work_item else None

    def get_decision_de(self, instance):
        return self._get_decision(instance, "de")

    def get_decision_fr(self, instance):
        return self._get_decision(instance, "fr")

    def get_decision_it(self, instance):
        return self._get_decision(instance, "it")

    def _get_decision(self, instance, language):
        if not settings.DECISION:  # pragma: no cover
            return ""

        decision = instance.case.work_items.filter(
            task_id=settings.DECISION["TASK"],
            status__in=[
                caluma_workflow_models.WorkItem.STATUS_COMPLETED,
                caluma_workflow_models.WorkItem.STATUS_SKIPPED,
            ],
        ).first()

        if not decision:
            return ""

        return find_answer(
            decision.document,
            settings.DECISION["QUESTIONS"]["DECISION"],
            language=language,
        )

    def get_schlussabnahme_uhrzeit(self, instance):
        if (
            not self.work_item
            or not self.work_item.case
            or not settings.CONSTRUCTION_MONITORING
        ):
            return None

        # Do not use instance.case here because construction monitoring has nested cases
        if schlussabnahme_planing_work_item := self.work_item.case.work_items.filter(
            task_id=settings.CONSTRUCTION_MONITORING[
                "CONSTRUCTION_STEP_PLAN_SCHLUSSABNAHME_PROJEKT_TASK"
            ]
        ).first():
            if answer := schlussabnahme_planing_work_item.document.answers.filter(
                question_id="construction-step-schlussabnahme-projekt-planen-zeit-der-abnahme"
            ).first():
                return answer.value

    def get_schlussabnahme_datum(self, instance):
        if (
            not self.work_item
            or not self.work_item.case
            or not settings.CONSTRUCTION_MONITORING
        ):
            return None

        # Do not use instance.case here because construction monitoring has nested cases
        if schlussabnahme_planing_work_item := self.work_item.case.work_items.filter(
            task_id=settings.CONSTRUCTION_MONITORING[
                "CONSTRUCTION_STEP_PLAN_SCHLUSSABNAHME_PROJEKT_TASK"
            ]
        ).first():
            if answer := schlussabnahme_planing_work_item.document.answers.filter(
                question_id="construction-step-schlussabnahme-projekt-planen-datum-der-abnahme"
            ).first():
                return answer.date.strftime("%d.%m.%Y")

    def to_representation(self, instance):
        ret = super().to_representation(instance)

        for field in instance.fields.all():
            # remove versioning (-v3) from question names so the placeholders are backwards compatible
            name_without_version = re.sub(r"(-v\d+$)", "", field.name)
            name = inflection.underscore("field-" + name_without_version)
            value = field.value

            if (
                field.name == settings.APPLICATION.get("COORDINATE_QUESTION", "")
                and value is not None
            ):
                value = "\n".join(transform_coordinates(value))
            elif field.name in settings.APPLICATION.get("QUESTIONS_WITH_OVERRIDE", []):
                override = instance.fields.filter(
                    name=f"{name_without_version}-override"
                ).first()
                value = override.value if override else value
            elif field.name == settings.APPLICATION.get("LOCATION_NAME_QUESTION", ""):
                ret["field_standort_adresse"] = value

            ret[name] = self._clean_none(value)

        if self.escape:
            ret = self._escape(ret)

        return ret

    class Meta:
        resource_name = "instance-merges"


class IssueMergeSerializer(serializers.Serializer):
    deadline_date = serializers.DateField()
    text = serializers.CharField()

    def to_representation(self, issue):
        ret = super().to_representation(issue)

        # include instance merge fields
        ret.update(
            InstanceMergeSerializer(instance=issue.instance, context=self.context).data
        )

        return ret


class NotificationTemplateSerializer(serializers.ModelSerializer):
    notification_type = serializers.CharField(source="type")

    def create(self, validated_data):
        service = self.context["request"].group.service
        validated_data["service"] = service
        validated_data["slug"] = slugify(validated_data["slug"])
        return super().create(validated_data)

    def validate_slug(self, value):
        if self.instance:
            raise serializers.ValidationError("Updating a slug is not allowed!")
        return value

    class Meta:
        model = models.NotificationTemplate
        fields = ("slug", "purpose", "subject", "body", "notification_type", "service")


class NotificationTemplateMergeSerializer(
    InstanceEditableMixin, serializers.Serializer
):
    instance_editable_permission = None
    """
    No specific permission needed to send notification
    """

    instance = serializers.ResourceRelatedField(queryset=Instance.objects.all())
    inquiry = serializers.ResourceRelatedField(
        queryset=caluma_workflow_models.WorkItem.objects.all(),
        required=False,
    )
    work_item = serializers.ResourceRelatedField(
        queryset=caluma_workflow_models.WorkItem.objects.all(),
        required=False,
    )
    case = serializers.ResourceRelatedField(
        queryset=caluma_workflow_models.Case.objects.all(),
        required=False,
    )
    notification_template = serializers.ResourceRelatedField(
        queryset=models.NotificationTemplate.objects.all()
    )
    subject = serializers.CharField(required=False)
    body = serializers.CharField(required=False)

    def _merge(self, value, data):
        value_template = jinja2.Template(value)

        return value_template.render(data)

    def _get_used_placeholders(self, subject, body):
        try:
            content = subject + body
            env = SandboxedEnvironment()
            ast = env.parse(content)

            return [
                placeholder.lower()
                # Find all variables that are used in the notification template.
                # Since we parsed the template with jinja without adding any
                # context (variables) those will be "undeclared variables"
                for placeholder in jinja2.meta.find_undeclared_variables(ast)
            ]
        except jinja2.TemplateError as e:
            raise exceptions.ValidationError(str(e))

    def validate(self, data):
        notification_template = data["notification_template"]
        instance = data["instance"]

        subject = data.get("subject", notification_template.get_trans_attr("subject"))
        body = data.get("body", notification_template.get_trans_attr("body"))

        placeholder_data = InstanceMergeSerializer(
            instance=instance,
            context=self.context,
            inquiry=data.get("inquiry"),
            work_item=data.get("work_item"),
            used_placeholders=self._get_used_placeholders(subject, body),
        ).data

        # some cantons use uppercase placeholders. be as compatible as possible
        placeholder_data.update({k.upper(): v for k, v in placeholder_data.items()})

        data["subject"] = self._merge(subject, placeholder_data)
        data["body"] = self._merge(body, placeholder_data)
        data["pk"] = "{0}-{1}".format(notification_template.slug, instance.pk)

        return data

    def create(self, validated_data):
        NotificationTemplateMerge = namedtuple(
            "NotificationTemplateMerge", validated_data.keys()
        )
        obj = NotificationTemplateMerge(**validated_data)

        return obj

    class Meta:
        resource_name = "notification-template-merges"


class NotificationTemplateSendmailSerializer(NotificationTemplateMergeSerializer):
    # Activation, circulation and message are only needed for the recipient types
    activation = serializers.ResourceRelatedField(
        queryset=Activation.objects.all(), required=False
    )
    circulation = serializers.ResourceRelatedField(
        queryset=Circulation.objects.all(), required=False
    )

    message = serializers.ResourceRelatedField(
        queryset=CommunicationsMessage.objects.all(), required=False
    )

    # Used for passing in additional information to the recipient
    # functions
    metainfo = serializers.DictField(required=False, default=None)

    recipient_types = serializers.MultipleChoiceField(
        choices=(
            "applicant",
            "unregistered_applicant",
            "municipality",
            "caluma_municipality",
            "leitbehoerde",
            "construction_control",
            "email_list",
            # Old circulation (UR)
            "unnotified_service",
            "activation_service_parent",
            # New circulation (BE, SZ)
            "involved_in_distribution",
            "unanswered_inquiries",
            "internal_involved_entities",
            "inquiry_addressed",
            "inquiry_controlling",
            # Work items
            "work_item_addressed",
            "work_item_controlling",
            "additional_demand_inviter",
            "acl_authorized",
            # GR specific
            "involved_in_distribution_except_gvg",
            "services_with_incomplete_inquiries",
            *settings.APPLICATION.get("CUSTOM_NOTIFICATION_TYPES", []),
        )
    )
    email_list = serializers.CharField(required=False)

    def _group_service_recipients(self, groups):
        return [
            {"to": email}
            for email in unpack_service_emails(
                Service.objects.filter(groups__in=groups, notification=1)
            )
        ]

    def _get_recipients_municipality_users(self, instance):
        """Email addresses on the municipality's service email list."""
        groups = Group.objects.filter(
            locations=instance.location, role=uri_constants.ROLE_MUNICIPALITY
        )
        return self._group_service_recipients(groups)

    def _get_recipients_unnotified_service_users(self, instance):
        circulation = self.validated_data["circulation"]
        activations = circulation.activations.filter(
            circulation_state_id=uri_constants.CIRCULATION_STATE_IDLE
        )

        services = Service.objects.filter(
            pk__in=activations.values("service_id"), notification=1
        )

        return [
            {"to": email}
            for value in services.values_list("email", flat=True)
            if value
            for email in value.split(",")
        ]

    def _notify_service(self, service_id):
        return [
            {"to": email}
            for email in unpack_service_emails(
                Service.objects.filter(pk=service_id, notification=1)
            )
        ]

    def _get_recipients_koor_np_users(self, instance):
        return self._notify_service(uri_constants.KOOR_NP_SERVICE_ID)

    def _get_recipients_koor_bg_users(self, instance):
        return self._notify_service(uri_constants.KOOR_BG_SERVICE_ID)

    def _get_recipients_responsible_koor(self, instance):
        return self._notify_service(get_responsible_koor_service_id(instance.form.pk))

    def _get_recipients_geometer_acl_services(self, instance):
        geometer_acls = (
            InstanceACL.currently_active()
            .filter(instance=instance)
            .filter(access_level_id="geometer")
        )

        answer = geometer_cadastral_survey_necessary_answer(instance)
        if answer and not geometer_cadastral_survey_is_necessary(answer):
            geometer_acls = geometer_acls.filter(created_by_event="manual-creation")

        return flatten(
            [self._get_responsible(instance, acl.service) for acl in geometer_acls]
        )

    def _get_recipients_localized_geometer(self, instance):
        if not settings.APPLICATION.get("LOCALIZED_GEOMETER_SERVICE_MAPPING"):
            return []  # pragma: no cover

        geometer_answer = (
            instance.fields.filter(
                name__in=settings.APPLICATION.get("GEOMETER_FORM_FIELDS", [])
            )
            .values_list("value", flat=True)
            .first()
        )

        if not geometer_answer:
            return []

        geometer_service_ids = settings.APPLICATION[
            "LOCALIZED_GEOMETER_SERVICE_MAPPING"
        ].get(geometer_answer, [])

        # TODO: For geometers that have groups that have a subset of locations and a
        # group without any location, are the groups containing the location to be preferred?
        geometer_services = Service.objects.filter(
            Q(groups__locations__in=[instance.location])
            | Q(groups__locations__isnull=True),
            pk__in=geometer_service_ids,
        )[:1]

        return flatten(
            [self._get_responsible(instance, service) for service in geometer_services]
        )

    def _get_recipients_lisag(self, instance):
        groups = Group.objects.filter(name="Lisag")
        return [{"to": group.email} for group in groups]

    def _get_recipients_inactive_municipality(self, instance):
        if (
            instance.responsible_service(filter_type="municipality").service_group.name
            != "district"
        ):
            return []

        return self._get_recipients_caluma_municipality(instance)

    def _get_recipients_caluma_municipality(self, instance):
        municipality_service_id = CalumaApi().get_municipality(instance)

        if not municipality_service_id:  # pragma: no cover
            raise exceptions.ValidationError(
                f"Could not get Caluma municipality for instance {instance.pk}"
            )

        return self._get_responsible(
            instance,
            Service.objects.filter(pk=municipality_service_id, notification=1).first(),
        )

    def _get_recipients_applicant(self, instance):
        return [
            {"to": applicant.invitee.email}
            for applicant in instance.involved_applicants.all()
            if applicant.invitee
        ]

    def _get_recipients_unregistered_applicant(self, instance):
        return [
            {"to": applicant.email}
            for applicant in instance.involved_applicants.filter(invitee__isnull=True)
        ]

    def _get_recipients_acl_authorized(self, instance):
        recipients = []

        acl = self.data["metainfo"]["acl"]
        if not acl or not acl.is_active():
            # TODO: Log? This should never happen: "ACL created"
            # event, but no ACL exists or is active?
            return []  # pragma: no cover

        if acl.user:
            recipients.append({"to": acl.user.email})
        elif acl.service:
            recipients.append({"to": acl.service.email})

        return recipients

    def _get_responsible(self, instance, service, work_item=None):
        if not service or not service.notification:
            return []

        # Responsible user for the instance from various responsibility modules
        responsible_user = User.objects.filter(
            pk__in=[
                *instance.responsible_services.filter(service=service).values_list(
                    "responsible_user", flat=True
                ),
                *instance.responsibilities.filter(service=service).values_list(
                    "user", flat=True
                ),
            ]
        ).first()

        # Assigned user from the work item
        assigned_user = (
            User.objects.filter(username__in=work_item.assigned_users).first()
            if work_item
            else None
        )

        if assigned_user or responsible_user:
            return [
                {
                    "to": (
                        assigned_user.email if assigned_user else responsible_user.email
                    ),
                    "cc": service.email,
                }
            ]

        return [{"to": service.email}]

    def _get_recipients_leitbehoerde(self, instance):  # pragma: no cover
        return self._get_responsible(
            instance, instance.responsible_service(filter_type="municipality")
        )

    def _get_recipients_municipality(self, instance):
        return self._get_responsible(instance, instance.group.service)

    def _get_recipients_unnotified_service(self, instance):
        # Circulation and subcirculation share the same circulation object.
        # They can only be distinguished by their SERVICE_PARENT_ID.
        activations = Activation.objects.filter(
            circulation=self.validated_data.get("circulation"),
            email_sent=0,
            service_parent=self.context["request"].group.service,
            service__notification=1,
        )

        return flatten(
            [
                self._get_responsible(instance, activation.service)
                for activation in activations
            ]
        )

    def _get_recipients_involved_in_distribution(self, instance):
        if not settings.DISTRIBUTION:  # pragma: no cover
            return []

        inquiries = caluma_workflow_models.WorkItem.objects.filter(
            task_id=settings.DISTRIBUTION["INQUIRY_TASK"],
            case__family__instance=instance,
        ).exclude(
            status__in=[
                caluma_workflow_models.WorkItem.STATUS_SUSPENDED,
                caluma_workflow_models.WorkItem.STATUS_CANCELED,
            ],
        )

        not_involved_answer = (
            settings.DISTRIBUTION["ANSWERS"].get("STATUS", {}).get("NOT_INVOLVED")
        )

        if not_involved_answer:
            # don't involve services that responded the inquiry with "not involved"
            inquiries = inquiries.exclude(
                Exists(
                    caluma_form_models.Answer.objects.filter(
                        document__case__parent_work_item=OuterRef("pk"),
                        question_id=settings.DISTRIBUTION["QUESTIONS"]["STATUS"],
                        value=not_involved_answer,
                    )
                )
            )

        addressed_groups = inquiries.values_list("addressed_groups", flat=True)

        return flatten(
            [
                self._get_responsible(instance, service)
                for service in Service.objects.filter(
                    pk__in=list(chain(*addressed_groups))
                )
            ]
        )

    def _get_recipients_involved_in_distribution_except_gvg(self, instance):
        recipients = self._get_recipients_involved_in_distribution(instance)
        return [
            recipient for recipient in recipients if "@gvg.ch" not in recipient["to"]
        ]

    def _get_recipients_unanswered_inquiries(self, instance):
        if not settings.DISTRIBUTION:  # pragma: no cover
            return []

        addressed_groups = caluma_workflow_models.WorkItem.objects.filter(
            task_id=settings.DISTRIBUTION["INQUIRY_TASK"],
            status=caluma_workflow_models.WorkItem.STATUS_SKIPPED,
            case__family__instance=instance,
        ).values_list("addressed_groups", flat=True)

        return flatten(
            [
                self._get_responsible(instance, service)
                for service in Service.objects.filter(
                    pk__in=list(chain(*addressed_groups))
                )
            ]
        )

    def _get_recipients_services_with_incomplete_inquiries(self, instance):
        if not settings.DISTRIBUTION:  # pragma: no cover
            return []

        inquiries = caluma_workflow_models.WorkItem.objects.filter(
            task_id=settings.DISTRIBUTION["INQUIRY_TASK"],
            case__family__instance=instance,
            status=caluma_workflow_models.WorkItem.STATUS_SKIPPED,
        )

        addressed_groups = inquiries.values_list("addressed_groups", flat=True)

        return flatten(
            [
                self._get_responsible(instance, service)
                for service in Service.objects.filter(
                    pk__in=list(chain(*addressed_groups))
                )
            ]
        )

    def _get_recipients_inquiry_addressed(self, instance):
        inquiry = self.validated_data.get("inquiry")

        if not settings.DISTRIBUTION or not inquiry:  # pragma: no cover
            return []

        return self._get_responsible(
            instance, Service.objects.get(pk=inquiry.addressed_groups[0])
        )

    def _get_recipients_internal_involved_entities(self, instance):
        message = self.validated_data.get("message")

        if not message:  # pragma: no cover
            return []

        created_by = message.created_by

        result = [
            entity
            for entity in message.topic.involved_entities
            if entity not in ["APPLICANT", created_by]
        ]

        services = Service.objects.filter(pk__in=result, notification=1)
        return flatten(
            [self._get_responsible(instance, service) for service in services]
        )

    def _get_recipients_inquiry_controlling(self, instance):
        inquiry = self.validated_data.get("inquiry")

        if not settings.DISTRIBUTION or not inquiry:  # pragma: no cover
            return []

        return self._get_responsible(
            instance, Service.objects.get(pk=inquiry.controlling_groups[0])
        )

    def _get_recipients_construction_control(self, instance):
        instance_services = core_models.InstanceService.objects.filter(
            instance=instance,
            service__service_group__name="construction-control",
            active=1,
        )
        return flatten(
            [
                self._get_responsible(instance, instance_service.service)
                for instance_service in instance_services
            ]
        )

    def _get_recipients_email_list(self, instance):
        return [{"to": to} for to in self.validated_data["email_list"].split(",")]

    def _get_recipients_activation_service_parent(self, instance):
        activation = self.validated_data.get("activation")

        if not activation or not activation.service_parent:  # pragma: no cover
            return []

        return self._get_responsible(instance, activation.service_parent)

    def _get_recipients_work_item_controlling(self, instance):
        return flatten(
            [
                self._get_responsible(instance, service)
                for service in Service.objects.filter(
                    pk__in=self.validated_data.get("work_item").controlling_groups
                )
            ]
        )

    def _get_recipients_work_item_addressed(self, instance):
        work_item = self.validated_data.get("work_item")

        data = []
        for group in work_item.addressed_groups:
            if group == "applicant":
                data.append(self._get_recipients_applicant(instance))
            elif group == "municipality":
                data.append(self._get_recipients_municipality(instance))
            else:
                data.append(
                    flatten(
                        [
                            self._get_responsible(instance, service, work_item)
                            for service in Service.objects.filter(pk=group)
                        ]
                    )
                )
        return flatten(data)

    def _get_recipients_additional_demand_inviter(self, instance):
        if not settings.ADDITIONAL_DEMAND:  # pragma: no cover
            return []

        current_group = self.validated_data.get(
            "work_item"
        ).case.parent_work_item.addressed_groups
        inquiries = caluma_workflow_models.WorkItem.objects.filter(
            task_id=settings.DISTRIBUTION["INQUIRY_TASK"],
            case__family__instance=instance,
            addressed_groups=current_group,
        ).exclude(
            status__in=[
                caluma_workflow_models.WorkItem.STATUS_SUSPENDED,
                caluma_workflow_models.WorkItem.STATUS_CANCELED,
            ],
        )

        groups = inquiries.values_list("controlling_groups", flat=True)
        groups = [group for group in chain(*groups) if group not in current_group]

        return flatten(
            [
                self._get_responsible(instance, service)
                for service in Service.objects.filter(pk__in=groups)
            ]
        )

    def _get_recipients_involved_in_construction_step(self, instance):
        work_item = self.validated_data.get("work_item")
        case = self.validated_data.get("case")
        if not settings.CONSTRUCTION_MONITORING or (
            not work_item and not case
        ):  # pragma: no cover
            return []

        key = work_item.task.pk if work_item else case.workflow.pk
        notification_recipients_config = settings.CONSTRUCTION_MONITORING.get(
            "NOTIFICATION_RECIPIENTS", {}
        ).get(key, [])

        notify_service_ids = [
            config["service_id"]
            for config in notification_recipients_config
            # Notification should *not* be sent if an involvement is required
            # but the service has no inquiry. Otherwise, send it.
            if not config["require_involvement"]
            or instance.has_inquiry(config["service_id"])
        ]

        return flatten(
            [
                self._get_responsible(instance, service)
                for service in Service.objects.filter(pk__in=notify_service_ids)
            ]
        )

    def _get_recipients_invited_to_schlussabnahme_projekt(self, instance):
        work_item = self.validated_data.get("work_item")
        case = work_item.case
        if not settings.CONSTRUCTION_MONITORING or (
            not work_item and not case
        ):  # pragma: no cover
            return []

        if planning_work_item := case.work_items.filter(
            task_id=settings.CONSTRUCTION_MONITORING[
                "CONSTRUCTION_STEP_PLAN_SCHLUSSABNAHME_PROJEKT_TASK"
            ]
        ).first():
            service_ids = flatten(
                planning_work_item.document.answers.filter(
                    question_id="construction-step-schlussabnahme-projekt-planen-fachstellen"
                ).values_list("value", flat=True)
                or []
            )

            return flatten(
                [
                    self._get_responsible(instance, service)
                    for service in Service.objects.filter(pk__in=service_ids)
                ]
            )

    def _get_recipients_tax_administration(self, instance):
        service = Service.objects.filter(
            pk=settings.APPLICATION.get("TAX_ADMINISTRATION")
        ).first()
        if service:
            return self._get_responsible(instance, service)

        return []  # pragma: no cover

    def _get_recipients_aib(self, instance):
        service = Service.objects.filter(name="aib").first()
        if service:
            return [{"to": service.email}]
        return []  # pragma: no cover

    def _get_recipients_gvg(self, instance):
        service = Service.objects.filter(name="gvg").first()
        if service:
            return [{"to": service.email}]
        return []  # pragma: no cover

    def _get_recipients_abwasser_uri(self, instance):
        service = Service.objects.filter(name="AWU").first()
        if service:
            return [{"to": service.email}]
        return []  # pragma: no cover

    def _get_recipients_immissionsschutz_be(self, instance):
        return [{"to": "info.luft@be.ch"}]

    def _get_recipients_schnurgeruestabnahme_uri(self, instance):
        work_item = self.validated_data.get("work_item")

        # Do not use "instance" here because construction monitoring work items
        # are nested inside construction monitoring cases (bauetappen)
        construction_stage_planing_document = work_item.case.work_items.get(
            task_id=settings.CONSTRUCTION_MONITORING[
                "CONSTRUCTION_STEP_PLAN_CONSTRUCTION_STAGE_TASK"
            ]
        ).document
        relevant_answer_value = construction_stage_planing_document.answers.get(
            question_id="schnurgeruestabnahme-durch"
        ).value
        check_by_geometer = (
            relevant_answer_value
            == "wer-fuehrt-die-schnurgeruestabnahme-durch-geometer"
        )

        if check_by_geometer:
            service = Service.objects.filter(name="AGO (Geometer)").first()
            if service:
                return [{"to": service.email}]
            return []  # pragma: no cover
        else:
            return self._get_recipients_municipality(instance)

    def _get_recipients_geometer_uri(self, instance):
        service = Service.objects.filter(name="AGO (Geometer)").first()
        if service:
            return [{"to": service.email}]
        return []  # pragma: no cover

    def _get_recipients_fgs_uri(self, instance):
        service = Service.objects.filter(name="FGS").first()
        if service:
            return [{"to": service.email}]
        return []  # pragma: no cover

    def _get_recipients_abm_zs_uri(self, instance):
        service = Service.objects.filter(name="ABM ZS").first()
        if service:
            return [{"to": service.email}]
        return []  # pragma: no cover

    def _get_recipients_liegenschaftsschaetzung_uri(self, instance):
        service = Service.objects.filter(
            pk=uri_constants.AMT_FUER_STEUERN_LIEGENSCHAFTSSCHAETZUNG_SERVICE_ID
        ).first()
        if service:
            return [{"to": service.email}]
        return []  # pragma: no cover

    def _recipient_log(self, recipients):
        return ", ".join(
            [
                recipient["to"]
                or "" + (f" (CC: {recipient['cc']})" if "cc" in recipient else "")
                for recipient in recipients
            ]
        )

    def _receiver_type(self, recipient_type: str, language: str) -> str:
        receiver_type = RECIPIENT_TYPE_NAMES.get(recipient_type)

        if receiver_type:
            with translation.override(language):
                return f" ({translation.gettext(receiver_type)})"

        return ""

    def _post_send_unnotified_service(self, instance):
        Activation.objects.filter(
            circulation=self.validated_data.get("circulation"),
            email_sent=0,
            service_parent=self.context["request"].group.service,
        ).update(email_sent=1)

    def create(self, validated_data):
        subj_prefix = settings.EMAIL_PREFIX_SUBJECT
        body_prefix = settings.EMAIL_PREFIX_BODY
        special_forms_prefix = settings.EMAIL_PREFIX_BODY_SPECIAL_FORMS

        instance = validated_data["instance"]
        form_slug = CalumaApi().get_form_slug(instance)

        emails = []
        post_send = []

        connection = get_connection()

        for recipient_type in sorted(validated_data["recipient_types"]):
            recipients = getattr(self, "_get_recipients_%s" % recipient_type)(instance)
            subject = subj_prefix + validated_data["subject"]

            if (
                recipient_type != "applicant"
                and form_slug in settings.ECH_EXCLUDED_FORMS
            ):
                body = body_prefix + special_forms_prefix + validated_data["body"]
            else:
                body = body_prefix + validated_data["body"]

            for recipient in [r for r in recipients if r.get("to")]:
                emails.append(
                    EmailMessage(
                        subject=subject,
                        body=body,
                        connection=connection,
                        # EmailMessage needs "to" and "cc" to be lists
                        **{
                            k: [e.strip() for e in email.split(",")]
                            for (k, email) in recipient.items()
                            if email
                        },
                    )
                )

            post_send.append(
                getattr(self, f"_post_send_{recipient_type}", lambda instance: None)
            )

            # If no request context was provided to the serializer we assume the
            # mail delivery is part of a batch job initalized by the system
            # operation user.
            user = None

            if self.context:
                user = self.context["request"].user
            elif settings.APPLICATION.get("SYSTEM_USER"):
                user = User.objects.filter(
                    username=settings.APPLICATION.get("SYSTEM_USER")
                ).first()

            if not user:
                # This should be removed in the future since it's a really
                # strange fallback that does not really make sense in any case
                # to choose a random support user as sender. This can be removed
                # when the notifyoverdue command of UR is using the system user
                user = (
                    Role.objects.get(name__iexact="support")
                    .groups.order_by("group_id")
                    .first()
                    .users.first()
                )

            self._create_history_entry(
                instance, subject, body, recipients, recipient_type, user
            )

        self._send_mails(emails, connection)

        for fn in post_send:
            fn(instance)

        return len(emails)

    def _create_history_entry(
        self, instance, subject, body, recipients, recipient_type, user
    ):
        if settings.APPLICATION.get("LOG_NOTIFICATIONS"):
            receiver_emails = self._recipient_log(recipients)

            # Don't create history entries for notifications with no receivers (only for SZ)
            if (
                not settings.APPLICATION.get(
                    "LOG_NOTIFICATIONS_WITH_NO_RECEIVERS", True
                )
                and not receiver_emails
            ):
                return

            title = "Notifikation gesendet an {0} ({1})".format(
                receiver_emails, subject
            )

            if settings.APPLICATION.get("IS_MULTILINGUAL", False):
                if receiver_emails:
                    title = translation.gettext_noop(
                        "Notification sent to %(receiver_emails)s%(receiver_type)s (%(subject)s)"
                    )
                else:
                    title = translation.gettext_noop(
                        "Notification sent to no receivers%(receiver_type)s (%(subject)s)"
                    )

            create_history_entry(
                instance,
                user,
                title,
                lambda lang: {
                    "receiver_emails": receiver_emails,
                    "receiver_type": self._receiver_type(recipient_type, lang),
                    "subject": subject,
                },
                HistoryActionConfig.HISTORY_TYPE_NOTIFICATION,
                body,
            )

    def _send_mails(self, emails, connection):
        if emails:
            connection.open()
            exceptions = []
            for email in emails:
                try:
                    email.send()
                    logger.info(f'Sent email "{email.subject}" to {email.to}')
                except Exception as e:  # noqa: B902
                    exceptions.append((e, email))
            connection.close()

            if len(exceptions) > 0:
                error_msgs = "\n".join(
                    [
                        f"to {email.to}, cc {email.cc}: {str(exception)}"
                        for exception, email in exceptions
                    ]
                )
                logger.error(f"Failed to send {len(exceptions)} emails: {error_msgs}")

    class Meta:
        resource_name = "notification-template-sendmails"


class PermissionlessNotificationTemplateSendmailSerializer(
    NotificationTemplateSendmailSerializer
):
    """
    Send emails without checking for instance permission.

    This serializer subclasses NotificationTemplateSendmailSerializer and
    overloads the validate_instance method of the InstanceEditableMixin to
    disable permission checking the instance and allow anyone to send a email.
    """

    # Temporary pragma no cover, remove when publication permission endpoint is reenabled
    # revert !2353 to remove
    def validate_instance(self, instance):  # pragma: no cover
        return instance
