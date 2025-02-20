from caluma.caluma_core.visibilities import filter_queryset_for
from caluma.caluma_form import (
    historical_schema as historical_form_schema,
    schema as form_schema,
)
from caluma.caluma_user.visibilities import Authenticated
from caluma.caluma_workflow import models as workflow_models, schema as workflow_schema
from caluma.caluma_workflow.models import Case
from django.conf import settings
from django.db.models import Exists, ExpressionWrapper, F, OuterRef, Q, Value
from django.db.models.fields import BooleanField

from camac.caluma.utils import CamacRequest, visible_inquiries_expression
from camac.constants.kt_bern import DASHBOARD_FORM_SLUG
from camac.instance.filters import CalumaInstanceFilterSet
from camac.instance.mixins import InstanceQuerysetMixin
from camac.user.models import Role
from camac.user.permissions import permission_aware
from camac.utils import filters, order

from .. import ast_utils


def module_tasks(module_settings):
    return [
        task_id
        for key, task_id in module_settings.items()
        if key.endswith("_TASK") or key == "TASK"
    ]


def needs_visibility(module_settings, task_slugs):
    """Return True if the module needs visibility filtering.

    Visibility filtering needs to happen if the module is active and
    user is interested in tasks defined in the module settings.

    If user did not specify task slugs then we assume that user is interested in all tasks,
    including the ones from this module.
    """
    if not settings.APPLICATION.get("VISIBILITY_PERFORMANCE_OPTIMISATIONS_ACTIVE"):
        return bool(module_settings)

    if not module_settings:
        return False

    if task_slugs is None:
        return True

    return bool(set(task_slugs).intersection(set(module_tasks(module_settings))))


