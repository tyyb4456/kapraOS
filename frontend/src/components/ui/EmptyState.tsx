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
      className={`clay-panel-soft flex flex-col items-center justify-center border-dashed p-8 text-center ${className}`}
    >
      <div className="clay-icon-tile h-10 w-10 text-zinc-500 dark:text-zinc-400 mb-3 rounded-full">
        {icon || <PackageOpen className="w-5 h-5" />}
      </div>
      <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">{title}</h3>
      {description && <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400 max-w-sm">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
