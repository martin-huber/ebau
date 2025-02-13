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
    "plot_number": "parzellennummer",
    "egrid_number": "e-grid",
    "coord_east": "lagekoordinaten-ost",
    "coord_north": "lagekoordinaten-nord",
}