class CustomVisibility(Authenticated, InstanceQuerysetMixin):
    """Custom visibility for Kanton Bern.

    To avoid multiple db lookups, the result is cached in the
    request object, and reused if the need arises. Caching beyond a request is
    not done but might become a future optimisation.
    """

    instance_field = None
    suppress_visibilities = [
        "WorkItem.case",
        "WorkItem.child_case",
        "Case.document",
        "Case.parent_work_item",
        "Case.family",
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.group = None
        self.user = None
        self.request = None

    def filter_queryset(self, node, queryset, info):
        if not getattr(info.context, "camac_request", None):
            info.context.camac_request = CamacRequest(info).request

        self.request = info.context.camac_request

        return super().filter_queryset(node, queryset, info)

    @filter_queryset_for(form_schema.Question)
    @filter_queryset_for(form_schema.Form)
    @filter_queryset_for(form_schema.Option)
    @filter_queryset_for(form_schema.DynamicOption)
    def filter_queryset_public(self, node, queryset, info):
        # this is blueprint data which is uncritical and can be exposed publicly
        return queryset

    @permission_aware
    @filter_queryset_for(form_schema.Document)
    @filter_queryset_for(historical_form_schema.HistoricalDocument)
    def filter_queryset_for_document(self, node, queryset, info):
        instance_ids = self._all_visible_instances(info)

        return queryset.filter(
            # document is accessible through CAMAC ACLs
            Q(family__case__family__instance__pk__in=instance_ids)
            | Q(family__work_item__case__family__instance__pk__in=instance_ids)
            # OR dashboard documents
            | Q(form_id=DASHBOARD_FORM_SLUG)
            # OR unlinked table documents created by the requesting user
            | Q(
                family__case__isnull=True,
                family__work_item__isnull=True,
                family=F("pk"),
                created_by_user=info.context.user.username,
            )
        )

    def filter_queryset_for_document_for_public(self, node, queryset, info):
        return queryset.filter(
            family__case__family__instance__pk__in=self._all_visible_instances(info)
        )

    @permission_aware
    @filter_queryset_for(form_schema.Answer)
    @filter_queryset_for(historical_form_schema.HistoricalAnswer)
    def filter_queryset_for_answer(self, node, queryset, info):
        # return all answers, since answers can only be accessed via documents
        # which are already properly protected
        return queryset

    def filter_queryset_for_answer_for_public(self, node, queryset, info):
        # Scrub sensitive data from answer queryset for public users
        return queryset.exclude(
            question_id__in=settings.PUBLICATION.get("SCRUBBED_ANSWERS", [])
        )

    @filter_queryset_for(workflow_schema.Case)
    def filter_queryset_for_case(self, node, queryset, info):
        order_by = order(self.request)

        queryset = queryset.filter(
            family__instance__pk__in=self._all_visible_instances(info)
        )

        return queryset.order_by(*order_by) if order_by else queryset

    @permission_aware
    @filter_queryset_for(workflow_schema.WorkItem)
    def filter_queryset_for_work_items(self, node, queryset, info):
        if getattr(info.operation.name, "value", None) in [
            "WorkItemListQuery",
            "WorkItemsForTasks",
        ]:
            # performance optimization for the global work item list:
            # Only show work items with deadline where service is directly affected.
            # This is more efficient than going through _all_visible_instances
            # and returns a subset of those work items.
            service_id = str(self.request.group.service_id)
            filters = Q(deadline__isnull=False) & (
                Q(addressed_groups__contains=[service_id])
                | Q(controlling_groups__contains=[service_id])
            )
        else:
            filters = Q(
                case__family__instance__pk__in=self._all_visible_instances(info)
            )

        # Limit visible work items based on the task slugs in the GQL query
        # Consider only work items that user is interested in
        task_slugs = ast_utils.extract_tasks_from_filters(info)

        if needs_visibility(settings.DISTRIBUTION, task_slugs):
            # Provide additional filtering for inquiry work-items
            filters &= ~Q(task_id=settings.DISTRIBUTION["INQUIRY_TASK"]) | (
                Q(task_id=settings.DISTRIBUTION["INQUIRY_TASK"])
                & visible_inquiries_expression(self.request.group)
            )

        if needs_visibility(settings.ADDITIONAL_DEMAND, task_slugs):
            filters &= self.visible_additional_demands_expression(self.request.group)

        if needs_visibility(settings.CONSTRUCTION_MONITORING, task_slugs):
            queryset = queryset.annotate(
                is_construction_stage=ExpressionWrapper(
                    Q(
                        task__pk=settings.CONSTRUCTION_MONITORING[
                            "CONSTRUCTION_STAGE_TASK"
                        ]
                    ),
                    output_field=BooleanField(),
                ),
                is_construction_monitoring_control=ExpressionWrapper(
                    Q(
                        task__pk__in=[
                            settings.CONSTRUCTION_MONITORING[
                                "INIT_CONSTRUCTION_MONITORING_TASK"
                            ],
                            settings.CONSTRUCTION_MONITORING[
                                "COMPLETE_CONSTRUCTION_MONITORING_TASK"
                            ],
                        ]
                    ),
                    output_field=BooleanField(),
                ),
                is_construction_step=ExpressionWrapper(
                    Q(**{"meta__construction-step-id__isnull": False}),
                    output_field=BooleanField(),
                ),
            )

            filters &= self.visible_construction_step_work_items_expression(
                self.request.group
            )

        return queryset.filter(filters).distinct()

    def filter_queryset_for_work_items_for_public(self, node, queryset, info):
        return queryset.none()

    def _visible_instances_qs(self, info):
        """Return "all" visible instances, as a queryset.

        Also contains a performance improvement heuristic: If the GQL query
        contains a filter that identifies a single case or instance, we limit
        the "visible instances" such that only the selected instance id is
        returned. This greatly improves performance if you'd normally see a lot
        of instances.
        """
        qs = CalumaInstanceFilterSet(
            data=filters(self.request),
            queryset=self.get_queryset(),
            request=self.request,
        ).qs

        if info.path.typename == "Mutation":
            return qs

        # Apply heuristic: If there is a case ID or instance ID in
        # the query, we can narrow down the "visible instances" massively
        # to gain improved query performance
        filter_type, value = ast_utils.extract_case_from_filters(info)
        if filter_type == "case_id" and value:
            qs = qs.filter(
                case__family__in=Case.objects.filter(pk=value).values("family")
            )
        elif filter_type == "instance_id" and value:
            # Return both the instance itself and the source instances that it has
            # been copied from. This ensure that functions that depend on seeing
            # the source instances still work (such as eBau-Nr. suggestion), even if
            # the instance_id filter is set.
            # Whether the instances are actually visible in that moment, is checked
            # previously.
            qs = qs.filter(
                Q(pk=value) | Q(case__document__copies__case__instance=value)
            )

        if filter_type in ["case_id", "instance_id"] and value:
            qs._single_instance_mode = True

        return qs

    def _all_visible_instances(self, info):
        """Fetch visible camac instances and cache the result.

        Take user's group from a custom HTTP header named `X-CAMAC-GROUP` or use
        default group  to retrieve all Camac instance IDs that are accessible.

        Return a list of instance identifiers.
        """
        result = getattr(info.context, "_visibility_instances_cache", None)
        if result is not None:  # pragma: no cover
            return result

        instance_ids = self._visible_instances_qs(info)

        # For performance reasons we return the instance_ids as a list
        # if we are in single_instance_mode. Otherwise we return a queryset
        # which will be evaluated later and result in a subquery during query execution
        # Note: If the total amount of instances is less than a few thousand, this optimization
        # is not helping much (in fact, it potentially slows down list queries a little bit).
        # Currently this is only used for Kt. Uri.
        if getattr(
            instance_ids, "_single_instance_mode", False
        ) or not settings.APPLICATION.get(
            "VISIBILITY_PERFORMANCE_OPTIMISATIONS_ACTIVE"
        ):
            instance_ids = list(instance_ids.values_list("pk", flat=True))

        setattr(info.context, "_visibility_instances_cache", instance_ids)
        return instance_ids

    def visible_additional_demands_expression(self, group):
        if settings.APPLICATION.get("PORTAL_GROUP") == self.request.group.pk:
            return (
                # applicants see the base work item only if an additional demand has
                # been created
                ~Q(task_id=settings.ADDITIONAL_DEMAND["TASK"])
                | Q(
                    task_id=settings.ADDITIONAL_DEMAND["TASK"],
                    child_case__work_items__task_id=settings.ADDITIONAL_DEMAND[
                        "FILL_TASK"
                    ],
                )
            ) & (
                # applicants see additional work items addressed to the municipality (fill, check)
                # only if they are completed (i.e. sent)
                ~Q(
                    task_id__in=[
                        settings.ADDITIONAL_DEMAND["CHECK_TASK"],
                        settings.ADDITIONAL_DEMAND["SEND_TASK"],
                    ]
                )
                | Q(
                    task_id__in=[
                        settings.ADDITIONAL_DEMAND["CHECK_TASK"],
                        settings.ADDITIONAL_DEMAND["SEND_TASK"],
                    ],
                    status=workflow_models.WorkItem.STATUS_COMPLETED,
                )
            )
        else:
            # municipalities see the applicants' "fill" work item only after it has been
            # completed (i.e. sent to the municipality)
            return ~Q(task_id=settings.ADDITIONAL_DEMAND["FILL_TASK"]) | Q(
                task_id=settings.ADDITIONAL_DEMAND["FILL_TASK"],
                status=workflow_models.WorkItem.STATUS_COMPLETED,
            )

    @permission_aware
    def visible_construction_step_work_items_expression(
        self, group
    ):  # pragma: todo cover
        return (
            Q(is_construction_step=False)
            & Q(is_construction_stage=False)
            & Q(is_construction_monitoring_control=False)
        )

    def visible_construction_step_work_items_expression_for_applicant(
        self, group
    ):  # pragma: todo cover
        return (
            (
                # No construction-monitoring work-item (no additional filtering)
                Q(is_construction_step=False)
                & Q(is_construction_stage=False)
                & Q(is_construction_monitoring_control=False)
                & ~Q(task=settings.CONSTRUCTION_MONITORING["COMPLETE_INSTANCE_TASK"])
            )
            | (
                # Applicants see construction-step work-items addressed to them,
                # and those that have been completed by the municipality
                Q(is_construction_step=True)
                & (
                    Q(addressed_groups__contains=["applicant"])
                    | Q(status=workflow_models.WorkItem.STATUS_COMPLETED)
                )
            )
            | (
                # Applicants can see construction stages as soon as the planning
                # has been completed
                Q(
                    Exists(
                        workflow_models.WorkItem.objects.filter(
                            task_id=settings.CONSTRUCTION_MONITORING[
                                "CONSTRUCTION_STEP_PLAN_CONSTRUCTION_STAGE_TASK"
                            ],
                            status=workflow_models.WorkItem.STATUS_COMPLETED,
                            case__parent_work_item__pk=OuterRef("pk"),
                        )
                    ),
                    is_construction_stage=True,
                )
            )
        )

    def visible_construction_step_work_items_expression_for_municipality(self, group):
        return (
            # No construction-monitoring work-item (no additional filtering)
            Q(is_construction_step=False)
            & Q(is_construction_stage=False)
            & Q(is_construction_monitoring_control=False)
        ) | (
            # Construction-monitoring work-item addressed to current service
            Q(addressed_groups__contains=[str(group.service_id)])
        )

    def visible_construction_step_work_items_expression_for_trusted_service(
        self, group
    ):
        return Value(True)

    def visible_construction_step_work_items_expression_for_support(
        self, group
    ):  # pragma: todo cover
        return Value(True)

    def visible_construction_step_work_items_expression_for_coordination(self, group):
        return self.visible_construction_step_work_items_expression_for_municipality(
            group
        )


class CustomVisibilitySZ(CustomVisibility):
    """Custom visibility for Kanton Schwyz.

    Form visibility rules are defined in the form meta.
    Possible configurations are shown below.
    Note: If no visibility is configured for a form,
    the form is visible by default.

    Visible for internal roles:
    "meta": {
        "visibility": {
            "type": "internal"
        }
    },

    Publicly visible:
    "meta": {
        "visibility": {
            "type": "public"
        }
    },

    Visible for certain roles and services:
    * Request user needs to belong to one of the
    specified roles OR services:
    "meta": {
        "visibility": {
            "type": "specific",
            "visibleFor": {
                "roles": [3],
                "services": [6]
            }
        }
    },

    * Request user needs to belong to one of the specified
    roles, service is not relevant:
    "meta": {
        "visibility": {
            "type": "specific",
            "visibleFor": {
                "roles": [3, 4]
            }
        }
    },

    * Request user needs to belong to one of the specified
    services, role is not relevant:
    "meta": {
        "visibility": {
            "type": "specific",
            "visibleFor": {
                "services": [6, 7]
            }
        }
    },

    TODO: More generic future design
    To make the configurable visibility / permission feature
    usable for the other cantons, a few things need to be adapted:

    * Use "permissions" instead of "visibility" on form meta:

        "meta": {
            "createPermission": {
                "type": "public"
            }
        },

    * Read from and check permission config in instance serializer
        (create method), throw exception if permission is missing

    * Filter forms based on create permission in frontend
    """

    @filter_queryset_for(form_schema.Form)
    def filter_queryset_for_form(self, node, queryset, info):
        camac_role = self.request.group.role.role_id
        camac_service = self.request.group.service_id

        default_filter = Q(meta__visibility__isnull=True)

        # public filter: visible to all
        public_filter = Q(meta__visibility__type="public")

        # internal filter: excludes configured public roles
        public_roles = [
            role.role_id
            for role in Role.objects.filter(
                name__in=settings.APPLICATION.get("PUBLIC_ROLES", [])
            )
        ]
        internal_forms = (
            [form.slug for form in queryset.filter(meta__visibility__type="internal")]
            if camac_role not in public_roles
            else []
        )
        internal_filter = Q(slug__in=internal_forms)

        # specific filter: visible to specific roles or services
        specific_filter = Q(
            meta__visibility__type="specific",
            meta__visibility__visibleFor__roles__contains=[camac_role],
        ) | Q(
            meta__visibility__type="specific",
            meta__visibility__visibleFor__services__contains=[camac_service],
        )

        return queryset.filter(
            default_filter | public_filter | internal_filter | specific_filter
        )

    @filter_queryset_for(form_schema.Question)
    @filter_queryset_for(form_schema.Option)
    @filter_queryset_for(form_schema.DynamicOption)
    def filter_queryset_public(self, node, queryset, info):
        # this is blueprint data which is uncritical and can be exposed publicly
        return queryset  # pragma: no cover


class CustomVisibilityBE(CustomVisibility):
    pass
