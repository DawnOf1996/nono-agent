import { useChatStore } from "@/store/chat";

export function Trajectory() {
  const events = useChatStore((state) => state.events);

  if (events.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center px-8 text-sm text-muted">
        还没有轨迹。发一条消息后，事件会列在这里。
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-auto px-8 py-6">
      <ol className="mx-auto max-w-3xl space-y-2">
        {events.map((event) => (
          <li key={event.seq} className="rounded-xl bg-card px-3 py-2">
            <div className="flex items-center justify-between gap-3">
              <span className="font-mono text-[12px] text-ink">{event.type}</span>
              <span className="font-mono text-[11px] text-faint">#{event.seq}</span>
            </div>
            {Object.keys(event.data).length ? (
              <pre className="mt-1 max-h-40 overflow-auto font-mono text-[11px] leading-5 text-muted">
                {JSON.stringify(event.data, null, 2)}
              </pre>
            ) : null}
          </li>
        ))}
      </ol>
    </div>
  );
}
