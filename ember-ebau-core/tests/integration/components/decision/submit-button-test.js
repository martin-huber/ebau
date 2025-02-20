import { render, click } from "@ember/test-helpers";
import { faker } from "@faker-js/faker";
import { hbs } from "ember-cli-htmlbars";
import { setupMirage } from "ember-cli-mirage/test-support";
import { t } from "ember-intl/test-support";
import { module, test } from "qunit";

import { setupRenderingTest } from "dummy/tests/helpers";
import id from "dummy/tests/helpers/graphql-id";
import { setupConfig } from "ember-ebau-core/test-support";

module("Integration | Component | decision/submit-button", function (hooks) {
  setupRenderingTest(hooks);
  setupMirage(hooks);
  setupConfig(hooks);

  hooks.beforeEach(function (assert) {
    this.isAppeal = false;
    this.isPreliminaryClarification = false;
    this.isPartial = false;

    this.field = {
      question: {
        raw: {
          action: "COMPLETE",
          color: "PRIMARY",
          validateOnEnter: false,
          label: "Entscheid verfügen",
        },
      },
      document: {
        fields: [],
        workItemUuid: faker.string.uuid(),
        findAnswer: (slug) => {
          if (slug === "decision-workflow") {
            return this.isPreliminaryClarification
              ? "preliminary-clarification"
              : "building-permit";
          } else if (slug === "decision-approval-type") {
            return this.isPartial
              ? "decision-approval-type-partial-building-permit"
              : null;
          } else if (slug === "decision-decision-assessment") {
            return this.isAppeal
              ? "decision-decision-assessment-appeal-rejected"
              : null;
          }
        },
      },
    };

    this.server.post(
      "/api/v1/notification-templates/sendmail",
      () => assert.step("notification"),
      201,
    );

    this.server.post("/graphql", (_, request) => {
      const { operationName } = JSON.parse(request.requestBody);
      if (operationName === "GetCaseMeta") {
        return {
          data: {
            allCases: {
              edges: [
                {
                  node: {
                    id: id("Case"),
                    meta: { "is-appeal": this.isAppeal },
                    document: {
                      id: id("Document"),
                      form: {
                        id: id("Form", "baugesuch"),
                        slug: "baugesuch",
                      },
                    },
                  },
                },
              ],
            },
          },
        };
      } else if (operationName === "CompleteWorkItem") {
        assert.step("complete");
        return {
          data: {
            completeWorkItem: {
              workItem: {
                id: id("WorkItem"),
                status: "COMPLETED",
                case: {
                  id: id("Case"),
                  status: "RUNNING",
                },
              },
            },
          },
        };
      } else if (operationName === "GetCopies") {
        return {
          data: {
            allCases: {
              edges: [
                {
                  node: {
                    id: id("Case"),
                    document: {
                      id: id("Document"),
                      copies: {
                        edges: [
                          {
                            node: {
                              id: id("Document"),
                              case: {
                                id: id("Case"),
                                meta: {
                                  "is-rejected-appeal": true,
                                  "camac-instance-id": 2,
                                },
                              },
                            },
                          },
                        ],
                      },
                    },
                  },
                },
              ],
            },
          },
        };
      }
    });
  });

  test.each(
    "it renders the correct label",
    [
      [false, false, "Entscheid verfügen"],
      [true, false, "decision.submit.appeal"],
      [false, true, "decision.submit.preliminary-clarification"],
    ],
    async function (
      assert,
      [isAppeal, isPreliminaryClarification, expectedLabel],
    ) {
      this.isAppeal = isAppeal;
      this.isPreliminaryClarification = isPreliminaryClarification;

      await render(
        hbs`<Decision::SubmitButton @field={{this.field}} @context={{hash instanceId=1}} />`,
      );

      expectedLabel = this.owner.lookup("service:intl").exists(expectedLabel)
        ? t(expectedLabel)
        : expectedLabel;

      assert.dom("button").hasText(expectedLabel);
    },
  );

  test("it only sends notifications on submit of partial decisions", async function (assert) {
    this.isPartial = true;

    await render(
      hbs`<Decision::SubmitButton @field={{this.field}} @context={{hash instanceId=1}} />`,
    );

    await click("button");

    assert.verifySteps(["notification", "notification"]);
  });

  test("it redirects to the new instance after submitting rejected appeal decisions", async function (assert) {
    this.isAppeal = true;

    this.config.set("decision", {
      answerSlugs: {
        decision: "decision-decision-assessment",
      },
    });

    this.config.set("appeal", {
      answerSlugs: {
        willGenerateCopy: ["decision-decision-assessment-appeal-rejected"],
      },
    });

    this.owner.lookup("service:ebau-modules").redirectToInstance = (
      instanceId,
    ) => {
      assert.step("redirect");
      assert.strictEqual(instanceId, 2);
    };

    await render(
      hbs`<Decision::SubmitButton @field={{this.field}} @context={{hash instanceId=1}} />`,
    );

    await click("button");

    assert.verifySteps(["complete", "redirect"]);
  });
});
