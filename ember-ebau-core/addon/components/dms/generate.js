import { service } from "@ember/service";
import Component from "@glimmer/component";
import { tracked } from "@glimmer/tracking";
import { dropTask } from "ember-concurrency";
import { findAll } from "ember-data-resources";
import { saveAs } from "file-saver";
import { trackedFunction } from "reactiveweb/function";

import mainConfig from "ember-ebau-core/config/main";
import {
  MIME_TYPE_TO_EXTENSION,
  sortByDescription,
} from "ember-ebau-core/utils/dms";

function extractCategories(templates) {
  return [...new Set(templates.map((t) => t.meta.category?.trim()))]
    .filter(Boolean)
    .sort((a, b) => a.localeCompare(b));
}

export default class DmsGenerateComponent extends Component {
  @service alexandriaDocuments;
  @service notification;
  @service ebauModules;
  @service fetch;
  @service intl;

  @tracked template;

  allTemplates = findAll(this, "template");

  placeholders = trackedFunction(this, async () => {
    const response = await this.fetch.fetch(
      `/api/v1/instances/${this.args.instanceId}/dms-placeholders`,
      {
        headers: { accept: "application/json" },
      },
    );

    return await response.json();
  });

  get templates() {
    if (!this.allTemplates.records) return [];

    const templates = this.allTemplates.records
      .filter((t) => t.description)
      .sort(sortByDescription);

    const ownTemplates = templates.filter(
      (t) => parseInt(t.meta.service) === parseInt(this.ebauModules.serviceId),
    );
    const inheritedTemplates = templates.filter(
      (t) =>
        t.meta.service &&
        parseInt(t.meta.service) !== parseInt(this.ebauModules.serviceId),
    );
    const systemTemplates = templates.filter((t) => !t.meta.service);

    const ownUncategorized = ownTemplates.filter((t) => !t.meta.category);
    const inheritedUncategorized = inheritedTemplates.filter(
      (t) => !t.meta.category,
    );

    const categories = extractCategories(ownTemplates);
    const inheritedCategories = extractCategories(inheritedTemplates);

    return [
      ...categories.map((category) => ({
        groupName: category,
        options: ownTemplates.filter(
          (t) => t.meta.category?.trim() === category,
        ),
      })),
      ...(ownUncategorized.length
        ? [
            {
              groupName: this.intl.t("dms.ownUncategorized"),
              options: ownUncategorized,
            },
          ]
        : []),
      ...inheritedCategories.map((category) => ({
        groupName: `${category} (${this.intl.t("dms.inherited")})`,
        options: inheritedTemplates.filter(
          (t) => t.meta.category?.trim() === category,
        ),
      })),
      ...(inheritedUncategorized.length
        ? [
            {
              groupName: this.intl.t("dms.inheritedUncategorized"),
              options: inheritedUncategorized,
            },
          ]
        : []),
      ...(systemTemplates.length
        ? [
            {
              groupName: this.intl.t("dms.system"),
              options: systemTemplates,
            },
          ]
        : []),
    ];
  }

  @dropTask
  *merge(saveToDocuments, event) {
    event.preventDefault();

    const body = new FormData();
    body.append("data", JSON.stringify(this.placeholders.value));

    yield Promise.all(
      Object.entries(this.placeholders.value)
        .filter((entry) => String(entry[1]).startsWith("data:"))
        .map(async ([key, value]) => {
          const res = await fetch(value);
          const blob = await res.blob();

          body.append("files", blob, key);
        }),
    );

    try {
      const response = yield this.fetch.fetch(
        `/document-merge-service/api/v1/template/${this.template.slug}/merge/`,
        {
          method: "POST",
          headers: { "content-type": undefined, accept: "*/*" },
          body,
        },
      );

      const blob = yield response.blob();

      const extension = MIME_TYPE_TO_EXTENSION[blob.type];
      const filename = `${this.template.description}${extension}`;

      if (saveToDocuments) {
        if (mainConfig.documentBackend === "camac") {
          yield this.saveToDocumentsCamac(blob, filename);
        } else {
          yield this.saveToDocumentsAlexandria(blob, filename);
        }

        this.notification.success(this.intl.t("dms.merge-and-save-success"));
      } else {
        saveAs(blob, `${this.args.instanceId} - ${filename}`);
      }
    } catch {
      this.notification.danger(this.intl.t("dms.merge-error"));
    }
  }

  async saveToDocumentsCamac(blob, filename) {
    const attachmentBody = new FormData();

    const attachmentSection = mainConfig.attachmentSections.internal;

    attachmentBody.append("attachment_sections", attachmentSection);
    attachmentBody.append("instance", this.args.instanceId);
    attachmentBody.append("path", blob, filename);

    await this.fetch.fetch(`/api/v1/attachments`, {
      method: "POST",
      headers: { "content-type": undefined },
      body: attachmentBody,
    });
  }

  async saveToDocumentsAlexandria(blob, filename) {
    const file = new File([blob], filename, { type: blob.type });

    await this.alexandriaDocuments.upload("intern", [file]);
  }
}
