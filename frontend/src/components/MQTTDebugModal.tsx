import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { X, Play, Square, Trash2, RefreshCw, ArrowDown, ArrowUp } from 'lucide-react';
import { api, type MQTTLogEntry } from '../api/client';
import { Button } from './Button';
import { useState, useEffect, useRef } from 'react';

interface MQTTDebugModalProps {
  printerId: number;
  printerName: string;
  onClose: () => void;
}

export function MQTTDebugModal({ printerId, printerName, onClose }: MQTTDebugModalProps) {
  const queryClient = useQueryClient();
  const [autoScroll, setAutoScroll] = useState(true);
  const [expandedLogs, setExpandedLogs] = useState<Set<number>>(new Set());
  const logContainerRef = useRef<HTMLDivElement>(null);

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['mqtt-logs', printerId],
    queryFn: () => api.getMQTTLogs(printerId),
    refetchInterval: 1000, // Poll every second when logging is enabled
  });

  const enableMutation = useMutation({
    mutationFn: () => api.enableMQTTLogging(printerId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['mqtt-logs', printerId] });
    },
  });

  const disableMutation = useMutation({
    mutationFn: () => api.disableMQTTLogging(printerId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['mqtt-logs', printerId] });
    },
  });

  const clearMutation = useMutation({
    mutationFn: () => api.clearMQTTLogs(printerId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['mqtt-logs', printerId] });
    },
  });

  // Auto-scroll to bottom when new logs arrive
  useEffect(() => {
    if (autoScroll && logContainerRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight;
    }
  }, [data?.logs, autoScroll]);

  const toggleExpand = (index: number) => {
    setExpandedLogs((prev) => {
      const newSet = new Set(prev);
      if (newSet.has(index)) {
        newSet.delete(index);
      } else {
        newSet.add(index);
      }
      return newSet;
    });
  };

  const formatTimestamp = (timestamp: string) => {
    const date = new Date(timestamp);
    return date.toLocaleTimeString('en-US', { hour12: false, fractionalSecondDigits: 3 });
  };

  const formatPayload = (payload: Record<string, unknown>, expanded: boolean) => {
    const json = JSON.stringify(payload, null, expanded ? 2 : 0);
    if (!expanded && json.length > 100) {
      return json.substring(0, 100) + '...';
    }
    return json;
  };

  const loggingEnabled = data?.logging_enabled ?? false;
  const logs = data?.logs ?? [];

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
      <div className="bg-bambu-dark-secondary rounded-lg max-w-4xl w-full max-h-[85vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary">
          <div>
            <h2 className="text-lg font-semibold text-white">MQTT Debug Log</h2>
            <p className="text-sm text-bambu-gray">{printerName}</p>
          </div>
          <button
            onClick={onClose}
            className="text-bambu-gray hover:text-white transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Controls */}
        <div className="flex items-center gap-2 p-4 border-b border-bambu-dark-tertiary">
          {loggingEnabled ? (
            <Button
              size="sm"
              variant="secondary"
              onClick={() => disableMutation.mutate()}
              disabled={disableMutation.isPending}
            >
              <Square className="w-4 h-4" />
              Stop
            </Button>
          ) : (
            <Button
              size="sm"
              onClick={() => enableMutation.mutate()}
              disabled={enableMutation.isPending}
            >
              <Play className="w-4 h-4" />
              Start Logging
            </Button>
          )}
          <Button
            size="sm"
            variant="secondary"
            onClick={() => clearMutation.mutate()}
            disabled={clearMutation.isPending || logs.length === 0}
          >
            <Trash2 className="w-4 h-4" />
            Clear
          </Button>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => refetch()}
            disabled={isLoading}
          >
            <RefreshCw className={`w-4 h-4 ${isLoading ? 'animate-spin' : ''}`} />
          </Button>
          <div className="flex-1" />
          <label className="flex items-center gap-2 text-sm text-bambu-gray cursor-pointer">
            <input
              type="checkbox"
              checked={autoScroll}
              onChange={(e) => setAutoScroll(e.target.checked)}
              className="rounded border-bambu-dark-tertiary"
            />
            Auto-scroll
          </label>
          <span className="text-sm text-bambu-gray">
            {logs.length} message{logs.length !== 1 ? 's' : ''}
          </span>
        </div>

        {/* Log Content */}
        <div
          ref={logContainerRef}
          className="flex-1 overflow-auto p-4 font-mono text-xs bg-bambu-dark min-h-[400px]"
        >
          {logs.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-bambu-gray">
              <p className="mb-2">No messages logged yet</p>
              {!loggingEnabled && (
                <p className="text-sm">Click "Start Logging" to begin capturing MQTT messages</p>
              )}
            </div>
          ) : (
            <div className="space-y-1">
              {logs.map((log: MQTTLogEntry, index: number) => {
                const isExpanded = expandedLogs.has(index);
                const isIncoming = log.direction === 'in';

                return (
                  <div
                    key={index}
                    className={`p-2 rounded cursor-pointer hover:bg-bambu-dark-secondary transition-colors ${
                      isExpanded ? 'bg-bambu-dark-secondary' : ''
                    }`}
                    onClick={() => toggleExpand(index)}
                  >
                    <div className="flex items-start gap-2">
                      <span className="text-bambu-gray shrink-0">
                        {formatTimestamp(log.timestamp)}
                      </span>
                      <span
                        className={`shrink-0 ${
                          isIncoming ? 'text-blue-400' : 'text-green-400'
                        }`}
                        title={isIncoming ? 'Incoming' : 'Outgoing'}
                      >
                        {isIncoming ? (
                          <ArrowDown className="w-3 h-3" />
                        ) : (
                          <ArrowUp className="w-3 h-3" />
                        )}
                      </span>
                      <span className="text-purple-400 shrink-0">{log.topic}</span>
                    </div>
                    <pre
                      className={`mt-1 text-white/80 overflow-x-auto ${
                        isExpanded ? 'whitespace-pre-wrap' : 'truncate'
                      }`}
                    >
                      {formatPayload(log.payload, isExpanded)}
                    </pre>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between p-4 border-t border-bambu-dark-tertiary">
          <div className="text-sm text-bambu-gray">
            {loggingEnabled ? (
              <span className="flex items-center gap-2">
                <span className="w-2 h-2 bg-green-500 rounded-full animate-pulse" />
                Logging active - messages will auto-refresh
              </span>
            ) : (
              <span>Logging stopped</span>
            )}
          </div>
          <Button variant="secondary" onClick={onClose}>
            Close
          </Button>
        </div>
      </div>
    </div>
  );
}
