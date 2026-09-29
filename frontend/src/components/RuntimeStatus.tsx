import { ShieldCheck, ShieldAlert } from "lucide-react";
import { Badge, Dot } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip } from "@/components/ui/tooltip";
import { useHealth } from "@/lib/queries";
import { strings } from "@/lib/strings";

const s = strings.onboarding;

/** "Local only" pill: green while the backend has seen no external connection attempts. */
export function LocalOnlyBadge() {
  const { data } = useHealth();
  if (!data) return null;
  const ok = data.privacy.local_only;
  return (
    <Tooltip label={ok ? strings.settings.localOnlyOn : strings.settings.localOnlyOff}>
      <Badge variant={ok ? "success" : "warning"} role="status" tabIndex={0}>
        {ok ? <ShieldCheck /> : <ShieldAlert />}
        {strings.settings.localOnly}
      </Badge>
    </Tooltip>
  );
}

function Row({
  label,
  ok,
  pending,
  detail,
}: {
  label: string;
  ok: boolean;
  pending?: boolean;
  detail?: string | null;
}) {
  return (
    <div className="flex items-center justify-between gap-3 py-2">
      <div className="min-w-0">
        <div className="text-[13px] font-medium">{label}</div>
        {detail && <div className="truncate text-xs text-muted">{detail}</div>}
      </div>
      <Badge variant={ok ? "success" : pending ? "neutral" : "warning"}>
        <Dot />
        {ok ? s.ready : pending ? s.starting : s.notReady}
      </Badge>
    </div>
  );
}

/** Foundry Local, chat model and embedding readiness, shown on the onboarding screen. */
export function RuntimeStatus() {
  const { data, isLoading } = useHealth();
  if (isLoading || !data) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-9" />
        <Skeleton className="h-9" />
        <Skeleton className="h-9" />
      </div>
    );
  }
  const starting = !data.foundry.running && !data.foundry.error;
  return (
    <div className="divide-y divide-border">
      <Row
        label={s.foundry}
        ok={data.foundry.running}
        pending={starting}
        detail={data.foundry.error ?? data.foundry.endpoint}
      />
      <Row
        label={s.chatModel}
        ok={data.chat_model.ready}
        pending={starting}
        detail={data.chat_model.alias}
      />
      <Row
        label={s.embeddings}
        ok={data.embedder.loaded}
        pending={!data.embedder.error && !data.embedder.loaded}
        detail={data.embedder.error ?? data.embedder.name}
      />
    </div>
  );
}
