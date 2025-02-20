import { action } from "@ember/object";
import { service } from "@ember/service";
import Component from "@glimmer/component";
import { tracked } from "@glimmer/tracking";
import { task } from "ember-concurrency";
import { findRecord } from "ember-data-resources";
import { trackedFunction } from "reactiveweb/function";

import paginatedQuery from "ember-ebau-core/resources/paginated";

export default class CommunicationTopicComponent extends Component {
  @service store;
  @service router;
  @service fetch;
  @service notification;
  @service intl;

  @tracked page = 1;

  get newMessage() {
    return this.newMessageResource.value;
  }

  get messages() {
    return [...this.messagesResource.records].reverse();
  }

  get topic() {
    return this.topicResource.record;
  }

  get isLoading() {
    return (
      this.topicResource.isLoading ||
      (this.messagesResource.isLoading && !this.messagesResource.records.length)
    );
  }

  get involvedEntities() {
    return this.topic?.involvedEntities.map((entity) => entity.name).join(", ");
  }

  topicResource = findRecord(this, "communications-topic", () => [
    this.args.topicId,
    { include: "initiated_by" },
  ]);

  messagesResource = paginatedQuery(this, "communications-message", () => ({
    topic: this.args.topicId,
    page: {
      number: this.page,
      size: 5,
    },
    include: "created-by-user,attachments",
  }));

  newMessageResource = trackedFunction(this, async () => {
    const topic = this.topicResource.record;
    if (topic) {
      return this.store.createRecord("communications-message", {
        topic,
      });
    }
  });

  sendMessage = task(async () => {
    try {
      await this.newMessage.send();
      await this.messagesResource.retry();
      await this.newMessageResource.retry();
    } catch (error) {
      console.error(error);
      let errorCode = error?.message;

      if (error.response) {
        const data = await error.response.json();
        errorCode = data.errors?.[0].code;
      }

      if (errorCode === "infected") {
        this.notification.danger(
          this.intl.t("communications.new.uploadErrorVirus"),
        );
      } else {
        this.notification.danger(this.intl.t("communications.new.saveError"));
      }
    }
  });

  @action
  updateMessage(body) {
    this.newMessage.body = body;
  }
}
