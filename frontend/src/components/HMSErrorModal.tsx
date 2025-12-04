// HMS Error Modal - Updated Dec 4 2025 for attr field support
import { useEffect } from 'react';
import { X, AlertTriangle, AlertCircle, Info, ExternalLink } from 'lucide-react';
import type { HMSError } from '../api/client';

interface HMSErrorModalProps {
  printerName: string;
  errors: HMSError[];
  onClose: () => void;
}

// HMS error code descriptions keyed by full HMS code (attr + code combined)
// Format: "AAAA_BBBB_CCCC_DDDD" where AAAA_BBBB is from attr, CCCC_DDDD is from code
const HMS_DESCRIPTIONS: Record<string, string> = {
  // H2D specific errors
  '0700_5500_0002_0001': 'A binding error occurred between AMS and the extruder. Please perform AMS initialization again.',
  '0500_0300_0002_000E': 'Some modules are incompatible with the printer firmware version. Please update firmware.',
  // Common errors
  '0300_0100_0002_0054': 'The heatbed temperature is abnormal. The sensor may be disconnected or damaged.',
  '0500_0100_0005_0005': 'Motor driver overheated. Let the printer cool down.',
  '0500_0100_0005_0006': 'Motor driver communication error.',
  '0700_0100_0007_0001': 'AMS communication error.',
  '0700_0100_0007_0002': 'AMS filament runout.',
  '0700_0100_0007_0003': 'AMS filament not detected.',
  '0C00_0100_000C_0003': 'First layer inspection failed.',
  '0C00_0100_000C_0004': 'Nozzle clog detected.',
  '0C00_0100_000C_8000': 'Foreign object detected on print bed.',
  '0500_0100_0005_0000': 'Motor X axis lost steps.',
  '0500_0100_0005_0001': 'Motor Y axis lost steps.',
  '0500_0100_0005_0002': 'Motor Z axis lost steps.',
};

function getSeverityInfo(severity: number): { label: string; color: string; bgColor: string; Icon: typeof AlertTriangle } {
  switch (severity) {
    case 1:
      return { label: 'Fatal', color: 'text-red-500', bgColor: 'bg-red-500/20', Icon: AlertTriangle };
    case 2:
      return { label: 'Serious', color: 'text-red-400', bgColor: 'bg-red-500/15', Icon: AlertTriangle };
    case 3:
      return { label: 'Warning', color: 'text-orange-400', bgColor: 'bg-orange-500/20', Icon: AlertCircle };
    case 4:
    default:
      return { label: 'Info', color: 'text-blue-400', bgColor: 'bg-blue-500/20', Icon: Info };
  }
}

function getFullHMSCode(attr: number, code: number): string {
  // Construct the full HMS code from attr and code
  // Format: AAAA_BBBB_CCCC_DDDD
  // AAAA_BBBB from attr, CCCC_DDDD from code
  const a1 = ((attr >> 24) & 0xFF).toString(16).padStart(2, '0').toUpperCase();
  const a2 = ((attr >> 16) & 0xFF).toString(16).padStart(2, '0').toUpperCase();
  const a3 = ((attr >> 8) & 0xFF).toString(16).padStart(2, '0').toUpperCase();
  const a4 = (attr & 0xFF).toString(16).padStart(2, '0').toUpperCase();

  const c1 = ((code >> 24) & 0xFF).toString(16).padStart(2, '0').toUpperCase();
  const c2 = ((code >> 16) & 0xFF).toString(16).padStart(2, '0').toUpperCase();
  const c3 = ((code >> 8) & 0xFF).toString(16).padStart(2, '0').toUpperCase();
  const c4 = (code & 0xFF).toString(16).padStart(2, '0').toUpperCase();

  return `${a1}${a2}_${a3}${a4}_${c1}${c2}_${c3}${c4}`;
}

function getHMSWikiUrl(attr: number, code: number, printerName: string): string {
  // Construct wiki URL from attr and code
  const fullCode = getFullHMSCode(attr, code);

  // Use H2 wiki path for H2D printers, otherwise use X1 path
  const isH2 = printerName.toLowerCase().includes('h2');
  const basePath = isH2 ? 'h2' : 'x1';

  return `https://wiki.bambulab.com/en/${basePath}/troubleshooting/hmscode/${fullCode}`;
}

export function HMSErrorModal({ printerName, errors, onClose }: HMSErrorModalProps) {
  // Debug: log errors to see what data we're receiving
  console.log('HMSErrorModal errors:', JSON.stringify(errors, null, 2));

  // Close on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-bambu-dark-secondary rounded-lg shadow-xl max-w-lg w-full max-h-[80vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary">
          <div className="flex items-center gap-2">
            <AlertTriangle className="w-5 h-5 text-orange-400" />
            <h2 className="text-lg font-semibold text-white">HMS Errors - {printerName}</h2>
          </div>
          <button
            onClick={onClose}
            className="p-1 hover:bg-bambu-dark-tertiary rounded-lg transition-colors"
          >
            <X className="w-5 h-5 text-bambu-gray" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-4">
          {errors.length === 0 ? (
            <div className="text-center py-8 text-bambu-gray">
              <AlertCircle className="w-12 h-12 mx-auto mb-3 opacity-30" />
              <p>No HMS errors</p>
            </div>
          ) : (
            <div className="space-y-3">
              {errors.map((error, index) => {
                const { label, color, bgColor, Icon } = getSeverityInfo(error.severity);
                const codeNum = parseInt(error.code.replace('0x', ''), 16) || 0;
                const fullHMSCode = getFullHMSCode(error.attr, codeNum);
                const description = HMS_DESCRIPTIONS[fullHMSCode] || 'Unknown error. Click the link below for details.';
                const wikiUrl = getHMSWikiUrl(error.attr, codeNum, printerName);
                const displayCode = `HMS_${fullHMSCode.replace(/_/g, '-')}`;

                return (
                  <div
                    key={`${error.code}-${index}`}
                    className={`p-4 rounded-lg ${bgColor} border border-white/10`}
                  >
                    <div className="flex items-start gap-3">
                      <Icon className={`w-5 h-5 ${color} flex-shrink-0 mt-0.5`} />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1">
                          <span className={`font-mono text-sm ${color}`}>{displayCode}</span>
                          <span className={`text-xs px-2 py-0.5 rounded-full ${bgColor} ${color}`}>
                            {label}
                          </span>
                        </div>
                        <p className="text-sm text-bambu-gray mb-2">{description}</p>
                        <a
                          href={wikiUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1 text-xs text-bambu-green hover:underline"
                        >
                          <ExternalLink className="w-3 h-3" />
                          View on Bambu Lab Wiki
                        </a>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="p-4 border-t border-bambu-dark-tertiary">
          <p className="text-xs text-bambu-gray">
            HMS (Health Management System) monitors printer health. Clear errors on the printer to dismiss them here.
          </p>
        </div>
      </div>
    </div>
  );
}
