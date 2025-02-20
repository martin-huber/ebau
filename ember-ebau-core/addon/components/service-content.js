import { service } from "@ember/service";
import Component from "@glimmer/component";
import { decodeId } from "@projectcaluma/ember-core/helpers/decode-id";
import { trackedFunction } from "reactiveweb/function";

import mainConfig from "ember-ebau-core/config/main";

export default class ServiceContentComponent extends Component {
  @service store;
  @service session;
  @service calumaStore;

  content = trackedFunction(this, async () => {
    const caseId = decodeId(this.args.field.document.raw.case.id);
    const calumaCase = this.calumaStore.find(`Case:${caseId}`);
    const municipality_answer = this.args.field.document.findAnswer(
      mainConfig.answerSlugs.municipality,
    );
    const formSlug = this.args.field.document.raw.form.slug;
    const serviceId = municipality_answer ?? calumaCase.municipalityId ?? null;

    if (!serviceId) {
      return "";
    }

    const content = await this.store.query("service-content", {
      service: serviceId,
      forms: formSlug,
    });

    return content[0].content;
  });
}
