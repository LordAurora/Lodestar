import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Cpu,
  Palette,
  Shield,
  SlidersHorizontal,
  Download,
  Loader2,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";
import { useState } from "react";
import { ThemeSegmented } from "@/components/ThemeToggle";
import { Badge, Dot } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectItem } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { toast } from "@/components/ui/toaster";
import { api, type ModelInfo, type Settings } from "@/lib/api";
import { useAppState } from "@/lib/app-state";
import { keys, useHealth, useModels, useSettings } from "@/lib/queries";
import { strings } from "@/lib/strings";

const s = strings.settings;

function Field({
  label,
  help,
  htmlFor,
  value,
  children,
}: {
  label: string;
  help?: string;
  htmlFor?: string;
  value?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2 py-4 first:pt-0 last:pb-0">
      <div className="flex items-baseline justify-between gap-4">
        <label htmlFor={htmlFor} className="text-[13px] font-medium">
          {label}
        </label>
        {value && <span className="text-[13px] text-muted tabular-nums">{value}</span>}
      </div>
      {children}
      {help && <p className="text-xs text-muted">{help}</p>}
    </div>
  );
}

function ModelStatus({ model }: { model: ModelInfo }) {
  if (model.job?.state === "downloading")
    return <Badge variant="accent">{s.downloading(model.job.progress)}</Badge>;
  if (model.job?.state === "loading")
    return (
      <Badge variant="accent">
        <Loader2 className="animate-spin" />
        {s.loading}
      </Badge>
    );
  if (model.job?.state === "error") return <Badge variant="danger">{model.job.error}</Badge>;
  if (model.loaded)
    return (
      <span className="flex items-center gap-2">
        <Badge variant="success">
          <Dot />
          {s.loaded}
        </Badge>
        <Badge title={s.device(model.device)}>{model.device}</Badge>
      </span>
    );
  if (model.cached) return <Badge>{s.cached}</Badge>;
  return <Badge variant="warning">{s.notDownloaded}</Badge>;
}

function ModelCard({
  settings,
  save,
}: {
  settings: Settings;
  save: (c: Partial<Settings>) => void;
}) {
  const { data, error, isLoading } = useModels();
  const qc = useQueryClient();
  const load = useMutation({
    mutationFn: (alias: string) => api.loadModel(alias),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.models }),
    onError: (e) => toast.error((e as Error).message),
  });
  const selected = data?.models.find((m) => m.alias === settings.chat_model);

  return (
    <Card>
      <CardHeader>
        <CardTitle icon={Cpu}>{s.model}</CardTitle>
        <CardDescription>{s.modelHelp}</CardDescription>
      </CardHeader>
      <CardContent className="divide-y divide-border">
        <Field label={s.chatModel}>
          {isLoading ? (
            <Skeleton className="h-9" />
          ) : error ? (
            <p className="text-[13px] text-danger">{(error as Error).message}</p>
          ) : (
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
              <Select
                label={s.chatModel}
                value={settings.chat_model}
                onValueChange={(v) => save({ chat_model: v })}
                className="sm:max-w-xs"
              >
                {data!.models.map((m) => (
                  <SelectItem key={m.alias} value={m.alias}>
                    {m.alias}
                    {m.size_mb ? ` · ${(m.size_mb / 1024).toFixed(1)} GB` : ""}
                    {m.cached ? "" : strings.common.notDownloadedSuffix}
                  </SelectItem>
                ))}
              </Select>
              {selected && (
                <div className="flex items-center gap-2">
                  <ModelStatus model={selected} />
                  {!selected.loaded && !selected.job && (
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => load.mutate(selected.alias)}
                    >
                      {!selected.cached && <Download />}
                      {selected.cached ? s.load : s.download}
                    </Button>
                  )}
                </div>
              )}
            </div>
          )}
        </Field>
        <Field label={s.embeddingModel} help={s.embeddingHelp}>
          <div className="rounded-md border border-border bg-surface px-3 py-2 font-mono text-[13px]">
            {settings.embedding_model}
          </div>
        </Field>
      </CardContent>
    </Card>
  );
}

