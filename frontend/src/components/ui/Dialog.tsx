import React, { useEffect } from 'react';
import { X } from 'lucide-react';

export interface DialogProps {
  open: boolean;
  onClose: () => void;
  title?: string;
  description?: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
  maxWidth?: 'sm' | 'md' | 'lg' | 'xl';
}

function getWidthClass(maxWidth: string = 'md') {
  if (maxWidth === 'sm') return 'max-w-sm';
  if (maxWidth === 'lg') return 'max-w-lg';
  if (maxWidth === 'xl') return 'max-w-xl';
  return 'max-w-md';
}

export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  maxWidth = 'md',
}: DialogProps) {
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && open) {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        className="fixed inset-0 bg-zinc-950/40 backdrop-blur-xs"
        onClick={onClose}
        aria-hidden="true"
      />

      <div
        role="dialog"
        aria-modal="true"
        className={`relative w-full ${getWidthClass(maxWidth)} rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-6 shadow-xl transition-all z-10`}
      >
        <button
          type="button"
          onClick={onClose}
          className="absolute right-4 top-4 rounded-md p-1 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200 focus:outline-none focus:ring-2 focus:ring-zinc-400"
          aria-label="Close"
        >
          <X className="w-4 h-4" />
        </button>

        {(title || description) && (
          <div className="mb-4 space-y-1">
            {title && <h2 className="text-base font-semibold text-zinc-900 dark:text-zinc-100">{title}</h2>}
            {description && <p className="text-xs text-zinc-500 dark:text-zinc-400">{description}</p>}
          </div>
        )}

        <div className="py-2 text-sm text-zinc-800 dark:text-zinc-200">{children}</div>

        {footer && (
          <div className="mt-5 flex items-center justify-end gap-2 border-t border-zinc-100 dark:border-zinc-800 pt-4">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}
