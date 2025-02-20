import os
import re
import sys
import time
import traceback
from dataclasses import asdict
from functools import wraps
from logging import getLogger

import requests
from caluma.caluma_workflow.models import Case
from django.conf import settings
from django.utils import timezone
from django.utils.module_loading import import_string
from requests_toolbelt.multipart.encoder import MultipartEncoder

from camac.core.utils import generate_ebau_nr
from camac.dossier_import.dossier_classes import (
    Dossier,
)
from camac.dossier_import.loaders import XlsxFileDossierLoader
from camac.dossier_import.messages import (
    DOSSIER_IMPORT_STATUS_ERROR,
    DossierSummary,
    Message,
    MessageCodes,
    Severity,
    update_summary,
)
from camac.dossier_import.models import DossierImport
from camac.instance.models import Instance
from camac.user.models import User
from camac.utils import build_url

log = getLogger(__name__)


def delay_and_refresh(func):
    @wraps(func)
    def wrapper(dossier_import, *args, **kwargs):
        # django-q is using pickle to transmit arguments. We need to refresh from db, otherwise
        # the task id (which we set immediately after the task is started) is lost when we save the
        # pickled object.
        # We also need to make sure that the view has finished processing (i.e. saved the task id),
        # so we wait shortly.
        time.sleep(0.1)
        dossier_import.refresh_from_db()
        return func(dossier_import, *args, **kwargs)

    return wrapper


@delay_and_refresh
def perform_import(dossier_import):
    try:
        configured_writer_cls = import_string(settings.DOSSIER_IMPORT["WRITER_CLASS"])

        loader = XlsxFileDossierLoader()

        writer = configured_writer_cls(
            user_id=User.objects.get(username=settings.DOSSIER_IMPORT["USER"]).pk,
            group_id=dossier_import.group.pk,
            location_id=dossier_import.location and dossier_import.location.pk,
        )
        dossier_import.messages["import"] = {"details": []}
        for dossier in loader.load_dossiers(dossier_import.get_archive()):
            dossier: Dossier
            try:
                message = writer.import_dossier(
                    dossier,
                    str(dossier_import.id),
                )
            except Exception as e:  # pragma: no cover  # noqa: B902
                # We need to catch unhandled exeptions in single dossier imports
                # and keep it going.
                tb = traceback.format_exc()
                log.exception(e)
                msg = Message(
                    level=Severity.ERROR.value,
                    code=MessageCodes.UNHANDLED_EXCEPTION.value,
                    detail=f"{e}",
                )
                debug = Message(
                    level=Severity.DEBUG.value,
                    code=msg.code,
                    detail=f"{msg.detail}\n{tb}",
                )
                message = DossierSummary(
                    dossier_id=dossier.id,
                    status=DOSSIER_IMPORT_STATUS_ERROR,
                    details=[msg, debug],
                )
            dossier_import.messages["import"]["details"].append(asdict(message))
            dossier_import.save()
        update_summary(dossier_import)
        dossier_import.messages["import"]["completed"] = timezone.localtime().strftime(
            "%Y-%m-%dT%H:%M:%S%z"
        )
        dossier_import.status = DossierImport.IMPORT_STATUS_IMPORTED

    except Exception as e:  # pragma: no cover  # noqa: B902
        # This is just the last straw. An exception caught here
        # aborts the import session.
        log.exception(e, exc_info=True)
        dossier_import.messages["import"]["exception"] = str(e)
        dossier_import.status = DossierImport.IMPORT_STATUS_IMPORT_FAILED

    finally:
        dossier_import.save()

    return dossier_import.status


def get_token():
    DOSSIER_IMPORT = settings.DOSSIER_IMPORT
    r = requests.post(
        DOSSIER_IMPORT.get("PROD_AUTH_URL"),
        {
            "grant_type": "client_credentials",
            "client_id": settings.DOSSIER_IMPORT_CLIENT_ID,
            "client_secret": settings.DOSSIER_IMPORT_CLIENT_SECRET,
        },
    )
    r.raise_for_status()
    return r.json()["access_token"]


