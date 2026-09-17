import React, { forwardRef } from 'react';

export interface SelectOption {
  value: string | number;
  label: string;
}

export interface SelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
  label?: string;
  error?: string;
  helperText?: string;
  options?: SelectOption[];
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(
  ({ className = '', label, error, helperText, options, id, children, disabled, ...props}, ref) => {
    const selectId = id || (label ? label.toLowerCase().replace(/\\s+/g, '-') : undefined);

    return (
      <div className="w-full space-y-1">
        {label && (
          <label htmlFor={selectId} className="block text-xs font-medium text-zinc-700">
            {label}
          </label>
        )}
        <select
          id={selectId}
          ref={ref}
          disabled={disabled}
          className={`w-full h-9 rounded-md border bg-white px-3 py-1 text-sm text-zinc-900 transition-colors focus:outline-none focus:ring-1 ${
            error
              ? 'border-rose-300 focus:border-rose-500 focus:ring-rose-500'
              : 'border-zinc-300 focus:border-zinc-900 focus:ring-zinc-900'
          } disabled:bg-zinc-50 disabled:text-zinc-500 disabled:cursor-not-allowed ${className}`}
          {...props}
        >
          {options
            ? options.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))
            : children}
        </select>
        {error ? (
          <p className="text-xs text-rose-600">{error}</p>
        ) : helperText ? (
          <p className="text-xs text-zinc-500">{helperText}</p>
        ) : null}
      </div>
    );
  },
);

Select.displayName = 'Select';
