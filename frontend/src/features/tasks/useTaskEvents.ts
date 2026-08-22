import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

const taskEventNames = [
  "image.started",
  "image.succeeded",
  "image.failed",
  "task.updated",
  "report.pending",
  "report.running",
  "report.succeeded",
  "report.failed",
] as const;

export function useTaskEvents(taskId: string) {
  const queryClient = useQueryClient();

  useEffect(() => {
    let closed = false;
    let lastEventId = "";
    const source = new EventSource(`/api/tasks/${taskId}/events`);

    const refetch = (event?: Event) => {
      if (event instanceof MessageEvent && event.lastEventId) lastEventId = event.lastEventId;
      void queryClient.invalidateQueries({ queryKey: ["task", taskId] });
    };

    for (const eventName of taskEventNames) {
      source.addEventListener(eventName, refetch as EventListener);
    }
    source.onmessage = refetch;
    source.onerror = () => {
      if (!closed) void queryClient.invalidateQueries({ queryKey: ["task", taskId] });
    };

    return () => {
      closed = true;
      void lastEventId;
      source.close();
    };
  }, [queryClient, taskId]);
}