@delay_and_refresh
def transmit_import(dossier_import):
    try:
        token = f"Bearer {get_token()}"

        DOSSIER_IMPORT = settings.DOSSIER_IMPORT
        dossier_import.source_file.seek(0)
        fields = {
            "group": str(dossier_import.group.pk),
            "source_file": (
                os.path.basename(dossier_import.source_file.name),
                dossier_import.source_file,
                "application/zip",
            ),
        }
        if DOSSIER_IMPORT.get("LOCATION_REQUIRED", False):
            fields["location_id"] = str(dossier_import.location.pk)

        m = MultipartEncoder(fields=fields)
        r = requests.post(
            build_url(DOSSIER_IMPORT.get("PROD_URL"), "/api/v1/dossier-imports"),
            data=m,
            headers={
                "Content-Type": m.content_type,
                "Authorization": token,
                "x-camac-group": str(DOSSIER_IMPORT.get("PROD_SUPPORT_GROUP_ID")),
            },
        )
        r.raise_for_status()
        dossier_import.status = DossierImport.IMPORT_STATUS_TRANSMITTED

    except Exception as e:  # pragma: no cover # noqa: B902
        log.exception(e)
        dossier_import.messages["import"]["exception"] = str(e)
        dossier_import.status = DossierImport.IMPORT_STATUS_TRANSMISSION_FAILED

    finally:
        dossier_import.save()
    return dossier_import.status


@delay_and_refresh
def undo_import(dossier_import):
    try:
        # wait shortly to avoid race condition when "in progress" status is saved for
        # very quick "undo" operations
        time.sleep(0.1)
        Instance.objects.filter(
            **{"case__meta__import-id": str(dossier_import.pk)}
        ).delete()
        Case.objects.filter(**{"meta__import-id": str(dossier_import.pk)}).delete()
        dossier_import.delete()
        return DossierImport.IMPORT_STATUS_UNDONE
    except Exception as e:  # pragma: no cover # noqa: B902
        log.exception(e)
        dossier_import.status = DossierImport.IMPORT_STATUS_UNDO_FAILED
        dossier_import.save()
        return dossier_import.status


def clean_import(dossier_import):
    try:
        dossier_import.delete_file()
        dossier_import.status = DossierImport.IMPORT_STATUS_CLEANED
    except Exception as e:  # pragma: no cover # noqa: B902
        log.exception(e, exc_info=True)
        dossier_import.messages["import"]["exception"] = str(sys.exc_info())
        dossier_import.status = DossierImport.IMPORT_STATUS_CLEAN_FAILED
    finally:
        dossier_import.save()
    return dossier_import.status


def get_or_create_ebau_nr(ebau_number, service, submit_date=None):
    """Validate a proposed ebau-number to match its service domain or get a new one."""
    pattern = re.compile("([0-9]{4}-[1-9][0-9]*)")
    result = pattern.search(str(ebau_number))
    if result:
        try:
            match = result.groups()[0]
            case = Case.objects.filter(**{"meta__ebau-number": match}).first()
            if case.instance.services.filter(service_id=service.pk).exists():
                return match
        except AttributeError:
            pass

    return generate_ebau_nr(None, submit_date.year) if submit_date else None


def set_status_callback(task):
    dossier_import = task.args[0]

    try:
        dossier_import.refresh_from_db()
    except DossierImport.DoesNotExist:
        # the undo task deletes the instance on success
        return

    if task.result == dossier_import.IMPORT_STATUS_UNDONE:
        # fallback to cover race condition when deleting the import on undo
        return

    if task.result in [status[0] for status in DossierImport.IMPORT_STATUS_CHOICES]:
        status = task.result
    else:
        status = dossier_import.set_progressing_to_failed()

    dossier_import.status = status
    dossier_import.save()
