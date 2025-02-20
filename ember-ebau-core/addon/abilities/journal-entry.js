import { service } from "@ember/service";
import { Ability } from "ember-can";

export default class JournalEntryAbility extends Ability {
  @service session;
  @service ebauModules;

  get canAdd() {
    return !this.session.isReadOnlyRole;
  }

  get canEdit() {
    return (
      this.canAdd &&
      parseInt(this.session.user?.id) ===
        parseInt(this.model?.belongsTo("user").id()) &&
      parseInt(this.ebauModules.serviceId) ===
        parseInt(this.model?.belongsTo("service").id())
    );
  }
}
