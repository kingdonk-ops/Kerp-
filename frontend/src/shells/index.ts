// Reusable domain shells (consolidation §6): build module pages on these. See README.md.
export { RegisterPage, browserDownload, type DownloadFn, type RegisterPageProps } from "./register/RegisterPage";
export {
  filterRows,
  localSavedViewStore,
  toCsv,
  type RegisterColumn,
  type SavedView,
  type SavedViewStore,
} from "./register/model";
export { DetailPage, type AuditEntry, type AuditSource, type DetailPageProps, type DetailTab } from "./detail/DetailPage";
export { GateBanner, type GateState, type GateStatus } from "./detail/GateBanner";
export { DynamicForm, type DynamicFormProps } from "./form/DynamicForm";
export { evaluateForm, type FormEvaluation } from "./form/engine";
export type { FieldError, FormFieldDef, FormTemplate, FormValues, RuleEvaluator } from "./form/types";
