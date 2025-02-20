import EmberRouter from "@ember/routing/router";
import { service } from "@ember/service";
import registerAdditionalDemand from "ember-ebau-core/modules/additional-demand";
import registerBilling from "ember-ebau-core/modules/billing";
import registerCommunications from "ember-ebau-core/modules/communications";
import registerCommunicationsGlobal from "ember-ebau-core/modules/communications-global";
import registerConstructionMonitoring from "ember-ebau-core/modules/construction-monitoring";
import registerDMSAdmin from "ember-ebau-core/modules/dms-admin";
import registerDossierImport from "ember-ebau-core/modules/dossier-import";
import registerLegalSubmission from "ember-ebau-core/modules/legal-submission";
import registerLinkedInstances from "ember-ebau-core/modules/linked-instances";
import registerPermissions from "ember-ebau-core/modules/permissions";
import registerPublication from "ember-ebau-core/modules/publication";
import registerRejection from "ember-ebau-core/modules/rejection";
import registerResponsible from "ember-ebau-core/modules/responsible";
import registerServicePermissions from "ember-ebau-core/modules/service-permissions";
import registerStaticContent from "ember-ebau-core/modules/static-content";
import registerTaskForm from "ember-ebau-core/modules/task-form";

import config from "ebau/config/environment";

export default class Router extends EmberRouter {
  @service ebauModules;

  location = config.locationType;
  rootURL = config.rootURL;

  setupRouter(...args) {
    const didSetup = super.setupRouter(...args);

    if (didSetup) {
      this.ebauModules.setupModules();
    }

    return didSetup;
  }
}

const resetNamespace = true;

/* eslint-disable-next-line array-callback-return */
Router.map(function () {
  this.route("login");
  this.route("protected", { path: "/" }, function () {
    // this is needed to resolve ambiguity between the global index and protected index routes
    this.route("index", { path: "/", resetNamespace });
    this.route("work-items", { resetNamespace });
    this.mount("ember-ebau-gwr", { as: "gwr-global", path: "/gwr" });

    this.route("cases", { resetNamespace }, function () {
      this.route("detail", { path: "/:instance_id" }, function () {
        this.mount("ember-alexandria", {
          as: "alexandria",
          path: "/documents",
        });
        this.route("form");
        this.route("work-items", function () {
          this.route("edit", { path: "/:work_item_id" });
          this.route("new");
        });
        this.route("journal");
        this.route("history");
        this.route("dms-generate");
        this.route("corrections");
        this.route("distribution", function () {
          this.mount("@projectcaluma/ember-distribution", {
            as: "distribution-engine",
            path: "/:case",
          });
        });
        this.mount("ember-ebau-gwr", { as: "gwr" });
        registerLegalSubmission(this);
        registerTaskForm(this);
        registerCommunications(this);
        registerPublication(this);
        registerAdditionalDemand(this);
        registerResponsible(this);
        registerBilling(this);
        registerRejection(this);
        registerPermissions(this);
        registerConstructionMonitoring(this);
        registerLinkedInstances(this);
      });
      this.route("new");
      this.route("not-found");
    });
    registerDMSAdmin(this, { resetNamespace });
    registerServicePermissions(this, { resetNamespace });
    registerCommunicationsGlobal(this, { resetNamespace });
    registerStaticContent(this, { resetNamespace });
    registerDossierImport(this, { resetNamespace });
    this.route("documents", { resetNamespace }, function () {
      this.mount("ember-alexandria", {
        as: "alexandria",
        path: "/",
      });
    });
  });
});
