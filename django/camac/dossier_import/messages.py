from dataclasses import dataclass, field
from enum import Enum
from typing import List, Literal, Union

from dataclasses_json import dataclass_json
from django.utils.translation import gettext as _

from camac.document.models import Attachment
from camac.instance.models import Instance


@dataclass_json
@dataclass(order=True)
class Message:
    level: int
    code: str
    detail: Union[list, str, dict]


@dataclass_json
@dataclass
class FieldValidationMessage(Message):
    field: str


@dataclass_json
@dataclass
class DossierSummary:
    status: str  # one of success, warning, error
    dossier_id: str
    details: List[Message]


@dataclass_json
@dataclass
class Summary:
    stats: dict = field(default_factory=dict)
    warning: dict = field(default_factory=list)
    error: dict = field(default_factory=list)


class Severity(int, Enum):
    """Levels of verbosity.

    More is less.
    """

    DEBUG = 0
    INFO = 1
    WARNING = 2
    ERROR = 3


# import status categories
DOSSIER_IMPORT_STATUS_SUCCESS = "success"
DOSSIER_IMPORT_STATUS_WARNING = "warning"
DOSSIER_IMPORT_STATUS_ERROR = "error"


class MessageCodes(str, Enum):
    """Message codes that are used for identifying messages.

    NOTE: Only codes listed here are included in summary reports.

    These listed here are for convenience and uniformity.

    Loaders may have further message codes that are dynamically
    constructed on field validation. E. g.:
     - "submit-date-validation-error" for Dossier.submit_date

    """

    # info
    INSTANCE_CREATED = "instance-created"
    FORM_DATA_WRITTEN = "form-data-written"
    ATTACHMENTS_WRITTEN = "attachments-written"
    ATTACHMENT_UPDATED = "attachment-updated"
    ATTACHMENT_CREATED = "attachment-created"
    SET_WORKFLOW_STATE = "workflow-state-set"
    UNDO_IMPORT_TRIGGERED = "undo-import-triggered"
    UPDATE_DOSSIER = "update-dossier"
    VALUE_DELETED = "value-deleted"

    # warnings
    DUPLICATE_DOSSIER = "duplicate-dossier"
    DATE_FIELD_VALIDATION_ERROR = "date-field-validation-error"
    STATUS_CHOICE_VALIDATION_ERROR = "status-choice-validation-error"
    MISSING_REQUIRED_VALUE_ERROR = "missing-required-field-error"
    DUPLICATE_IDENTFIER_ERROR = "duplicate-identifier-error"
    FIELD_VALIDATION_ERROR = "field-validation-error"
    MIME_TYPE_UNKNOWN = "mime-type-unknown"
    MIME_TYPE_INVALID = "mime-type-invalid"
    WORKFLOW_SKIP_ITEM_FAILED = "skip-workitem-failed"  # not user-facing
    INCONSISTENT_WORKFLOW_STATE = "inconsistent-workflow-state"  # trying to write dates that should not exist at the current state of workflow
    WRITING_READ_ONLY_FIELD = (
        "writing-read-only-field"  # on existing instance when reimporting
    )
    DELETION_HAS_NO_EFFECT = "deletion-has-no-effect"  # e.g. the value wasn't set or the work item does not exist

    # errors
    TASK_TIMED_OUT = "task-timed-out"
    UNHANDLED_EXCEPTION = "unhandled-exceptions"


def get_message_max_level(message_list: List[Message], default=Severity.DEBUG.value):
    return (
        message_list
        and sorted(message_list, key=lambda msg: msg.level, reverse=True)[0].level
    ) or default


def aggregate_messages_by_level(message_object: dict, level: str) -> list:
    r"""Aggregate dossier messages by level grouped by message.

    The message_object is a collection of dossier summaries:
    {
        "details": [
            {
                "dossier_id": 123,
                "status": "warning",
                "details": [
                    {
                        "code": "date-field-validation-error",
                        "detail": "not a date",
                        "field": "SUBMIT-DATE",
                        "level": 2,  # warning
                    },
                    ...
                ]
            }
        ],
        ...

    }

    The result is a list of messages counting the occurrence of a message and listing
    affected dossier-ids. E. g.
    [
        ...
        '2 Dossiers haben ein ungültiges Datum. Datumsangaben bitte im Format "DD.MM.YYYY" (z.B. "13.04.2021") machen. Betroffene Dossiers:\n2017-84: \'2-2-2\' (submit-date)',
        '3 Dossiers ...',
        ...
    ]

    """
    result = []
    if message_object:
        for code in MessageCodes:
            filtered_summaries = []
            for dossier_detail in message_object["details"]:
                messages = list(
                    filter(
                        lambda x: x["level"] == level and x["code"] == code.value,
                        dossier_detail["details"],
                    )
                )
                if messages:
                    filtered_summaries.append(
                        {
                            "dossier_id": dossier_detail["dossier_id"],
                            "messages": messages,
                        }
                    )

            if filtered_summaries:
                result.append(compile_message_for_code(code, filtered_summaries))
    return sorted(result)


