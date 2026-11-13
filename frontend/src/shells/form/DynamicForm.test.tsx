import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { i18n } from "../../i18n";
import { DynamicForm } from "./DynamicForm";
import type { FormTemplate } from "./types";

// Synthetic template: weld visual check with conditional defect fields.
const TEMPLATE: FormTemplate = {
  fields: [
    {
      field_key: "visual_result",
      label: "Visual result",
      data_type: "select",
      required: true,
      options: [
        { value: "ACCEPT", label: "Accept" },
        { value: "REJECT", label: "Reject" },
      ],
    },
    {
      field_key: "defect_length_mm",
      label: "Defect length (mm)",
      data_type: "number",
      required: true,
      visible_if: "fields.visual_result == 'REJECT'",
      validation: { cel_expression: "fields.defect_length_mm > 0.0", error_message: "Length must be positive." },
    },
    { field_key: "remarks", label: "Remarks", data_type: "text" },
    { field_key: "witnessed", label: "Witnessed", data_type: "boolean" },
    { field_key: "inspected_on", label: "Inspected on", data_type: "date" },
  ],
};

function submit() {
  fireEvent.click(screen.getByRole("button", { name: i18n.t("shells.form.submit") }));
}

describe("DynamicForm", () => {
  it("renders a labelled control per data type and hides conditional fields", () => {
    render(<DynamicForm template={TEMPLATE} onSubmit={vi.fn()} />);

    expect(screen.getByLabelText(/Visual result/)).toBeInstanceOf(HTMLSelectElement);
    expect(screen.getByLabelText("Remarks")).toHaveAttribute("type", "text");
    expect(screen.getByLabelText("Witnessed")).toHaveAttribute("type", "checkbox");
    expect(screen.getByLabelText("Inspected on")).toHaveAttribute("type", "date");
    expect(screen.queryByLabelText(/Defect length/)).not.toBeInTheDocument();
  });

  it("reveals a field live when its visible_if becomes true", () => {
    render(<DynamicForm template={TEMPLATE} onSubmit={vi.fn()} />);

    fireEvent.change(screen.getByLabelText(/Visual result/), { target: { value: "REJECT" } });
    expect(screen.getByLabelText(/Defect length/)).toHaveAttribute("type", "number");

    fireEvent.change(screen.getByLabelText(/Visual result/), { target: { value: "ACCEPT" } });
    expect(screen.queryByLabelText(/Defect length/)).not.toBeInTheDocument();
  });

  it("blocks submission and shows required and CEL validation messages", () => {
    const onSubmit = vi.fn();
    render(<DynamicForm template={TEMPLATE} onSubmit={onSubmit} />);

    submit();
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByLabelText(/Visual result/)).toHaveAccessibleDescription(i18n.t("shells.form.errors.required"));

    fireEvent.change(screen.getByLabelText(/Visual result/), { target: { value: "REJECT" } });
    fireEvent.change(screen.getByLabelText(/Defect length/), { target: { value: "-2" } });
    submit();
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByLabelText(/Defect length/)).toHaveAccessibleDescription("Length must be positive.");
    expect(screen.getByLabelText(/Defect length/)).toHaveAttribute("aria-invalid", "true");
  });

  it("shows a generic message when a rule cannot be evaluated", () => {
    const template: FormTemplate = {
      fields: [
        {
          field_key: "steel_temp",
          label: "Steel temperature",
          data_type: "number",
          validation: { cel_expression: "fields.steel_temp >= fields.dew_point + 3.0", error_message: "unused" },
        },
      ],
    };
    render(<DynamicForm template={template} onSubmit={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Steel temperature"), { target: { value: "21" } });
    submit();
    expect(screen.getByLabelText("Steel temperature")).toHaveAccessibleDescription(
      i18n.t("shells.form.errors.ruleError"),
    );
  });

  it("submits typed, sanitized values once valid", () => {
    const onSubmit = vi.fn();
    render(
      <DynamicForm
        template={TEMPLATE}
        initialValues={{ visual_result: "REJECT", defect_length_mm: 4 }}
        onSubmit={onSubmit}
      />,
    );

    fireEvent.change(screen.getByLabelText(/Visual result/), { target: { value: "ACCEPT" } });
    fireEvent.change(screen.getByLabelText("Remarks"), { target: { value: "Cap profile fine" } });
    fireEvent.click(screen.getByLabelText("Witnessed"));
    fireEvent.change(screen.getByLabelText("Inspected on"), { target: { value: "2026-01-15" } });
    submit();

    expect(onSubmit).toHaveBeenCalledWith({
      visual_result: "ACCEPT",
      remarks: "Cap profile fine",
      witnessed: true,
      inspected_on: "2026-01-15",
    });
  });

  it("passes numbers to CEL as doubles and clears an emptied number field", () => {
    const onSubmit = vi.fn();
    render(<DynamicForm template={TEMPLATE} initialValues={{ visual_result: "REJECT" }} onSubmit={onSubmit} />);

    const length = screen.getByLabelText(/Defect length/);
    fireEvent.change(length, { target: { value: "12.5" } });
    submit();
    expect(onSubmit).toHaveBeenLastCalledWith({ visual_result: "REJECT", defect_length_mm: 12.5 });

    fireEvent.change(length, { target: { value: "" } });
    submit();
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(length).toHaveAccessibleDescription(i18n.t("shells.form.errors.required"));
  });

  it("handles prototype-named field keys as ordinary fields", () => {
    const onSubmit = vi.fn();
    const template: FormTemplate = {
      fields: [
        { field_key: "constructor", label: "Constructor", data_type: "text" },
        { field_key: "toString", label: "To string", data_type: "text", required: true },
      ],
    };
    render(<DynamicForm template={template} onSubmit={onSubmit} />);
    const ctor = screen.getByLabelText("Constructor");
    expect(ctor).toHaveValue("");

    fireEvent.change(ctor, { target: { value: "x" } });
    expect(ctor).not.toHaveAttribute("aria-invalid");
    submit();
    expect(onSubmit).not.toHaveBeenCalled();
    expect(ctor).not.toHaveAttribute("aria-invalid");
    expect(screen.getByLabelText(/To string/)).toHaveAccessibleDescription(i18n.t("shells.form.errors.required"));

    fireEvent.change(screen.getByLabelText(/To string/), { target: { value: "y" } });
    submit();
    expect(onSubmit).toHaveBeenCalledWith({ constructor: "x", toString: "y" });
  });

  it("marks a field required (asterisk + aria-required) only while its required_if is active", () => {
    const template: FormTemplate = {
      fields: [
        { field_key: "result", label: "Result", data_type: "text" },
        {
          field_key: "reason",
          label: "Reason",
          data_type: "text",
          required_if: "has(fields.result) && fields.result == 'REJECT'",
        },
      ],
    };
    render(<DynamicForm template={template} onSubmit={vi.fn()} />);
    const mark = i18n.t("shells.form.requiredMark");
    const reasonLabel = () => screen.getByText(/^Reason/, { selector: "label" });

    expect(screen.getByLabelText(/Reason/)).not.toHaveAttribute("aria-required");
    expect(within(reasonLabel()).queryByTitle(mark)).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Result"), { target: { value: "REJECT" } });
    expect(screen.getByLabelText(/Reason/)).toHaveAttribute("aria-required", "true");
    expect(within(reasonLabel()).getByTitle(mark)).toHaveTextContent("*");

    fireEvent.change(screen.getByLabelText("Result"), { target: { value: "ACCEPT" } });
    expect(screen.getByLabelText(/Reason/)).not.toHaveAttribute("aria-required");
    expect(within(reasonLabel()).queryByTitle(mark)).not.toBeInTheDocument();
  });

  it("keeps the static required mark and aria-required", () => {
    render(<DynamicForm template={TEMPLATE} onSubmit={vi.fn()} />);
    expect(screen.getByLabelText(/Visual result/)).toHaveAttribute("aria-required", "true");
    expect(screen.getByLabelText("Remarks")).not.toHaveAttribute("aria-required");
  });

  it("uses a custom submit label and disables the form while busy", () => {
    render(<DynamicForm template={TEMPLATE} onSubmit={vi.fn()} submitLabel="Record result" busy />);
    expect(screen.getByRole("button", { name: "Record result" })).toBeDisabled();
  });
});
