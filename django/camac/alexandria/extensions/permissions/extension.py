import datetime
import json
from typing import Union

from alexandria.core.models import BaseModel, Category, Document, File, Mark, Tag
from django.conf import settings
from django.core.validators import EMPTY_VALUES
from generic_permissions.permissions import object_permission_for, permission_for

from camac.alexandria.extensions.common import get_role
from camac.alexandria.extensions.permissions import conditions, scopes
from camac.instance.models import Instance
from camac.permissions.api import PermissionManager
from camac.permissions.switcher import permission_switching_method
from camac.utils import get_dict_item


def resolve_permissions(category, group):
    if category.parent:
        return resolve_permissions(category.parent, group)

    return get_dict_item(category.metainfo, f"access.{get_role(group)}", default=None)


MODE_CREATE = "create"
MODE_UPDATE = "update"
MODE_DELETE = "delete"


class CustomPermission:
    @permission_switching_method
    def has_base_permission(self, request, instance):
        manager = PermissionManager.from_request(request)

        # This is a very simple permission check as we do specific permission
        # check without the permissions module. As soon as GR fully integrates
        # the permissions module, we should find a way to combine the two
        # permission mechanisms or even drop the current one.
        return manager.has_all(instance, "documents-write")

    @has_base_permission.register_old
    def _has_base_permission(self, request, instance):
        manager = PermissionManager.from_request(request)

        # In GR the permission module is not fully activated but only used for
        # read only permissions. As this permission assigns a "documents-read"
        # permission but all the other "old" permissions don't have any
        # permissions, we need to check whether the "documents-read" is assigned
        # and then explicitly prohibit any writing actions in alexandria.
        return not manager.has_any(instance, "documents-read")

    def get_needed_permissions(self, request, document=None) -> set:
        if request.method == "POST":
            used_permissions = {MODE_CREATE}
            for key, value in request.parsed_data.items():
                if (
                    key in settings.ALEXANDRIA["RESTRICTED_FIELDS"]
                    and value not in EMPTY_VALUES
                ):
                    used_permissions.add(f"{MODE_CREATE}-{key}")

            return used_permissions
        elif request.method == "PATCH":
            return self.get_needed_patch_permissions(request, document)
        elif request.method == "DELETE":
            return {MODE_DELETE}

        return set()  # pragma: no cover

    def get_needed_patch_permissions(self, request, document) -> set:  # noqa: C901
        used_permissions = {MODE_UPDATE}
        for key in settings.ALEXANDRIA["RESTRICTED_FIELDS"]:
            if key not in request.parsed_data:
                continue

            old_value = getattr(document, key)
            new_value = request.parsed_data.get(key)

            # DateField
            if isinstance(old_value, datetime.date):
                new_value = datetime.datetime.fromisoformat(new_value).date()
            # ForeignKey
            elif isinstance(old_value, Category):
                old_value = old_value.pk
                new_value = new_value["id"]
            # ManyToManyField
            elif hasattr(old_value, "values_list"):
                # can be a set because ids are unique
                old_value = set(
                    [str(v) for v in old_value.values_list("pk", flat=True)]
                )
                new_value = set([item["id"] for item in new_value])

            if old_value != new_value:
                used_permissions.add(f"{MODE_UPDATE}-{key}")

                if key == "marks":
                    added_marks = new_value - old_value
                    removed_marks = old_value - new_value
                    used_permissions.update(
                        {
                            f"{MODE_UPDATE}-{key}-{slug}"
                            for slug in added_marks.union(removed_marks)
                        }
                    )

        return used_permissions

    def get_available_permissions(
        self,
        request,
        instance: Instance,
        category: Category,
        document: Union[Document, None] = None,
    ) -> set:
        category_permissions = resolve_permissions(category, request.group)

        if not category_permissions or "permissions" not in category_permissions:
            return set()

        available_permissions = set()

        if not self.has_base_permission(request, instance):
            return set()

        for permission in category_permissions["permissions"]:
            all_checks_met = True

            if document and permission["permission"] != "create":
                all_checks_met &= getattr(scopes, permission["scope"])(
                    request.group, document
                ).evaluate()

            required_conditions = permission.get("condition")
            if required_conditions and all_checks_met:
                for condition, value in required_conditions.items():
                    negated = condition.startswith("~")
                    result = getattr(conditions, condition.lstrip("~"))(
                        value, instance, request, document
                    ).evaluate()
                    all_checks_met = not result if negated else result

                    if not all_checks_met:
                        break

            if all_checks_met:
                fields = permission.get(
                    "fields", settings.ALEXANDRIA["RESTRICTED_FIELDS"]
                )

                available_permissions.add(permission["permission"])
                for field in fields:
                    available_permissions.add(f"{permission['permission']}-{field}")

                    if field == "marks":
                        available_permissions.update(
                            {
                                f"{permission['permission']}-{field}-{slug}"
                                for slug in permission.get(
                                    "marks", Mark.objects.values_list("pk", flat=True)
                                )
                            }
                        )

        return available_permissions

    @permission_for(BaseModel)
    @object_permission_for(BaseModel)
    def has_permission_default(self, request, document=None):  # pragma: no cover
        return get_role(request.group) == "support"

    @permission_for(Document)
    @object_permission_for(Document)
    def has_permission_for_document(self, request, document=None):
        if document is not None:
            # On update and delete we can get the needed data from the database
            instance = document.instance_document.instance
            category = document.category
            request.parsed_data = request.data
        elif request.method == "POST":
            if "data" in request.data:
                # On creation we don't have any data in the database yet. Therefore
                # we need to get the needed data from the request.
                data = json.loads(request.data["data"].read().decode("utf-8"))
                instance = Instance.objects.get(
                    pk=data["metainfo"]["camac-instance-id"]
                )
                category = Category.objects.get(pk=data["category"])
                request.parsed_data = data
                request.data["data"].seek(0)
            else:
                document = Document.objects.get(
                    pk=request.parser_context["kwargs"]["pk"]
                )
                instance = document.instance_document.instance
                category = document.category
                request.parsed_data = request.data
        else:
            # If there is no document, we called `permission_for` which can be
            # ignored for update and delete requests as `object_permission_for`
            # will be called afterwards and will execute the branch above.
            return True

        # analyze category to figure out available permissions
        available_permissions = self.get_available_permissions(
            request, instance, category, document
        )

        # short circuit if no permissions are available
        if not available_permissions:
            return False

        # analyze request to figure out needed permissions
        needed_permissions = self.get_needed_permissions(request, document)

        # if the category changed, we need to check whether we are allowed to
        # create a document in the new category as well
        new_category_id = get_dict_item(
            request.parsed_data, "category.id", default=None
        )
        if new_category_id and category.pk != new_category_id:
            new_category = Category.objects.get(pk=new_category_id)

            available_permissions_new_category = self.get_available_permissions(
                request, instance, new_category, document
            )
            needed_permissions_new_category = {MODE_CREATE}

            # if the document already has marks, we need to make sure that the
            # new category allows marks
            if document.marks.exists():
                needed_permissions_new_category.add(f"{MODE_UPDATE}-marks")

            if not needed_permissions_new_category.issubset(
                available_permissions_new_category
            ):
                return False

        # check if needed permissions are subset of available permissions
        return needed_permissions.issubset(available_permissions)

    @permission_for(File)
    def has_permission_for_file(self, request):
        document = Document.objects.get(pk=request.data["document"])

        # replacement files can only be created by same organization
        if (
            settings.APPLICATION_NAME == "kt_gr"
            and request.method == "POST"
            and document.files.filter(variant=File.Variant.ORIGINAL).count() >= 1
            and not scopes.ServiceAndSubservice(request.group, document).evaluate()
        ):
            return False

        available_permissions = self.get_available_permissions(
            request, document.instance_document.instance, document.category, document
        )

        if not available_permissions:
            return False

        needed_permissions = {"create-files"}
        return needed_permissions.issubset(available_permissions)

    @object_permission_for(File)
    def has_object_permission_for_file(self, request, file):  # pragma: no cover
        # patch or delete not allowed
        return False

    @permission_for(Tag)
    @object_permission_for(Tag)
    def has_permission_for_tag(self, request, tag=None):
        role = get_role(request.group)

        if role == "support":
            # Support can create, edit and delete tags
            return True
        elif role not in ["public", "applicant"] and tag is None:
            # Internal roles can create tags
            return True
        elif role not in ["public", "applicant"] and tag is not None:
            # Internal roles can only edit and delete own tags
            return tag.created_by_group == str(request.group.service_id)

        return False
