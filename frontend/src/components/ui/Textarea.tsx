import React, { forwardRef } from 'react';

export interface TextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string;
  error?: string;
  helperText?: string;
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className = '', label, error, helperText, id, disabled, rows = 3, ...props }, ref) => {
    const areaId = id || (label ? label.toLowerCase().replace(/\s+/g, '-') : undefined);

    return (
      <div className="w-full space-y-1">
        {label && (
          <label htmlFor={areaId} className="block text-xs font-medium text-zinc-700">
            {label}
          </label>
        )}
        <textarea
          id={areaId}
          ref={ref}
          rows={rows}
          disabled={disabled}
          className={`w-full rounded-md border bg-white p-2.5 text-sm text-zinc-900 placeholder:text-zinc-400 transition-colors focus:outline-none focus:ring-1 ${
            error
              ? 'border-rose-300 focus:border-rose-500 focus:ring-rose-500'
              : 'border-zinc-300 focus:border-zinc-900 focus:ring-zinc-900'
          } disabled:bg-zinc-50 disabled:text-zinc-500 disabled:cursor-not-allowed ${className}`}
          {...props}
        />
        {error ? (
          <p className="text-xs text-rose-600">{error}</p>
        ) : helperText ? (
          <p className="text-xs text-zinc-500">{helperText}</p>
        ) : null}
      </div>
    );
  },
);

Textarea.displayName = 'Textarea';
