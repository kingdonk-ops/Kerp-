import { describe, expect, it } from "vitest";

import { localise } from "./localise";

// Synthetic strings only.
const LABEL = { en: "Pull-off adhesion (PSI)", de: "Haftabzugsprüfung (PSI)", "de-AT": "Haftabzug (PSI, AT)" };

describe("localise", () => {
  it("uses a plain string as is, whatever the locale", () => {
    expect(localise("Remarks", "de-AT", "en")).toBe("Remarks");
    expect(localise("", "en", "en")).toBe("");
  });

  it("picks the exact locale first", () => {
    expect(localise(LABEL, "de-AT", "en")).toBe("Haftabzug (PSI, AT)");
    expect(localise(LABEL, "de", "en")).toBe("Haftabzugsprüfung (PSI)");
  });

  it("matches BCP 47 tags case-insensitively", () => {
    expect(localise(LABEL, "DE-at", "en")).toBe("Haftabzug (PSI, AT)");
    expect(localise({ EN: "Remarks" }, "en", "fr")).toBe("Remarks");
  });

  it("falls back to the language of the locale (de-CH -> de)", () => {
    expect(localise(LABEL, "de-CH", "en")).toBe("Haftabzugsprüfung (PSI)");
    expect(localise({ en: "Remarks", de: "Bemerkungen" }, "de-AT", "en")).toBe("Bemerkungen");
  });

  it("falls back to the template default language", () => {
    expect(localise(LABEL, "fr-FR", "en")).toBe("Pull-off adhesion (PSI)");
    expect(localise(LABEL, "fr", "de")).toBe("Haftabzugsprüfung (PSI)");
  });

  it("falls back to the first entry when neither locale nor default language match", () => {
    expect(localise({ nl: "Opmerkingen", de: "Bemerkungen" }, "fr", "en")).toBe("Opmerkingen");
  });

  it("returns an empty string for an empty map (the server lint rejects it)", () => {
    expect(localise({}, "en", "en")).toBe("");
  });

  it("reads own properties only", () => {
    expect(localise({ en: "Remarks" }, "constructor", "toString")).toBe("Remarks");
    expect(localise({ en: "Remarks" }, "__proto__", "en")).toBe("Remarks");
  });
});
