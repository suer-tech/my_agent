import assert from "node:assert/strict";
import test from "node:test";
import fc from "fast-check";
import { selfRestartViolation } from "./self-restart-guard.ts";
import { schedulerBypassViolation } from "./scheduler-bypass-guard.ts";

void test("launchctl controls of Iva itself are blocked in command positions", () => {
  fc.assert(
    fc.property(
      fc.constantFrom("", "sudo ", "env X=1 ", "command "),
      fc.constantFrom("bootout", "kickstart -k", "kill SIGTERM"),
      (wrapper, verb) => {
        assert.ok(
          selfRestartViolation(
            `${wrapper}launchctl ${verb} gui/501/app.iva.iva`,
          ),
        );
        assert.equal(
          selfRestartViolation(
            `${wrapper}launchctl ${verb} gui/501/app.iva.iva-brain`,
          ),
          null,
        );
      },
    ),
    { seed: 20261017 },
  );
  assert.equal(
    selfRestartViolation("launchctl print gui/501/app.iva.iva"),
    null,
  );
  assert.equal(
    selfRestartViolation("rg 'launchctl bootout gui/501/app.iva.iva' docs/"),
    null,
  );
});

void test("launchd schedule writes and foreign scheduling are guarded while reads remain available", () => {
  fc.assert(
    fc.property(
      fc.constantFrom("cat >", "tee", "cp x", "install -m 0644 x"),
      (verb) => {
        assert.ok(
          schedulerBypassViolation(
            `${verb} ~/Library/LaunchAgents/app.iva.custom.plist`,
          ),
        );
      },
    ),
    { seed: 20261017 },
  );
  assert.ok(
    schedulerBypassViolation("launchctl bootstrap gui/501 /tmp/custom.plist"),
  );
  assert.equal(
    schedulerBypassViolation("launchctl print gui/501/app.iva.iva-brain"),
    null,
  );
  assert.equal(
    schedulerBypassViolation("cat ~/Library/LaunchAgents/app.iva.iva.plist"),
    null,
  );
});
