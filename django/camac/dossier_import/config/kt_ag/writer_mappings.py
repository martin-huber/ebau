PERSON_VALUE_MAPPING = {
    "is_juristic_person": {
        True: "juristische-person-ja",
        False: "juristische-person-nein",
    }
}

PERSON_MAPPING = {
    "is_juristic_person": "juristische-person",
    "company": "juristische-person-name",
    "last_name": "nachname",
    "first_name": "vorname",
    "street": "strasse",
    "street_number": "strasse-nummer",
    "zip": "plz",
    "town": "ort",
    "phone": "telefon",
    "email": "e-mail",
}

PLOT_DATA_MAPPING = {
    "number": "parzellennummer",
    "egrid": "e-grid-nr",
    "municipality": "gemeinde",
    # "coord_east": "lagekoordinaten-ost", TODO
    # "coord_north": "lagekoordinaten-nord",
}

INSTANCE_STATE_MAPPING = {
    "Gesuch in Erfassung": "subm",  # TODO should be new
    "Gesuch übermittelt": "subm",
    "Gesuch storniert": "finished",
    "Gesuch in Bearbeitung": "subm",
    "Anfrage / Stellungnahme offen": "subm",
    "In öffentlicher Auflage": "subm",
    "Verfügung erstellt": "decision",
    "Gesuch zurückgezogen": "finished",
    "Gesuch abgeschrieben": "finished",
    "Gesuch archiviert": "finished",
    "Gesuch Offline erfasst": "subm",
    "Rückbau bestätigt": "finished",
    "An Kanton gesendet": "subm",
}

TARGET_STATE_MAPPING = {
    "Gesuch in Erfassung": "SUBMITTED",  # TODO should be DRAFT
    "Gesuch übermittelt": "SUBMITTED",
    "Gesuch storniert": "REJECTED",
    "Gesuch in Bearbeitung": "SUBMITTED",
    "Anfrage / Stellungnahme offen": "SUBMITTED",
    "In öffentlicher Auflage": "SUBMITTED",
    "Verfügung erstellt": "APPROVED",
    "Gesuch zurückgezogen": "DONE",
    "Gesuch abgeschrieben": "DONE",
    "Gesuch archiviert": "DONE",
    "Gesuch Offline erfasst": "SUBMITTED",
    "Rückbau bestätigt": "DONE",
    "An Kanton gesendet": "SUBMITTED",
}
