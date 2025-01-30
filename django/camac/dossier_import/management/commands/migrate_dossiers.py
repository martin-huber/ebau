import pprint

from django.core.management.base import BaseCommand

from camac.dossier_import.domain_logic import perform_import
from camac.dossier_import.models import DossierImport


class Command(BaseCommand):
    help = "Import a form from an data integration"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dossier_id",
            type=str,
            help="The ID of the dossier from that the form data will be imported",
            nargs=1,
        )

    def handle(self, *args, **options):
        if options.get("dossier_id"):
            dossier_id = options.get("dossier_id")[0]
            self.stdout.write(f"Importing from '{dossier_id}'")
        else:
            self.stdout.write("Importing all")

            dossier_import = DossierImport.objects.create(user_id="1", group_id="10003")

            self.stdout.write(f"Starting import: {dossier_import.pk}")

            perform_import(dossier_import)

            self.stdout.write(f"Dossier import finished Ref: {str(dossier_import.pk)}")
            self.stdout.write(
                f"{pprint.pformat(dossier_import.messages['import']['summary'])}"
            )