function RetrievalCard({
  settings,
  save,
}: {
  settings: Settings;
  save: (c: Partial<Settings>) => void;
}) {
  // Sliders update locally while dragging and save when released.
  const [topK, setTopK] = useState(settings.top_k);
  const [threshold, setThreshold] = useState(settings.relevance_threshold);

  return (
    <Card>
      <CardHeader>
        <CardTitle icon={SlidersHorizontal}>{s.retrieval}</CardTitle>
        <CardDescription>{s.retrievalHelp}</CardDescription>
      </CardHeader>
      <CardContent className="divide-y divide-border">
        <Field label={s.topK} value={String(topK)}>
          <Slider
            label={s.topK}
            value={topK}
            min={1}
            max={12}
            step={1}
            onValueChange={setTopK}
            onValueCommit={(v) => save({ top_k: v })}
          />
        </Field>
        <Field label={s.threshold} value={threshold.toFixed(2)} help={s.thresholdHelp}>
          <Slider
            label={s.threshold}
            value={threshold}
            min={0}
            max={0.9}
            step={0.01}
            onValueChange={setThreshold}
            onValueCommit={(v) => save({ relevance_threshold: Number(v.toFixed(2)) })}
          />
        </Field>
        <div className="flex items-center justify-between gap-4 pt-4">
          <div>
            <label htmlFor="hybrid" className="text-[13px] font-medium">
              {s.hybrid}
            </label>
            <p className="text-xs text-muted">{s.hybridHelp}</p>
          </div>
          <Switch
            id="hybrid"
            checked={settings.hybrid_search}
            onCheckedChange={(v) => save({ hybrid_search: v })}
          />
        </div>
      </CardContent>
    </Card>
  );
}

function PrivacyCard() {
  const { data } = useHealth();
  const ok = data?.privacy.local_only ?? true;
  return (
    <Card>
      <CardHeader>
        <CardTitle icon={Shield}>{s.privacy}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div
          className={
            ok
              ? "flex items-center gap-3 rounded-md bg-success-soft p-3 text-success"
              : "flex items-center gap-3 rounded-md bg-warning-soft p-3 text-warning"
          }
        >
          {ok ? (
            <ShieldCheck className="size-5 shrink-0" />
          ) : (
            <ShieldAlert className="size-5 shrink-0" />
          )}
          <div>
            <div className="text-[13px] font-semibold">{s.localOnly}</div>
            <div className="text-xs">{ok ? s.localOnlyOn : s.localOnlyOff}</div>
          </div>
        </div>
        <p className="text-[13px] text-muted">{s.privacyBody}</p>
        <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-[13px]">
          <dt className="text-muted">{s.endpoint}</dt>
          <dd className="truncate font-mono">{data?.foundry.endpoint ?? s.foundryDown}</dd>
          <dt className="text-muted">{s.blocked}</dt>
          <dd className="tabular-nums">{data?.privacy.external_attempts ?? 0}</dd>
          <dt className="text-muted">{s.version}</dt>
          <dd>0.1.0</dd>
        </dl>
      </CardContent>
    </Card>
  );
}

export function SettingsPage() {
  const { setView } = useAppState();
  const { data: settings } = useSettings();
  const qc = useQueryClient();
  const mutation = useMutation({
    mutationFn: api.saveSettings,
    onSuccess: (data) => {
      qc.setQueryData(keys.settings, data);
      qc.invalidateQueries({ queryKey: keys.health });
      toast.success(s.saved);
    },
    onError: (e) => toast.error((e as Error).message),
  });

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto flex w-full max-w-2xl flex-col gap-6 px-6 py-10">
        <div>
          <Button variant="subtle" size="sm" className="-ml-3" onClick={() => setView("chat")}>
            <ArrowLeft />
            {s.back}
          </Button>
          <h1 className="mt-3 text-2xl font-semibold tracking-tight">{s.title}</h1>
          <p className="mt-1 text-muted">{s.subtitle}</p>
        </div>
        {settings ? (
          <>
            <ModelCard settings={settings} save={mutation.mutate} />
            <RetrievalCard
              key={`${settings.top_k}:${settings.relevance_threshold}`}
              settings={settings}
              save={mutation.mutate}
            />
          </>
        ) : (
          <Skeleton className="h-64" />
        )}
        <Card>
          <CardHeader>
            <CardTitle icon={Palette}>{s.appearance}</CardTitle>
            <CardDescription>{s.appearanceHelp}</CardDescription>
          </CardHeader>
          <CardContent>
            <ThemeSegmented size="md" />
          </CardContent>
        </Card>
        <PrivacyCard />
      </div>
    </div>
  );
}
