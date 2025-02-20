import { action } from "@ember/object";
import { service } from "@ember/service";
import { isDevelopingApp, isTesting, macroCondition } from "@embroider/macros";
import Component from "@glimmer/component";
import { hasFeature } from "ember-ebau-core/helpers/has-feature";

import config from "caluma-portal/config/environment";

const {
  languages,
  environment,
  profileURL,
  APPLICATION: { internalFrontend },
  ebau: { internalURL },
} = config;

const getInstanceParam = (route) => {
  const instance = route?.params?.instance;
  const parent = route.parent;

  if (!instance) {
    if (!parent) return null;

    return getInstanceParam(parent);
  }

  return instance;
};

export default class BeNavbarComponent extends Component {
  @service session;
  @service router;
  @service notification;
  @service store;

  languages = languages;
  environment = environment;
  profileURL = profileURL;

  get showFormBuilder() {
    return macroCondition(isDevelopingApp())
      ? this.session.isAuthenticated && this.session.isSupport
      : false;
  }

  get internalLink() {
    const currentRoute = this.router.currentRoute;

    if (/^instances\.edit/.test(currentRoute.name)) {
      const instanceId = getInstanceParam(currentRoute);

      if (!instanceId) return internalURL;

      const path =
        internalFrontend === "camac"
          ? "/index/redirect-to-instance-resource/instance-id/"
          : "/cases/";

      return internalURL + path + instanceId;
    }

    return internalURL;
  }

  get showLanguageSwitcher() {
    return this.languages.length > 1;
  }

  @action
  async setGroup(groupId, event) {
    event?.preventDefault();

    if (this.router.currentRoute?.queryParams.group) {
      await this.router.replaceWith({ queryParams: { group: null } });
    }

    this.session.groupId = groupId;

    // Hard reload the whole page so the data is refetched
    if (macroCondition(!isTesting())) {
      window.location.reload();
    }
  }

  @action
  async setLanguage(language, event) {
    event?.preventDefault();

    if (this.router.currentRoute?.queryParams.language) {
      await this.router.replaceWith({ queryParams: { language: null } });
    }

    this.session.language = language;

    // Hard reload the whole page so the data is refetched
    if (macroCondition(!isTesting())) {
      window.location.reload();
    }
  }

  @action
  logout(event) {
    event.preventDefault();

    if (hasFeature("login.tokenExchange")) {
      // Reset the group ID to avoid errors after a re-login via token exchange
      this.session.groupId = null;
    }

    this.session.singleLogout();
  }
}
