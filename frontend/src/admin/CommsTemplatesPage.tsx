import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { useServices } from "../app/services";
import {
  draftProblems,
  fetchRevisions,
  fetchTemplates,
  groupTemplates,
  mailProblems,
  MAX_TEMPLATE,
  previewTemplate,
  resetTemplate,
  saveTemplate,
  TEMPLATE_FIELDS,
  testSendTemplate,
  type MailTemplate,
  type RefusalReason,
  type TemplateDraft,
  type TemplateField,
  type TemplatePreview,
  type TemplateRevision,
} from "./comms";
import { Region, retryTime, useCommsGate } from "./commsUi";
import { fetchDeploymentConfig } from "./settings";
import { BUTTON, CARD_ITEM, CommsTabs, Dialog, formatTime, INPUT, PRIMARY } from "./ui";

const TEMPLATES_KEY = ["admin", "comms", "templates"] as const;
const CONFIG_KEY = ["settings", "deploymentConfig"] as const;

/**
 * Admin → Communications → E-mail templates (diff §4.7 incl. 'Editor details', §6.5; T-0227,
 * T-0229): the registered templates grouped by audience; per template and language the
 * subject and text blocks with the declared placeholders beside them, a preview with sample
 * values (rendered in a sandboxed frame), a test send to oneself, reset to the shipped default
 * and the revisions. A banner says when the deployment cannot send e-mail. System admins only.
 */
export function CommsTemplatesPage() {
  const { t } = useTranslation();
  const { api } = useServices();
  const gate = useCommsGate(true);
  const templates = useQuery({ queryKey: TEMPLATES_KEY, enabled: gate.allowed, queryFn: () => fetchTemplates(api) });
  const config = useQuery({ queryKey: CONFIG_KEY, enabled: gate.allowed, queryFn: () => fetchDeploymentConfig(api) });
  const [selected, setSelected] = useState<string | null>(null);
  const forbidden = gate.forbidden([templates.error, config.error]);
  const template = templates.data?.find((tpl) => tpl.key === selected);

  return (
    <section className="space-y-4">
      <h1 className="text-2xl font-bold">{t("admin.comms.templates.title")}</h1>
      <CommsTabs systemAdmin={gate.access.systemAdmin} />
      {forbidden ? (
        <p role="alert">{t("admin.comms.forbiddenSystem")}</p>
      ) : (
        <>
          {config.data && <MailHealth config={config.data} />}
          <p>{t("admin.comms.templates.intro")}</p>
          {(gate.access.isLoading || templates.isPending) && <p>{t("app.loading")}</p>}
          {templates.isError && <p role="alert">{t("admin.comms.loadError")}</p>}
          {template && <Editor key={template.key} template={template} onClose={() => { setSelected(null); }} />}
          {templates.data && (
            <Region title={t("admin.comms.templates.listTitle")}>
              {groupTemplates(templates.data).map((group) => (
                <Region key={group.audience} level={3} title={t(`admin.comms.templates.audiences.${group.audience}`)}>
                  <ul className="space-y-2">
                    {group.templates.map((tpl) => (
                      <li key={tpl.key} className={`${CARD_ITEM} flex flex-wrap items-center justify-between gap-touch-gap`}>
                        <span className="space-x-2 break-all">
                          <span className="font-semibold">{tpl.key}</span>
                          {tpl.required && <span className="rounded border border-line px-1 text-sm">{t("admin.comms.templates.required")}</span>}
                          {tpl.locales.some((l) => l.override) && (
                            <span className="rounded border border-line px-1 text-sm">{t("admin.comms.templates.overridden")}</span>
                          )}
                        </span>
                        <button
                          type="button"
                          className={BUTTON}
                          aria-label={t("admin.comms.templates.open", { key: tpl.key })}
                          onClick={() => { setSelected(tpl.key); }}
                        >
                          {t("admin.comms.templates.edit")}
                        </button>
                      </li>
                    ))}
                  </ul>
                </Region>
              ))}
            </Region>
          )}
        </>
      )}
    </section>
  );
}

