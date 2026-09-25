import React from 'react';
import { AlertCircle, CheckCircle2, Info, AlertTriangle } from 'lucide-react';

export type AlertVariant = 'info' | 'success' | 'warning' | 'danger';

export interface AlertProps extends React.HTMLAttributes<HTMLDivElement> {
  variant?: AlertVariant;
  title?: string;
}

const variantConfig: Record<
  AlertVariant,
  { bg: string; border: string; text: string; icon: React.ComponentType<{ className?: string }> }
> = {
  info: {
    bg: 'bg-sky-50 dark:bg-sky-500/10',
    border: 'border-sky-200 dark:border-sky-500/30',
    text: 'text-sky-800 dark:text-sky-200',
    icon: Info,
  },
  success: {
    bg: 'bg-emerald-50 dark:bg-emerald-500/10',
    border: 'border-emerald-200 dark:border-emerald-500/30',
    text: 'text-emerald-800 dark:text-emerald-200',
    icon: CheckCircle2,
  },
  warning: {
    bg: 'bg-amber-50 dark:bg-amber-500/10',
    border: 'border-amber-200 dark:border-amber-500/30',
    text: 'text-amber-800 dark:text-amber-200',
    icon: AlertTriangle,
  },
  danger: {
    bg: 'bg-rose-50 dark:bg-rose-500/10',
    border: 'border-rose-200 dark:border-rose-500/30',
    text: 'text-rose-800 dark:text-rose-200',
    icon: AlertCircle,
  },
};

export function Alert({ variant = 'info', title, children, className = '', ...props }: AlertProps) {
  const config = variantConfig[variant];
  const IconComponent = config.icon;

  return (
    <div
      role="alert"
      className={`flex items-start gap-3 rounded-lg border p-3.5 text-xs ${config.bg} ${config.border} ${config.text} ${className}`}
      {...props}
    >
      <IconComponent className="w-4 h-4 shrink-0 mt-0.5" />
      <div className="flex-1 space-y-0.5">
        {title && <h5 className="font-semibold">{title}</h5>}
        <div className="leading-relaxed">{children}</div>
      </div>
    </div>
  );
}
