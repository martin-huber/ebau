from dataclasses import dataclass, field
from operator import attrgetter

from caluma.caluma_form import models as form_models
from caluma.caluma_form.validators import DocumentValidator
from dateutil.parser import ParserError, parse as dateutil_parse
from django.conf import settings
from django.utils.translation import get_language

from camac.core.models import MultilingualModel
from camac.utils import get_dict_item


@dataclass
class MasterData(object):
    case: object
    visible_questions: dict = field(default_factory=dict)
    validation_context: dict = field(default_factory=dict)
    disable_answer_visibility: bool = field(default=False)

    def __getattr__(self, lookup_key):
        config = get_dict_item(
            settings.MASTER_DATA, f"CONFIG.{lookup_key}", default=None
        )

        if not config:
            raise AttributeError(
                f"Key '{lookup_key}' is not configured in master data config. Available keys are: {', '.join(settings.MASTER_DATA['CONFIG'].keys())}"
            )

        resolver, *args = config
        fn = getattr(self, f"{resolver}_resolver", None)

        if not fn:
            raise AttributeError(
                f"Resolver '{resolver}' used in key '{lookup_key}' is not defined in master data class"
            )

        if len(args) == 0:
            return fn()

        lookup = args[0]
        kwargs = args[1] if len(args) > 1 else {}

        return fn(lookup, **kwargs)

    def _parse_value(
        self, value, default=None, value_parser=None, answer=None, **kwargs
    ):
        if not value_parser or not value:
            return value if value else default

        options = {}

        if isinstance(value_parser, tuple):
            parser_name, options = value_parser
        else:
            parser_name = value_parser

        parser = getattr(self, f"{parser_name}_parser", None)

        if not parser:
            raise AttributeError(
                f"Parser '{parser_name}' is not defined in master data class"
            )

        return parser(value, default=default, answer=answer, **options, **kwargs)

    def _get_cell_value(self, row, lookup_config):
        options = {}

        if lookup_config == "pk":
            return str(row.pk)
        elif isinstance(lookup_config, tuple):
            lookup, options = lookup_config
        else:
            lookup = lookup_config

        return self.answer_resolver(lookup, document=row, **options)

    def _get_ng_cell_value(self, row, lookup_config):
        options = {}

        if isinstance(lookup_config, tuple):
            lookup, options = lookup_config
            if lookup == "static":
                return options
        else:
            lookup = lookup_config

        return self._parse_value(row.get(lookup), **options)

    def _answer_is_visible(self, answer):
        if self.disable_answer_visibility:
            return True
        visible_questions = self.visible_questions.get(answer.document.pk)

        if visible_questions is None:
            document = answer.document

            if document.pk != document.family_id:
                document = document.family

            if not self.validation_context.get(document.pk, None):
                self.validation_context[document.pk] = (
                    DocumentValidator()._validation_context(document)
                )

            visible_questions = DocumentValidator().visible_questions(
                answer.document, self.validation_context[document.pk]
            )
            self.visible_questions[answer.document_id] = visible_questions

        return answer.question_id in visible_questions

    def static_resolver(self, value):
        """Resolve static value for a master data key.

        Example configuration for a static value:

        MASTER_DATA = {
            "demo": {
                "CONFIG": {
                    "some_string": ("static", "my-string")
                }
            }
        }
        """
        return value

    def form_name_resolver(self):
        return self.case.document.form.name.translate()

    def answer_resolver(
        self,
        lookup,
        value_key="value",
        document=None,
        document_from_work_item=None,
        **kwargs,
    ):
        """Resolve data from caluma answers.

        Example configuration for a "normal" value:

        MASTER_DATA = {
            "demo": {
                "CONFIG": {
                    "some_string": (
                        "answer",
                        # question slug of the answer, can also be multiple
                        "my-string"
                    )
                }
            }
        }

        Example configuration for a date value:

        MASTER_DATA = {
            "demo": {
                "CONFIG": {
                    "some_date": (
                        "answer",
                        "my-date",
                        {
                            "value_key": "date",
                            "default": datetime.date(2021, 8, 13)
                        }
                    )
                }
            }
        }

        Example configuration for a choice question:

        MASTER_DATA = {
            "demo": {
                "CONFIG": {
                    "some_choice": (
                        "answer",
                        "my-choice",
                        {
                            "value_parser": (
                                {
                                    "mapping": {
                                        "my-choice-yes": True,
                                        "my-choice-no": False,
                                    }
                                }
                            ),
                            "default": False
                        }
                    )
                }
            }
        }
        """
        if not isinstance(lookup, list):
            lookup = [lookup]

        if not document and document_from_work_item:
            work_item = next(
                filter(
                    lambda work_item: work_item.task_id == document_from_work_item,
                    self.case.work_items.all(),
                ),
                None,
            )
            document = work_item.document if work_item else None
        elif not document:
            document = self.case.document

        answer = next(
            filter(
                lambda answer: answer.question_id in lookup
                and self._answer_is_visible(answer),
                document.answers.all() if document else [],
            ),
            None,
        )

        return self._parse_value(
            getattr(answer, value_key, None) if answer else None,
            answer=answer,
            **kwargs,
        )

    def case_meta_resolver(self, lookup, **kwargs):
        """Resolve data from the case meta.

        Example configuration:

        MASTER_DATA = {
            "demo": {
                "CONFIG": {
                    "identifier": (
                        "case_meta",
                        "some-date",
                        {
                            "value_parser": "date"
                        }
                    )
                }
            }
        }
        """
        return self._parse_value(self.case.meta.get(lookup), **kwargs)

    def table_resolver(self, lookup, column_mapping={}, **kwargs):
        """Resolve data from caluma table answers.

        Example configuration:

        MASTER_DATA = {
            "demo": {
                "CONFIG": {
                    "applicant": (
                        "table",
                        "applicant",
                        {
                            "column_mapping": {
                                "first_name": "first-name",
                                "last_name": "last-name",
                                "is_juristic_person": (
                                    "is-juristic-person",
                                    {
                                        "value_parser": (
                                            "value_mapping",
                                            {
                                                "mapping": {
                                                    "is-juristic-person-yes": True,
                                                    "is-juristic-person-no": False,
                                                }
                                            }
                                        )
                                    }
                                )
                            }
                        }
                    )
                }
            }
        }
        """
        answer_documents = self.answer_resolver(
            lookup,
            "answerdocument_set",
            default=form_models.Document.objects.none(),
            **kwargs,
        )

        return [
            {
                key: self._get_cell_value(answer_document.document, lookup_config)
                for key, lookup_config in column_mapping.items()
            }
            for answer_document in reversed(
                sorted(
                    answer_documents.all(),
                    key=lambda answer_document: answer_document.sort,
                )
            )
        ]

    def baukontrolle_resolver(self, lookup, column_mapping={}, **kwargs):
        """Find a specific date from the "Baukontrolle" caluma table.

        This goes through all rows and returns the first non-empty value.

        Example configuration:

        MASTER_DATA = {
            "final_approval_date": (
                "baukontrolle", "baukontrolle-realisierung-schlussabnahme"
            }
        }
        """

        rows = self.table_resolver(
            "baukontrolle-realisierung-table",
            column_mapping={"value": (lookup, {"value_key": "date"})},
            document_from_work_item="building-authority",
        )
        if len(rows) == 0:
            return None

        return next((r["value"] for r in rows if r["value"]), None)

    def first_workflow_entry_resolver(self, lookup, default=None, **kwargs):
        """Resolve data from the first workflow entry.

        Example configuration:

        MASTER_DATA = {
            "demo": {
                "CONFIG": {
                    "submit_date": (
                        "first_workflow_entry",
                        # IDs of the workflow items
                        [10]
                    )
                }
            }
        }
        """
        entry = next(
            filter(
                lambda entry: entry.workflow_item_id in lookup,
                self.case.instance.workflowentry_set.all(),
            ),
            None,
        )

        return self._parse_value(entry.workflow_date if entry else default, **kwargs)

    def last_workflow_entry_resolver(self, lookup, default=None, **kwargs):
        """Resolve data from the last workflow entry.

        Example configuration:

        MASTER_DATA = {
            "demo": {
                "CONFIG": {
                    "submit_date": (
                        "last_workflow_entry",
                        # ID of the workflow item, can also be multiple
                        10
                    )
                }
            }
        }
        """
        if not isinstance(lookup, list):
            lookup = [lookup]  # pragma: no cover

        entries = list(
            filter(
                lambda entry: entry.workflow_item_id in lookup,
                self.case.instance.workflowentry_set.all(),
            )
        )

        entry = max(entries, key=lambda entry: entry.group, default=default)
        return self._parse_value(entry.workflow_date if entry else default, **kwargs)

    def php_answer_resolver(self, lookup, default=None, **kwargs):
        """Resolve data from old school camac answers.

        Example configuration:

        MASTER_DATA = {
            "demo": {
                "CONFIG": {
                    "some_string": (
                        "php_answer",
                        # question ID
                        123
                    )
                }
            }
        }
        """
        answer = next(
            filter(
                lambda answer: answer.question_id == lookup,
                self.case.instance.answers.all(),
            ),
            None,
        )

        return self._parse_value(answer.answer if answer else default, **kwargs)

    def ng_answer_resolver(self, lookup, default=None, **kwargs):
        """Resolve data from camac-ng fields.

        Example configuration for a "normal" value:

        MASTER_DATA = {
            "demo": {
                "CONFIG": {
                    "some_string": (
                        "ng_answer",
                        # name of the field
                        "my-field"
                    )
                }
            }
        }

        Example configuration for a value with a potential override:

        MASTER_DATA = {
            "demo": {
                "CONFIG": {
                    "some_string": (
                        "ng_answer",
                        # name of the field and override field
                        ["my-field", "my-field-override"],
                    )
                }
            }
        }
        """
        lookup_previous = None
        if isinstance(lookup, list):
            *lookup_previous, lookup = lookup

        field = next(
            filter(
                lambda field: field.name == lookup,
                self.case.instance.fields.all(),
            ),
            None,
        )

        if not field and lookup_previous:
            field = next(
                filter(
                    lambda field: field.name in lookup_previous,
                    self.case.instance.fields.all(),
                ),
                None,
            )

        parsed_value = self._parse_value(field.value if field else None, **kwargs)
        return parsed_value if parsed_value else default

    def ng_table_resolver(self, lookup, column_mapping={}, **kwargs):
        """Resolve data from camac-ng table fields.

        Example configuration for a camac-ng table with potential table override:

        MASTER_DATA = {
            "demo": {
                "CONFIG": {
                    "applicant": (
                        "ng_table",
                        ["bauherrschaft", "bauherrschaft-override"],
                        {
                            "column_mapping": {
                                "last_name": "name",
                                "first_name": "vorname",
                                "street": "strasse",
                                "zip": "plz",
                                "town": "ort",
                                "is_juristic_person": (
                                    "anrede",
                                    {
                                        "value_parser": (
                                            "value_mapping",
                                            {
                                                "mapping": {
                                                    "Herr": False,
                                                    "Frau": False,
                                                    "Firma": True,
                                                }
                                            }
                                        )
                                    }
                                )
                            }
                        }
                    )
                }
            }
        }

        Example configuration for a camac-ng table with list value:

        MASTER_DATA = {
            "demo": {
                "CONFIG": {
                    "buildings": (
                        "ng_table",
                        "gwr-v2",
                        {
                            "column_mapping": {
                                "name": "gebaeudebezeichnung",
                                "dwellings": (
                                    "wohnungen",
                                    {
                                        "value_parser": (
                                            "list_mapping",
                                            {
                                                "mapping": {
                                                    "location_on_floor": "lage",
                                                }
                                            }
                                        )
                                    }
                                )
                            }
                        }
                    )
                }
            }
        }
        """
        return [
            {
                key: self._get_ng_cell_value(row, lookup_config)
                for key, lookup_config in column_mapping.items()
            }
            for row in self.ng_answer_resolver(lookup, default=[])
        ]

    def instance_property_resolver(self, lookup):
        """Take a lookup path to the property to return final value.

        '__' separate nested properties

        If the target hits a MultilingualModel value the name is translated
        with `get_name`.

        """
        lookup_attr_of = attrgetter(lookup.replace("__", "."))

        try:
            value = lookup_attr_of(self.case.instance)
        except AttributeError as e:
            raise AttributeError(
                f"Instance property lookup failed for lookup `{lookup}` with {e}."
            )

        if isinstance(value, MultilingualModel):
            value = value.get_name()
        return value

    def datetime_parser(self, value, default, **kwargs):
        try:
            return dateutil_parse(value)
        except ParserError:  # pragma: no cover
            return default

    def date_parser(self, value, default, **kwargs):
        try:
            return dateutil_parse(value).date()
        except ParserError:  # pragma: no cover
            return default

    def value_mapping_parser(self, value, default, mapping={}, **kwargs):
        if isinstance(value, list):
            return [
                self.value_mapping_parser(v, default, mapping=mapping) for v in value
            ]

        return mapping.get(value, default)

    def list_mapping_parser(self, value, default, mapping={}, **kwargs):
        return [
            {
                key: (
                    self._parse_value(
                        next(
                            filter(
                                None,
                                (
                                    item.get(f)
                                    for f in (
                                        field[0]
                                        if isinstance(field[0], list)
                                        else [field[0]]
                                    )
                                ),
                            )
                        ),
                        **field[1],
                    )
                    if isinstance(field, tuple)
                    else item.get(field)
                )
                for key, field in mapping.items()
            }
            for item in value
        ]

    def _return_option(self, option, value, prop, default):
        if not option:
            return default

        if prop == "slug":  # pragma: no cover
            return option.slug
        elif prop == "label":
            return option.label.get(get_language())

        return {"slug": value, "label": option.label.get(get_language())}

    def option_parser(self, value, default, answer=None, prop=None, **kwargs):
        if isinstance(value, list):
            return [
                self.option_parser(v, default, answer=answer, prop=prop) for v in value
            ]

        option = next(
            filter(lambda option: option.pk == value, answer.question.options.all()),
            None,
        )
        return self._return_option(option, value, prop, default)

    def dynamic_option_parser(self, value, default, answer=None, prop=None, **kwargs):
        if isinstance(value, list):  # pragma: no cover
            return [
                self.dynamic_option_parser(v, default, answer=answer) for v in value
            ]

        dynamic_option = next(
            filter(
                lambda dynamic_option: dynamic_option.slug == value,
                answer.document.dynamicoption_set.all(),
            ),
            None,
        )
        return self._return_option(dynamic_option, value, prop, default)