/** Why sending is not possible, from the read-only deployment configuration; never a credential. */
function MailHealth({ config }: { config: { mail_transport: string; mail_from_address: string | null } }) {
  const { t } = useTranslation();
  const problems = mailProblems(config);
  if (problems.length === 0) return null;
  return (
    <div role="alert" className="space-y-1 rounded border-2 border-brand bg-panel p-3">
      <p className="font-semibold">{t("admin.comms.templates.health.title")}</p>
      <ul className="list-disc pl-5">
        {problems.map((p) => (
          <li key={p}>{t(`admin.comms.templates.health.${p}`)}</li>
        ))}
      </ul>
      <p>{t("admin.comms.templates.health.contact")}</p>
    </div>
  );
}

function draftOf(template: MailTemplate, locale: string): TemplateDraft {
  const entry = template.locales.find((l) => l.locale === locale);
  const text = entry?.override ?? entry?.shipped;
  return {
    subject: text?.subject ?? "",
    intro: text?.intro ?? "",
    outro: text?.outro ?? "",
    actionLabel: text?.action_label ?? "",
  };
}

type FieldErrors = Partial<Record<TemplateField, RefusalReason>>;

function Editor({ template, onClose }: { template: MailTemplate; onClose: () => void }) {
  const { t, i18n } = useTranslation();
  const { api } = useServices();
  const queryClient = useQueryClient();
  const [locale, setLocale] = useState(template.locales[0]?.locale ?? "en-AU");
  const [draft, setDraft] = useState<TemplateDraft>(() => draftOf(template, locale));
  const [errors, setErrors] = useState<FieldErrors>({});
  const [preview, setPreview] = useState<TemplatePreview | null>(null);
  const [notice, setNotice] = useState<{ role: "status" | "alert"; text: string } | null>(null);
  const [confirmReset, setConfirmReset] = useState(false);
  const refs = useRef<Partial<Record<TemplateField, HTMLInputElement | HTMLTextAreaElement | null>>>({});
  const lastField = useRef<TemplateField>("subject");
  const formId = useId();
  const fields = TEMPLATE_FIELDS.filter((f) => f !== "actionLabel" || template.has_action);
  const revisionsKey = [...TEMPLATES_KEY, template.key, locale, "revisions"] as const;
  const revisions = useQuery({ queryKey: revisionsKey, queryFn: () => fetchRevisions(api, template.key, locale) });

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: TEMPLATES_KEY });
  };
  const reload = async () => {
    await queryClient.invalidateQueries({ queryKey: TEMPLATES_KEY });
    const fresh = queryClient.getQueryData<MailTemplate[]>(TEMPLATES_KEY) ?? [];
    const tpl = fresh.find((x) => x.key === template.key);
    if (tpl) setDraft(draftOf(tpl, locale));
  };
  /** The client's checks (empty, length); `true` when the draft may go to the server. */
  const checked = (): boolean => {
    const problems = draftProblems(draft, template);
    setErrors(problems);
    return Object.keys(problems).length === 0;
  };

  const save = useMutation({
    mutationFn: () => saveTemplate(api, template.key, locale, draft, template.has_action),
    onSuccess: async (result) => {
      if (result.kind === "ok") {
        setNotice({ role: "status", text: t("admin.comms.templates.saved", { version: result.revision.version }) });
        await refresh();
      } else if (result.kind === "invalid") {
        setErrors(result.fields);
        setNotice(null);
      } else {
        await reload();
        setNotice({ role: "alert", text: t("admin.comms.templates.conflict") });
      }
    },
  });
  const render = useMutation({
    mutationFn: () => previewTemplate(api, template.key, locale, draft, template.has_action),
    onSuccess: (result) => {
      if (result.kind === "ok") setPreview(result.preview);
      else setErrors(result.fields);
    },
  });
  const testSend = useMutation({
    mutationFn: () => testSendTemplate(api, template.key, locale),
    onSuccess: (result) => {
      if (result.kind === "ok") {
        setNotice({ role: "status", text: t("admin.comms.templates.testSent") });
      } else {
        const time = retryTime(result.retryAfter, i18n.language);
        setNotice({
          role: "alert",
          text: time ? t("admin.comms.templates.testLimited", { time }) : t("admin.comms.templates.testLimitedLater"),
        });
      }
    },
  });
  const reset = useMutation({
    mutationFn: () => resetTemplate(api, template.key, locale),
    onSuccess: async () => {
      setConfirmReset(false);
      setNotice({ role: "status", text: t("admin.comms.templates.resetDone") });
      await reload();
    },
  });
  const failed = save.isError || render.isError || testSend.isError || reset.isError;

  const edit = (field: TemplateField, value: string) => {
    setDraft((d) => ({ ...d, [field]: value }));
    setErrors((e) => ({ ...e, [field]: undefined }));
    setPreview(null);
  };
  const insert = (name: string) => {
    const field = lastField.current;
    const el = refs.current[field];
    const value = draft[field];
    const start = el?.selectionStart ?? value.length;
    const end = el?.selectionEnd ?? value.length;
    const token = `{${name}}`;
    edit(field, value.slice(0, start) + token + value.slice(end));
    requestAnimationFrame(() => {
      el?.focus();
      el?.setSelectionRange(start + token.length, start + token.length);
    });
  };
  const switchLocale = (next: string) => {
    setLocale(next);
    setDraft(draftOf(template, next));
    setErrors({});
    setPreview(null);
    setNotice(null);
  };

  return (
    <Region title={t("admin.comms.templates.editorTitle", { key: template.key })}>
      {confirmReset && (
        <Dialog
          title={t("admin.comms.templates.resetTitle", { key: template.key })}
          onClose={() => { setConfirmReset(false); }}
          actions={
            <button type="button" className={PRIMARY} disabled={reset.isPending} onClick={() => { reset.mutate(); }}>
              {t("admin.comms.templates.reset")}
            </button>
          }
        >
          <p>{t("admin.comms.templates.resetBody")}</p>
        </Dialog>
      )}
      {template.locales.length > 1 && (
        <label className="block space-y-1">
          <span>{t("admin.comms.templates.locale")}</span>
          <select className={INPUT} value={locale} onChange={(e) => { switchLocale(e.target.value); }}>
            {template.locales.map((l) => (
              <option key={l.locale} value={l.locale}>
                {l.locale}
              </option>
            ))}
          </select>
        </label>
      )}
      {template.required && <p>{t("admin.comms.templates.requiredNote")}</p>}
      <div className="grid gap-3 md:grid-cols-[1fr_16rem]">
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            setNotice(null);
            if (checked()) save.mutate();
          }}
        >
          {fields.map((field) => {
            const problem = errors[field];
            const problemId = `${formId}-${field}-problem`;
            const props = {
              className: field === "intro" || field === "outro" ? `${INPUT} min-h-28 py-2` : INPUT,
              value: draft[field],
              "aria-invalid": problem !== undefined,
              "aria-describedby": problem ? problemId : undefined,
              onFocus: () => { lastField.current = field; },
              onChange: (e: { target: { value: string } }) => { edit(field, e.target.value); },
            };
            return (
              <div key={field} className="space-y-1">
                <label className="block space-y-1">
                  <span className="font-semibold">{t(`admin.comms.templates.fields.${field}`)}</span>
                  {field === "intro" || field === "outro" ? (
                    <textarea ref={(el) => { refs.current[field] = el; }} {...props} />
                  ) : (
                    <input ref={(el) => { refs.current[field] = el; }} {...props} />
                  )}
                </label>
                {problem && (
                  <p id={problemId} className="text-sm text-danger">
                    {t(`admin.comms.templates.problems.${problem}`, { max: MAX_TEMPLATE[field] })}
                  </p>
                )}
              </div>
            );
          })}
          <div className="flex flex-wrap gap-touch-gap">
            <button type="submit" className={PRIMARY} disabled={save.isPending}>
              {t("admin.comms.templates.save")}
            </button>
            <button
              type="button"
              className={BUTTON}
              disabled={render.isPending}
              onClick={() => { if (checked()) render.mutate(); }}
            >
              {t("admin.comms.templates.preview")}
            </button>
            <button type="button" className={BUTTON} disabled={testSend.isPending} onClick={() => { testSend.mutate(); }}>
              {t("admin.comms.templates.testSend")}
            </button>
            <button type="button" className={BUTTON} onClick={() => { setConfirmReset(true); }}>
              {t("admin.comms.templates.reset")}
            </button>
            <button type="button" className={BUTTON} onClick={onClose}>
              {t("admin.cancel")}
            </button>
          </div>
          {notice && <p role={notice.role}>{notice.text}</p>}
          {failed && <p role="alert">{t("admin.actionError")}</p>}
        </form>
        <Region level={3} title={t("admin.comms.templates.placeholdersTitle")}>
          {template.placeholders.length === 0 ? (
            <p>{t("admin.comms.templates.noPlaceholders")}</p>
          ) : (
            <>
              <p className="text-sm">{t("admin.comms.templates.placeholdersNote")}</p>
              <ul className="flex flex-wrap gap-touch-gap">
                {template.placeholders.map((name) => (
                  <li key={name}>
                    <button
                      type="button"
                      className={`${BUTTON} font-mono`}
                      aria-label={t("admin.comms.templates.insert", { name: `{${name}}` })}
                      onMouseDown={(e) => { e.preventDefault(); }}
                      onClick={() => { insert(name); }}
                    >
                      {`{${name}}`}
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </Region>
      </div>
      {preview && <Preview preview={preview} />}
      <Revisions
        revisions={revisions.data}
        error={revisions.isError}
        onLoad={(r) => {
          setErrors({});
          setPreview(null);
          setDraft({ subject: r.subject, intro: r.intro, outro: r.outro, actionLabel: r.action_label ?? "" });
        }}
      />
    </Region>
  );
}

/**
 * The rendered e-mail. The HTML is shown only inside a fully sandboxed frame (`sandbox=""`: no
 * scripts, no same-origin, no forms, no navigation), never in the page itself.
 */
function Preview({ preview }: { preview: TemplatePreview }) {
  const { t } = useTranslation();
  return (
    <Region level={3} title={t("admin.comms.templates.previewTitle")}>
      <p className="font-semibold break-words">{t("admin.comms.templates.previewSubject", { subject: preview.subject })}</p>
      <h4 className="font-semibold">{t("admin.comms.templates.previewHtml")}</h4>
      <iframe
        title={t("admin.comms.templates.previewHtml")}
        sandbox=""
        referrerPolicy="no-referrer"
        srcDoc={preview.html}
        className="h-96 w-full rounded border border-line bg-white"
      />
      <h4 className="font-semibold">{t("admin.comms.templates.previewText")}</h4>
      <pre className="overflow-x-auto rounded border border-line p-2 text-sm whitespace-pre-wrap">{preview.text}</pre>
    </Region>
  );
}

function Revisions({
  revisions,
  error,
  onLoad,
}: {
  revisions: TemplateRevision[] | undefined;
  error: boolean;
  onLoad: (r: TemplateRevision) => void;
}) {
  const { t, i18n } = useTranslation();
  return (
    <Region level={3} title={t("admin.comms.templates.revisionsTitle")}>
      {error && <p role="alert">{t("admin.comms.loadError")}</p>}
      {revisions?.length === 0 && <p>{t("admin.comms.templates.noRevisions")}</p>}
      {revisions && revisions.length > 0 && (
        <ul className="space-y-2">
          {revisions.map((r) => (
            <li key={r.id} className={CARD_ITEM}>
              <p className="font-semibold">
                {t("admin.comms.templates.revision", { version: r.version })}
                {r.is_current && <span className="ml-2 rounded border border-line px-1 text-sm">{t("admin.comms.templates.current")}</span>}
              </p>
              <p className="text-sm">{formatTime(r.created_at, i18n.language)}</p>
              <p className="break-words">{r.subject}</p>
              <button type="button" className={BUTTON} onClick={() => { onLoad(r); }}>
                {t("admin.comms.templates.load", { version: r.version })}
              </button>
            </li>
          ))}
        </ul>
      )}
    </Region>
  );
}
