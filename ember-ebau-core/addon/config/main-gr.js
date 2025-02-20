export default {
  name: "gr",
  languages: ["de", "it"],
  fallbackLanguage: "de",
  prodUrl: "ebau.admin.gr.ch",
  allowApplicantManualWorkItem: false,
  documentBackend: "alexandria",
  showIdInInternalArea: false,
  serviceGroups: {
    authorityBaB: 3,
  },
  instanceStates: {
    new: 1,
    subm: 120004,
    circulation: 120005,
    decision: 120007,
    correction: 120008,
    "init-distribution": 120009,
    rejected: 120011,
    withdrawn: 120012,
    withdrawal: 120013,
  },
  gwr: {
    cantonAbbreviation: "GR",
    importModels: ["project", "building", "dwelling"],
    modalContainer: "body",
  },
  submittedStates: [
    120004, // subm
    120005, // circulation
    120006, // finished
    120007, // decision
    120008, // correction
    120009, // init-distribution
    120010, // construction-acceptance
    120011, // rejected
  ],
  answerSlugs: {
    objectStreet: "street-and-housenumber",
    objectLocation: "ort-grundstueck",
    description: "beschreibung-bauvorhaben",
    municipality: "gemeinde",
    specialId: "dossier-number",
    parcel: "parzelle",
    parcelNumber: "parzellennummer",
    firstNameApplicant: "vorname-gesuchstellerin",
    lastNameApplicant: "name-gesuchstellerin",
    juristicNameApplicant: "name-juristische-person-gesuchstellerin",
    isJuristicApplicant: "juristische-person-gesuchstellerin",
    isJuristicApplicantYes: "juristische-person-gesuchstellerin-ja",
    personalDataApplicant: "personalien-gesuchstellerin",
    coordinates: "gis-map",
  },
  personalSuggestions: {
    tableQuestions: [
      "personalien-gesuchstellerin",
      "personalien-vertreterin-mit-vollmacht",
      "personalien-grundeigentumerin",
      "personalien-projektverfasserin",
      "personalien-gebaudeeigentumerin",
      "personalien-sb",
    ],
    firstNameRegexp: "^vorname-.*$",
    lastNameRegexp: "^name-.*$",
    juristicNameRegexp: "^name-juristische-person.*$",
    emailRegexp: "^e-mail-.*$",
  },
  paperInstances: {
    allowedGroups: {
      roles: [
        3, // municipality
      ],
      serviceGroups: [
        2, // municipality
      ],
    },
  },
  intentSlugs: ["beschreibung-bauvorhaben"],
  correction: {
    instanceState: "correction",
    allowedInstanceStates: ["subm", "init-distribution", "circulation"],
  },
  publication: {
    public: {
      task: "fill-publication",
    },
  },
  decision: {
    task: "decision",
    answerSlugs: {
      decision: "decision-decision",
      remarks: "decision-remarks",
    },
    colorMapping: {
      "decision-decision-approved": "uk-alert-success",
      "decision-decision-rejected": "uk-alert-danger",
      "decision-decision-written-off": "uk-alert-warning",
      "decision-decision-positive": "uk-alert-success",
      "decision-decision-negative": "uk-alert-danger",
      "decision-decision-positive-with-reservation": "uk-alert-warning",
      "decision-decision-retreat": "uk-alert-warning",
    },
  },
  legalSubmission: {
    task: "objections",
    tableForm: "einsprache",
    tableQuestion: "einsprachen",
    orderQuestion: "einsprache-datum",
    columns: {
      date: "einsprache-datum",
      "legal-claimants": "einsprache-einsprechende",
      withdrawn: "einsprache-zurueckgezogen",
    },
  },
  rejection: {
    instanceState: "rejected",
    allowedInstanceStates: [
      "subm",
      "init-distribution",
      "circulation",
      "decision",
    ],
  },
  withdrawal: {
    allowedInstanceStates: [
      "subm",
      "init-distribution",
      "distribution",
      "decision",
    ],
  },
  alexandria: {
    marks: {
      decision: "decision",
      void: "void",
    },
  },
  showDownloadReceiptAction: true,
  customDeadlineServiceGroupSlugs: [
    "authority-bab", // Ausserhalb Bauzone
    "uso", // Umweltschutzorganisationen
  ],
  communication: {
    rolesWithApplicantContact: ["activeOrInolvedLeadAuthority", "service"],
  },
  modification: {
    allowForms: ["baugesuch"],
    disallowStates: ["new"],
  },
};
