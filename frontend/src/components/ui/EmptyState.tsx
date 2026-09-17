import React from 'react';
import { PackageOpen } from 'lucide-react';

export interface EmptyStateProps {
  icon?: React.ReactNode;
  title: string;
  description?: string;
  action?: React.ReactNode;
  className?: string;
}

export function EmptyState({
  icon,
  title,
  description,
  action,
  className = '',
}: EmptyStateProps) {
  return (
    <div
      className={`flex flex-col items-center justify-center rounded-lg border border-dashed border-zinc-200 bg-white/50 p-8 text-center ${className}`}
    >
      <div className="flex h-10 w-10 items-center justify-center rounded-full bg-zinc-100 text-zinc-500 mb-3">
        {icon || <PackageOpen className="w-5 h-5" />}
      </div>
      <h3 className="text-sm font-semibold text-zinc-900">{title}</h3>
      {description && <p className="mt-1 text-xs text-zinc-500 max-w-sm">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