def compile_message_for_code(code, filtered_summaries):
    """Return a formatted message for a given error code.

    Filtered messages is list of dictionaries:
    [{
      "dossier_id": 123,
      "messages": [
        "code": "date-field-validation-error",
        "detail": "not a date",
        "field": "SUBMIT-DATE",
        "level": 2,  # warning
      ]
    }]
    """
    messages = {
        MessageCodes.DUPLICATE_DOSSIER.value: _("have the same ID"),
        MessageCodes.DATE_FIELD_VALIDATION_ERROR.value: _(
            'have an invalid value in date field. Please use the format "DD.MM.YYYY" (e.g. "13.04.2021")'
        ),
        MessageCodes.STATUS_CHOICE_VALIDATION_ERROR.value: _("have an invalid status"),
        MessageCodes.UPDATE_DOSSIER.value: _("will be updated"),
        MessageCodes.VALUE_DELETED.value: _("cleared values"),
        MessageCodes.ATTACHMENT_UPDATED.value: _("updated attachments"),
        MessageCodes.MISSING_REQUIRED_VALUE_ERROR.value: _(
            "miss a value in a required field"
        ),
        MessageCodes.DUPLICATE_IDENTFIER_ERROR.value: _("don't have a unique ID"),
        MessageCodes.FIELD_VALIDATION_ERROR.value: _("have an invalid value"),
        MessageCodes.MIME_TYPE_UNKNOWN.value: _(
            "have at least one document with an unknown file type"
        ),
        MessageCodes.INCONSISTENT_WORKFLOW_STATE.value: _(
            "have an inconsistent workflow state"
        ),
    }

    def format_message(message):
        if message.get("detail") and message.get("field"):
            return f"{message['detail']} ({message.get('field')})"
        if message.get("detail"):
            return f"{message.get('detail')}"
        return message.get("field")

    def format_summary(summary: dict) -> str:
        entries = ", ".join(
            [
                format_message(message)
                for message in summary["messages"]
                if format_message(message)
            ]
        )
        return (
            f"{summary['dossier_id']}: {entries}" if entries else summary["dossier_id"]
        )

    entries = [
        format_summary(summary)
        for summary in sorted(
            filtered_summaries, key=lambda entry: str(entry["dossier_id"])
        )
    ]

    return _("%(count)i dossiers %(message)s. Affected dossiers:\n%(entries)s") % dict(
        count=len(filtered_summaries),
        message=messages.get(code, ""),
        entries="\n".join(entries),
    )


def update_summary(dossier_import):
    validation_message_object = dossier_import.messages.get("validation")
    if validation_message_object:
        if not validation_message_object.get("summary"):  # pragma: no cover
            validation_message_object["summary"] = Summary().to_dict()
        validation_message_object["summary"]["warning"] += aggregate_messages_by_level(
            validation_message_object, Severity.WARNING.value
        )
        validation_message_object["summary"]["error"] += aggregate_messages_by_level(
            validation_message_object, Severity.ERROR.value
        )
        if validation_details := validation_message_object.get("details"):
            validation_message_object["details"] = sorted(
                validation_details, key=lambda x: str(x["dossier_id"])
            )
        dossier_import.messages["validation"] = validation_message_object
        dossier_import.save()

    import_message_object = dossier_import.messages.get("import")
    if import_message_object:
        if not import_message_object.get("summary"):  # pragma: no cover
            import_message_object["summary"] = Summary().to_dict()
        import_message_object["summary"]["warning"] += aggregate_messages_by_level(
            import_message_object, Severity.WARNING.value
        )
        import_message_object["summary"]["error"] += aggregate_messages_by_level(
            import_message_object, Severity.ERROR.value
        )
        import_message_object["summary"]["stats"].update(
            {
                "dossiers": Instance.objects.filter(
                    **{"case__meta__import-id": str(dossier_import.pk)}
                ).count(),
                "documents": Attachment.objects.filter(
                    **{"instance__case__meta__import-id": str(dossier_import.pk)}
                ).count(),
                "updated": Instance.objects.filter(
                    **{"case__meta__updated-with-import": str(dossier_import.pk)}
                ).count(),
            }
        )
        if import_details := import_message_object.get("details"):
            import_message_object["details"] = sorted(
                import_details, key=lambda x: str(x["dossier_id"])
            )
        dossier_import.messages["import"] = import_message_object
        dossier_import.save()
    return dossier_import


def append_or_update_dossier_message(
    dossier_id, field_name, detail, code, messages, level=Severity.WARNING.value
):
    dossier_msg = next(
        (d for d in messages if d.dossier_id == dossier_id),
        None,
    )
    if not dossier_msg:
        dossier_msg = DossierSummary(
            status=DOSSIER_IMPORT_STATUS_SUCCESS,
            details=[],
            dossier_id=dossier_id,
        )
        messages.append(dossier_msg)
    dossier_msg.details.append(
        FieldValidationMessage(
            code=code,
            level=level,
            field=field_name,
            detail=detail,
        )
    )


Sections = ["validation", "import"]


def update_messages_section_detail(
    message: DossierSummary,
    section: Literal[Sections],
    dossier_import,
):
    """Update DossierImport.messages with dossier message detail.

    This is to avoid overwriting previous messages on the current dossier with
    new messages.

    message: an instance of DossierSummary. It carries a unique dossier_id, the
        status of the dossier based on validation results and a set of details
        that is a list of messages regarding that dossier
        (field validations etc.)

    dossier_import: an instance of camac.dossier_import.models.DossierImport

    section: to what section of the messages should it be appended.

    The input message is transformed to a dictionary that can be saved to the
    DossierImport.messages field.
    """
    if section not in Sections:  # pragma: no cover
        raise ValueError(f"`section` must be one of {', '.join(Sections)}.")
    message_exists = next(
        (
            d
            for d in dossier_import.messages[section]["details"]
            if d["dossier_id"] == message.dossier_id
        ),
        None,
    )
    if not message_exists:
        message_exists = message.to_dict()
        dossier_import.messages[section]["details"].append(message_exists)
    message_exists.update(message.to_dict())


def default_messages_object():
    return {
        "import": {"details": [], "summary": Summary().to_dict(), "completed": None},
        "validation": {
            "details": [],
            "summary": Summary().to_dict(),
            "completed": None,
        },
    }
